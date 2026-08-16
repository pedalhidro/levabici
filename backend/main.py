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
  POST /api/photos            → corpo = bytes da imagem; grava
                                uploads/<sha256>.<ext> no store
                                (endereçamento por conteúdo) e devolve a
                                URL ABSOLUTA que vai no grafo
  GET  /uploads/<nome>        → serve o blob (imutável: cache eterno)
"""

import hashlib
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

MAX_PAYLOAD = 8 * 1024 * 1024
MAX_PHOTO = 4 * 1024 * 1024  # o app encolhe pra ~250 KB; margem folgada
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
UPLOAD_NAME_RE = re.compile(r"^[a-f0-9]{64}\.(jpg|png|webp|gif)$")

# Base ABSOLUTA das URLs de foto gravadas no grafo (decisão: IRIs
# dereferenciáveis, casadas com o domínio público). Local/dev cai no
# url_root da requisição.
PUBLIC_BASE = os.environ.get("LEVABICI_PUBLIC_BASE", "").rstrip("/")

# assinaturas de formato aceitas (sniff leve, sem dependência de imagem)
PHOTO_MAGIC = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)

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


# ---------------------------------------------------------------- fotos

def _sniff_photo(data):
    for magic, ext, ct in PHOTO_MAGIC:
        if data.startswith(magic):
            return ext, ct
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None, None


@app.post("/api/photos")
def upload_photo():
    """Corpo = bytes crus da imagem. Grava uploads/<sha256>.<ext> no
    store (endereçado por conteúdo: idempotente, dedupe de graça) e
    devolve a URL absoluta que o app põe no grafo (schema:image)."""
    data = request.get_data(cache=False)
    if not data:
        abort(400, "corpo vazio")
    if len(data) > MAX_PHOTO:
        abort(413, "foto grande demais (máx. 4 MB)")
    ext, ct = _sniff_photo(data)
    if not ext:
        abort(415, "formato não reconhecido (jpg/png/webp/gif)")
    name = hashlib.sha256(data).hexdigest() + "." + ext
    key = "uploads/" + name
    if not store.exists(key):
        store.write_bytes(key, data, content_type=ct)
    base = PUBLIC_BASE or request.url_root.rstrip("/")
    return jsonify({"url": f"{base}/uploads/{name}"}), 201


@app.get("/uploads/<name>")
def serve_upload(name):
    if not UPLOAD_NAME_RE.match(name):
        abort(404)
    data = store.read_bytes("uploads/" + name)
    if data is None:
        abort(404)
    _, ct = _sniff_photo(data)
    resp = Response(data, mimetype=ct or "application/octet-stream")
    # endereçado por conteúdo → imutável de verdade
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


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


# ------------------------------------------------- páginas rastreáveis
# SEO/LLM: o app é uma SPA com rotas de hash (invisíveis pra crawlers e
# pra LLMs que não executam JS). O backend, que já tem o grafo em
# vocabulário schema.org, serve o conteúdo REAL: fichas de empresa em
# /empresa/<slug> com JSON-LD (Organization + AggregateRating + Review),
# sitemap, robots.txt, llms.txt e um ranking em <noscript> na home.

import html as _html

_esc = _html.escape

MODE_LABEL = {
    LB + "modeBus": "ônibus", LB + "modePlane": "avião",
    LB + "modeTrain": "trem", LB + "modeFerry": "barca",
    LB + "modeOther": "outro",
}

_QUESTION_PROPS = ("stressLevel", "permissionNeeded", "disassemblyLevel",
                   "packingLevel", "receiptRequirement", "paymentLevel")

_vocab_labels = None  # prefLabel/rdfs:label do vocab (estático no container)


def _labels(rdflib):
    global _vocab_labels
    if _vocab_labels is None:
        v = rdflib.Graph()
        v.parse(str(VOCAB_PATH), format="turtle")
        SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
        RDFS = rdflib.Namespace("http://www.w3.org/2000/01/rdf-schema#")
        _vocab_labels = {}
        for s, _, o in list(v.triples((None, SKOS.prefLabel, None))) + list(
            v.triples((None, RDFS.label, None))
        ):
            _vocab_labels.setdefault(str(s), str(o))
    return _vocab_labels


def _live_graph():
    v = _rdf()
    rdflib = v["rdflib"]
    g = rdflib.Graph()
    g.parse(data=_graph_text(), format="turtle")
    return g, rdflib


def _companies_summary(g, rdflib):
    """[{slug, name, mode, score, reviews:[{...}]}] ordenado por nota."""
    SCHEMA = rdflib.Namespace("https://schema.org/")
    LBNS = rdflib.Namespace(LB)
    labels = _labels(rdflib)
    out = []
    for comp in g.subjects(rdflib.RDF.type, LBNS.Company):
        reviews = []
        for r in g.subjects(SCHEMA.itemReviewed, comp):
            rating = g.value(r, SCHEMA.reviewRating)
            score = g.value(rating, SCHEMA.ratingValue) if rating else None
            trip = g.value(r, LBNS.trip)
            place = lambda p: (g.value(g.value(trip, p), SCHEMA.name)
                               if trip and g.value(trip, p) else None)
            answers = []
            for prop in _QUESTION_PROPS:
                o = g.value(r, LBNS[prop])
                if o is None:
                    continue
                q_label = labels.get(str(LBNS[prop]), prop)
                if isinstance(o, rdflib.Literal):
                    a_label = "sim" if str(o) == "true" else "não"
                else:
                    a_label = labels.get(str(o), str(o).split("#")[-1])
                answers.append((q_label, a_label))
            reviews.append({
                "score": int(score) if score is not None else None,
                "date": str(g.value(trip, LBNS.tripDate)) if trip and g.value(trip, LBNS.tripDate) else None,
                "from": str(place(LBNS.departurePlace)) if place(LBNS.departurePlace) else None,
                "to": str(place(LBNS.arrivalPlace)) if place(LBNS.arrivalPlace) else None,
                "body": str(g.value(r, SCHEMA.reviewBody)) if g.value(r, SCHEMA.reviewBody) else None,
                "source": str(g.value(r, rdflib.Namespace("http://www.w3.org/ns/prov#").wasDerivedFrom))
                          if g.value(r, rdflib.Namespace("http://www.w3.org/ns/prov#").wasDerivedFrom) else None,
                "answers": answers,
                "photos": [str(o) for o in g.objects(r, SCHEMA.image)],
            })
        if not reviews:
            continue
        scores = [r["score"] for r in reviews if r["score"] is not None]
        out.append({
            "slug": str(comp).split("/")[-1],
            "iri": str(comp),
            "name": str(g.value(comp, SCHEMA.name) or comp),
            "mode": MODE_LABEL.get(str(g.value(comp, LBNS.mode)), "outro"),
            "score": round(sum(scores) / len(scores), 1) if scores else None,
            "reviews": sorted(reviews, key=lambda r: r["date"] or "", reverse=True),
        })
    out.sort(key=lambda c: (-(c["score"] or 0), -len(c["reviews"]), c["name"]))
    return out


def _public_base():
    return PUBLIC_BASE or request.url_root.rstrip("/")


def _fmt_score(s):
    return ("%.1f" % s).replace(".", ",") if s is not None else "—"


def _company_jsonld(c, base):
    import json as _json
    data = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": c["iri"],
        "name": c["name"],
        "url": f"{base}/empresa/{c['slug']}",
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": c["score"],
            "reviewCount": len(c["reviews"]),
            "bestRating": 5,
            "worstRating": 1,
        },
        "review": [
            {
                "@type": "Review",
                **({"datePublished": r["date"]} if r["date"] else {}),
                **({"reviewBody": r["body"]} if r["body"] else {}),
                "reviewRating": {"@type": "Rating", "ratingValue": r["score"],
                                 "bestRating": 5, "worstRating": 1},
            }
            for r in c["reviews"] if r["score"] is not None
        ],
    }
    return _json.dumps(data, ensure_ascii=False)


@app.get("/empresa/<slug>")
def company_page(slug):
    if not SLUG_RE.match(slug):
        abort(404)
    g, rdflib = _live_graph()
    company = next((c for c in _companies_summary(g, rdflib) if c["slug"] == slug), None)
    if company is None:
        abort(404)
    base = _public_base()
    n = len(company["reviews"])
    title = (f"{company['name']} — nota {_fmt_score(company['score'])}/5 de "
             f"amigabilidade à bicicleta · levabici")
    first_body = next((r["body"] for r in company["reviews"] if r["body"]), "")
    desc = (f"Como a {company['name']} ({company['mode']}) trata bicicletas: "
            f"nota {_fmt_score(company['score'])}/5 em {n} "
            f"{'avaliação' if n == 1 else 'avaliações'} do coletivo. "
            + first_body)[:300]

    cards = []
    for r in company["reviews"]:
        route = " → ".join(x for x in (r["from"], r["to"]) if x)
        answers = " · ".join(f"{q}: {a}" for q, a in r["answers"])
        cards.append(
            '<li class="review-card">'
            f'<div class="review-head"><strong>{_fmt_score(r["score"])}/5</strong>'
            + (f'<span class="review-route">{_esc(route)}</span>' if route else "")
            + (f'<span class="review-date">{_esc(r["date"])}</span>' if r["date"] else "")
            + "</div>"
            + (f'<p class="review-body">{_esc(r["body"])}</p>' if r["body"] else "")
            + (f'<p class="hint">{_esc(answers)}</p>' if answers else "")
            + (f'<p class="hint"><a href="{_esc(r["source"])}" rel="nofollow">fonte do relato</a></p>'
               if r["source"] else "")
            + "</li>"
        )

    page = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(desc)}">
<link rel="canonical" href="{base}/empresa/{_esc(slug)}">
<link rel="stylesheet" href="/style.css">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="alternate" type="text/turtle" href="/data/reviews.ttl" title="Grafo RDF completo (Turtle)">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(desc)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{base}/empresa/{_esc(slug)}">
<meta property="og:image" content="{base}/icon-512.png">
<script type="application/ld+json">{_company_jsonld(company, base)}</script>
</head>
<body>
<header class="app-header"><h1><a href="/">leva·bici</a></h1>
<p class="tagline">a bici no transporte coletivo — conte como foi, veja onde rola</p></header>
<main id="main"><section class="view">
<h2>{_esc(company['name'])} ({_esc(company['mode'])})</h2>
<p>Nota de amigabilidade à bicicleta: <strong>{_fmt_score(company['score'])}/5</strong>
em {n} {'avaliação' if n == 1 else 'avaliações'}.</p>
<p><a class="btn btn-primary" href="/#/empresa/{_esc(slug)}">abrir no app</a></p>
<ul class="review-list">{''.join(cards)}</ul>
<footer class="about"><p><a href="/">ranking completo</a> ·
<a href="/data/reviews.ttl">dados abertos (Turtle/RDF)</a> ·
<a href="/llms.txt">llms.txt</a></p></footer>
</section></main>
</body>
</html>"""
    return Response(page, mimetype="text/html")


@app.get("/sitemap.xml")
def sitemap():
    g, rdflib = _live_graph()
    base = _public_base()
    urls = [f"<url><loc>{base}/</loc></url>"]
    for c in _companies_summary(g, rdflib):
        lastmod = max((r["date"] or "" for r in c["reviews"]), default="")
        lastmod_tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        urls.append(f"<url><loc>{base}/empresa/{_esc(c['slug'])}</loc>{lastmod_tag}</url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(urls) + "</urlset>")
    return Response(xml, mimetype="application/xml")


@app.get("/robots.txt")
def robots():
    base = _public_base()
    return Response(
        f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n",
        mimetype="text/plain",
    )


@app.get("/llms.txt")
def llms_txt():
    g, rdflib = _live_graph()
    base = _public_base()
    companies = _companies_summary(g, rdflib)
    total = sum(len(c["reviews"]) for c in companies)
    lines = [
        "# levabici",
        "",
        "> Avaliações comunitárias de transporte de BICICLETAS em transporte",
        "> coletivo no Brasil (ônibus rodoviário, avião, trem, barca). Cada",
        "> empresa tem uma nota de amigabilidade à bici (1-5, média das",
        "> avaliações) e relatos com detalhes: precisou desmontar? embalar?",
        "> pagar quanto? Projeto do coletivo Pedal Hidrográfico, dados como",
        "> grafo RDF/Turtle aberto validado por SHACL.",
        "",
        f"Hoje: {total} avaliações sobre {len(companies)} empresas.",
        "",
        "## Dados estruturados (preferir estes para leitura por máquina)",
        "",
        f"- [Grafo completo (Turtle)]({base}/data/reviews.ttl): todas as",
        "  empresas e avaliações em RDF, vocabulário schema.org + PROV-O",
        f"- [Ontologia]({base}/data/vocab.ttl) e [shapes SHACL]({base}/data/shapes.ttl)",
        "",
        "## Empresas avaliadas",
        "",
    ]
    for c in companies:
        n = len(c["reviews"])
        lines.append(
            f"- [{c['name']}]({base}/empresa/{c['slug']}): {c['mode']}, nota "
            f"{_fmt_score(c['score'])}/5 em {n} {'avaliação' if n == 1 else 'avaliações'}"
        )
    return Response("\n".join(lines) + "\n", mimetype="text/plain; charset=utf-8")


# ---------------------------------------------------------------- estáticos

@app.get("/")
def index():
    """index.html com o ranking injetado em <noscript> (crawlers e LLMs
    sem JS leem conteúdo de verdade; o app substitui tudo no boot)."""
    html_text = (WEB / "index.html").read_text(encoding="utf-8")
    marker = "<!-- SSR:RANKING -->"
    if marker in html_text:
        try:
            g, rdflib = _live_graph()
            items = "".join(
                f'<li><a href="/empresa/{_esc(c["slug"])}">{_esc(c["name"])}</a> '
                f"({_esc(c['mode'])}): nota {_fmt_score(c['score'])}/5 em "
                f"{len(c['reviews'])} {'avaliação' if len(c['reviews']) == 1 else 'avaliações'}</li>"
                for c in _companies_summary(g, rdflib)
            )
            html_text = html_text.replace(
                marker, f"<h2>Empresas amigas da bici</h2><ol>{items}</ol>"
            )
        except Exception:  # noqa: BLE001 — home nunca cai por causa do SSR
            pass
    return Response(html_text, mimetype="text/html")


@app.get("/<path:path>")
def static_files(path):
    if any(path.startswith(p) for p in BLOCKED_PREFIXES):
        abort(404)
    return send_from_directory(WEB, path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8613)), debug=True)
