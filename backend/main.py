"""levabici backend — serve o app estático e mantém o grafo de avaliações.

Mesmo desenho do amora/backend: um único serviço Flask que roda igual num
host local e no Cloud Run — STORAGE_BACKEND escolhe onde vive o estado
(filesystem ou bucket GCS). Sem SQLite: o estado é UM arquivo Turtle
(reviews.ttl = empresas + avaliações), lido e reescrito inteiro a cada
mutação, sob lock de processo (premissa: gunicorn --workers 1 e Cloud Run
com no máximo 1 instância).

Moderação estilo wiki: qualquer pessoa cria, edita e apaga avaliações
(sem auth, por desenho — como todo o ecossistema). A proteção é o
HISTÓRICO: o bucket GCS tem versionamento de objetos ligado, então toda
escrita vira uma geração recuperável (ver backend/README.md). O portão de
qualidade é o SHACL: mutações que introduzam sh:Violation são rejeitadas
com 422; Warnings/Infos passam (mesma semântica do formulário).

Rotas:
  GET  /                      → index.html (o app)
  GET  /<path>                → estáticos do repo (app.js, lib/, …)
  GET  /health                → "ok" + backend de storage
  GET  /data/reviews.ttl      → o grafo vivo (bucket-first; semente do
                                container no primeiro boot)
  GET  /api/graph             → idem (alias)
  POST /api/reviews           → cria 1 avaliação (payload text/turtle)
  PUT  /api/reviews/<slug>    → substitui a subárvore da avaliação
  DELETE /api/reviews/<slug>  → remove a subárvore da avaliação
"""

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from storage import make_store_from_env

# ---------------------------------------------------------------- paths

# LEVABICI_WEB: raiz dos estáticos (no container: /app/web; local: o repo)
WEB = Path(os.environ.get("LEVABICI_WEB", Path(__file__).resolve().parent.parent))
SEED_PATH = WEB / "data" / "reviews.ttl"
SHAPES_PATH = WEB / "data" / "shapes.ttl"
VOCAB_PATH = WEB / "data" / "vocab.ttl"

GRAPH_KEY = "reviews.ttl"  # key no StateStore

LB = "https://id.pedalhidrografi.co/levabici/terms#"
AV = "https://id.pedalhidrografi.co/levabici/avaliacao/"
EMP = "https://id.pedalhidrografi.co/levabici/empresa/"

MAX_PAYLOAD = 8 * 1024 * 1024  # fotos viram data: URIs dentro do Turtle
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Diretórios do repo que nunca são servidos como estático.
BLOCKED_PREFIXES = ("backend/", "tools/", "local-state/", ".git")

app = Flask(__name__)
store = make_store_from_env(
    default_local_root=os.environ.get("LEVABICI_STATE", str(WEB / "local-state"))
)

_state_lock = threading.RLock()  # todo read-modify-write do grafo
_validate_lock = threading.Lock()  # pyshacl não é thread-safe (parser SPARQL)

# ---------------------------------------------------------------- RDF lazy

_rdf_cache = None


def _rdf():
    """rdflib/pyshacl são pesados — importa e carrega shapes/vocab uma vez."""
    global _rdf_cache
    if _rdf_cache is None:
        import pyshacl
        import rdflib

        shapes = rdflib.Graph()
        shapes.parse(SHAPES_PATH, format="turtle")
        vocab = rdflib.Graph()
        vocab.parse(VOCAB_PATH, format="turtle")
        _rdf_cache = {"rdflib": rdflib, "pyshacl": pyshacl,
                      "shapes": shapes, "vocab": vocab}
    return _rdf_cache


def _parse_graph(text):
    rdflib = _rdf()["rdflib"]
    g = rdflib.Graph()
    g.parse(data=text, format="turtle")
    return g


def _serialize_graph(g):
    rdflib = _rdf()["rdflib"]
    g.bind("lb", LB)
    g.bind("emp", EMP)
    g.bind("av", AV)
    g.bind("schema", "https://schema.org/")
    g.bind("prov", "http://www.w3.org/ns/prov#")
    g.bind("dcterms", "http://purl.org/dc/terms/")
    g.bind("xsd", "http://www.w3.org/2001/XMLSchema#")
    return g.serialize(format="turtle")


def _violations(data_graph):
    """Valida contra as shapes; retorna a lista de mensagens sh:Violation
    (Warnings/Infos passam — mesma semântica do formulário)."""
    r = _rdf()
    rdflib = r["rdflib"]
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    merged = data_graph + r["vocab"]
    with _validate_lock:
        _, results, _ = r["pyshacl"].validate(
            merged, shacl_graph=r["shapes"], advanced=True
        )
    out = []
    for res in results.subjects(rdflib.RDF.type, SH.ValidationResult):
        if results.value(res, SH.resultSeverity) == SH.Violation:
            focus = results.value(res, SH.focusNode)
            msg = results.value(res, SH.resultMessage) or "violação SHACL"
            out.append(f"{focus}: {msg}")
    return out


# ---------------------------------------------------------------- grafo

def _graph_text():
    """Grafo vivo do store; no primeiro acesso semeia com o do container."""
    with _state_lock:
        text = store.read_text(GRAPH_KEY)
        if text is None:
            text = SEED_PATH.read_text(encoding="utf-8")
            store.write_text(GRAPH_KEY, text)
        return text


def _subtree(g, review_iri):
    """Quads da avaliação + filhos determinísticos (<iri>_rating, _trip…)."""
    prefix = str(review_iri) + "_"
    return [
        t for t in g
        if str(t[0]) == str(review_iri) or str(t[0]).startswith(prefix)
    ]


def _review_iri(g, rdflib):
    """O único lb:Review do payload (400 se zero ou vários)."""
    reviews = list(g.subjects(rdflib.RDF.type, rdflib.URIRef(LB + "Review")))
    if len(reviews) != 1:
        abort(400, "o payload precisa conter exatamente uma lb:Review")
    iri = str(reviews[0])
    if not iri.startswith(AV):
        abort(400, f"IRI da avaliação precisa começar com {AV}")
    return reviews[0]


def _check_subjects(g, review_iri):
    """Todo sujeito do payload é a avaliação, um filho dela ou uma empresa."""
    prefix = str(review_iri) + "_"
    for s in set(t[0] for t in g):
        s = str(s)
        if s == str(review_iri) or s.startswith(prefix) or s.startswith(EMP):
            continue
        abort(400, f"sujeito fora do escopo da avaliação: {s}")


def _parse_payload():
    if request.content_length and request.content_length > MAX_PAYLOAD:
        abort(413, "payload grande demais (limite 8 MB — menos fotos?)")
    text = request.get_data(as_text=True)
    if not text.strip():
        abort(400, "payload vazio; esperava text/turtle")
    try:
        return _parse_graph(text)
    except Exception as e:  # noqa: BLE001 — erro de sintaxe do cliente
        abort(400, f"Turtle inválido: {e}")


def _stamp(g, subject, predicate_iri, rdflib):
    """Substitui/insere um literal xsd:dateTime de auditoria."""
    pred = rdflib.URIRef(predicate_iri)
    g.remove((subject, pred, None))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    g.add((subject, pred, rdflib.Literal(
        now, datatype=rdflib.URIRef("http://www.w3.org/2001/XMLSchema#dateTime"))))


def _mutate(build_candidate):
    """Read-modify-write serializado: candidato → SHACL → grava."""
    rdflib = _rdf()["rdflib"]
    with _state_lock:
        current = _parse_graph(_graph_text())
        candidate, response = build_candidate(current, rdflib)
        problems = _violations(candidate)
        if problems:
            return jsonify({"error": "o grafo resultante viola as shapes",
                            "violations": problems}), 422
        store.write_text(GRAPH_KEY, _serialize_graph(candidate))
        return response


# ---------------------------------------------------------------- rotas API

@app.get("/health")
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "storage": type(store).__name__})


@app.get("/data/reviews.ttl")
@app.get("/api/graph")
def get_graph():
    return Response(_graph_text(), mimetype="text/turtle; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@app.post("/api/reviews")
def create_review():
    payload = _parse_payload()

    def build(current, rdflib):
        iri = _review_iri(payload, rdflib)
        _check_subjects(payload, iri)
        slug = str(iri)[len(AV):]
        if not SLUG_RE.match(slug):
            abort(400, f"slug inválido: {slug}")
        if (iri, rdflib.RDF.type, None) in current:
            abort(409, f"avaliação já existe: {slug} (use PUT para editar)")
        if (iri, rdflib.URIRef("http://www.w3.org/ns/prov#generatedAtTime"),
                None) not in payload:
            _stamp(payload, iri, "http://www.w3.org/ns/prov#generatedAtTime",
                   rdflib)
        candidate = current + payload
        return candidate, (jsonify({"iri": str(iri), "slug": slug}), 201)

    return _mutate(build)


@app.put("/api/reviews/<slug>")
def update_review(slug):
    if not SLUG_RE.match(slug):
        abort(400, "slug inválido")
    payload = _parse_payload()

    def build(current, rdflib):
        iri = _review_iri(payload, rdflib)
        if str(iri) != AV + slug:
            abort(400, "IRI do payload difere do slug da URL")
        _check_subjects(payload, iri)
        if (iri, rdflib.RDF.type, None) not in current:
            abort(404, f"avaliação não existe: {slug}")
        _stamp(payload, iri, "http://purl.org/dc/terms/modified", rdflib)
        candidate = _rdf()["rdflib"].Graph()
        old = set(_subtree(current, iri))
        for t in current:
            if t not in old:
                candidate.add(t)
        for t in payload:
            candidate.add(t)
        return candidate, jsonify({"iri": str(iri), "slug": slug})

    return _mutate(build)


@app.delete("/api/reviews/<slug>")
def delete_review(slug):
    if not SLUG_RE.match(slug):
        abort(400, "slug inválido")

    def build(current, rdflib):
        iri = rdflib.URIRef(AV + slug)
        if (iri, rdflib.RDF.type, None) not in current:
            abort(404, f"avaliação não existe: {slug}")
        # Empresas ficam mesmo sem avaliações (páginas-vazias de wiki):
        # somem do ranking, continuam no seletor do formulário.
        candidate = rdflib.Graph()
        old = set(_subtree(current, iri))
        for t in current:
            if t not in old:
                candidate.add(t)
        return candidate, jsonify({"deleted": slug})

    return _mutate(build)


# CORS liberado só na API (dados públicos, sem auth por desenho) — deixa
# um espelho estático (GitHub Pages) apontar pra cá no futuro.
@app.after_request
def cors(resp):
    if request.path.startswith("/api/") or request.path == "/data/reviews.ttl":
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/<path:_>", methods=["OPTIONS"])
def cors_preflight(_):
    return "", 204


# ---------------------------------------------------------------- estáticos

@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/<path:path>")
def static_files(path):
    if any(path.startswith(p) for p in BLOCKED_PREFIXES):
        abort(404)
    return send_from_directory(WEB, path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8613)), debug=True)
