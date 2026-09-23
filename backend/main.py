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
  GET  /empresa/<slug>        → ficha SSR (HTML + JSON-LD) | Turtle (conneg)
  GET  /onibus|aviao|trem|barca → ranking SSR por modal
  GET  /avaliacao/<slug>      → Turtle (conneg) | 303 pro cartão na ficha
  GET  /terms                 → vocab (Turtle) | 303 /data/vocab.ttl
  GET  /sitemap.xml, /robots.txt, /llms.txt
"""

import hashlib
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory

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
    return Response(_graph_text(), content_type="text/turtle; charset=utf-8",
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
# /empresa/<slug> e rankings por modal (/onibus, /aviao, /trem, /barca),
# com resumo em TEXTO dos relatos (é o que buscadores e o AI Mode do
# Google citam), JSON-LD, sitemap, robots.txt, llms.txt e um ranking em
# <noscript> na home. Os IRIs de id.pedalhidrografi.co/levabici/… caem
# aqui (303 da Cloudflare → amora → este serviço) e respondem por
# content negotiation: Turtle pra máquina, página pra gente.
#
# Dados estruturados seguem as regras de review snippet do Google:
# avaliações DERIVADAS de outro site (prov:wasDerivedFrom, o WikiVoyage)
# ficam visíveis mas FORA do JSON-LD ("don't aggregate reviews from
# other websites"); a nota agregada do JSON-LD é só a da comunidade,
# e a página mostra esse número também. Sem assinatura, autor "anônimo".

import html as _html
import json as _json
import statistics
import time
import urllib.request

_esc = _html.escape

PROV = "http://www.w3.org/ns/prov#"
DCTERMS = "http://purl.org/dc/terms/"
SCHEMA = "https://schema.org/"

MODE_LABEL = {
    LB + "modeBus": "ônibus", LB + "modePlane": "avião",
    LB + "modeTrain": "trem", LB + "modeFerry": "barca",
    LB + "modeOther": "outro",
}

# ranking por modal: slug da URL e "levar bicicleta <prep>"
MODE_PAGES = {
    LB + "modeBus": ("onibus", "no ônibus"),
    LB + "modePlane": ("aviao", "no avião"),
    LB + "modeTrain": ("trem", "no trem"),
    LB + "modeFerry": ("barca", "na barca"),
}
MODE_BY_SLUG = {slug: iri for iri, (slug, _) in MODE_PAGES.items()}

_QUESTION_PROPS = ("stressLevel", "permissionNeeded", "disassemblyLevel",
                   "packingLevel", "receiptRequirement", "paymentLevel")

ANON = "anônimo"

_vocab_info = None  # rótulos + ordinais do vocab (estático no container)


def _vocab(rdflib):
    """({iri: rótulo}, {iri: ordinal}) — prefLabel/rdfs:label e lb:ordinal."""
    global _vocab_info
    if _vocab_info is None:
        v = rdflib.Graph()
        v.parse(str(VOCAB_PATH), format="turtle")
        SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
        labels, ordinals = {}, {}
        for s, _, o in list(v.triples((None, SKOS.prefLabel, None))) + list(
            v.triples((None, rdflib.RDFS.label, None))
        ):
            labels.setdefault(str(s), str(o))
        for s, _, o in v.triples((None, rdflib.URIRef(LB + "ordinal"), None)):
            ordinals[str(s)] = int(o)
        _vocab_info = (labels, ordinals)
    return _vocab_info


def _live_graph():
    v = _rdf()
    rdflib = v["rdflib"]
    g = rdflib.Graph()
    g.parse(data=_graph_text(), format="turtle")
    return g, rdflib


# ---- mapa de ônibus rodoviários (abiru.to/onibus) ----
# O mapa publica a identidade das empresas dele com as nossas (um
# void:Linkset de owl:sameAs op:<slug> → emp:<slug>) e já lê o nosso
# grafo ao vivo. Aqui é a volta: a ficha linka pras linhas da empresa
# no mapa. Melhor esforço com cache — o mapa fora do ar só some o link.
ABIRU_MAP = "https://abiru.to/onibus/"
ABIRU_LINKS = ABIRU_MAP + "data/levabici-links.ttl"
ABIRU_TTL = 6 * 3600
_abiru_cache = {"at": 0.0, "links": {}}
_abiru_lock = threading.Lock()


def _abiru_links():
    """{slug levabici: slug do mapa} (cache de 6 h; falha → o último bom)."""
    with _abiru_lock:
        if time.time() - _abiru_cache["at"] < ABIRU_TTL:
            return _abiru_cache["links"]
        _abiru_cache["at"] = time.time()  # falhou? não tenta de novo a cada request
        try:
            req = urllib.request.Request(ABIRU_LINKS, headers={"User-Agent": "levabici"})
            with urllib.request.urlopen(req, timeout=4) as res:
                text = res.read().decode("utf-8")
            rdflib = _rdf()["rdflib"]
            g = rdflib.Graph()
            g.parse(data=text, format="turtle")
            links = {}
            for s, o in g.subject_objects(rdflib.OWL.sameAs):
                if str(o).startswith(EMP):
                    links.setdefault(str(o)[len(EMP):], str(s).rstrip("/").split("/")[-1])
            _abiru_cache["links"] = links
        except Exception:  # noqa: BLE001 — integração é enfeite, nunca derruba a página
            pass
        return _abiru_cache["links"]


def _abiru_url(op_slug):
    return f"{ABIRU_MAP}#empresas={op_slug}"


# ---- resumo do grafo ----

def _day(s):
    return str(s)[:10] if s else None


def _companies_summary(g, rdflib):
    """[{slug, name, mode, score, community, reviews:[{...}]}] ordenado por nota."""
    S = rdflib.Namespace(SCHEMA)
    LBNS = rdflib.Namespace(LB)
    P = rdflib.Namespace(PROV)
    labels, ordinals = _vocab(rdflib)

    def val(s, p):
        return g.value(s, p) if s is not None else None

    out = []
    for comp in g.subjects(rdflib.RDF.type, LBNS.Company):
        reviews = []
        for r in g.subjects(S.itemReviewed, comp):
            score = val(val(r, S.reviewRating), S.ratingValue)
            trip = val(r, LBNS.trip)
            answers = []  # (prop, pergunta, resposta, ordinal)
            for prop in _QUESTION_PROPS:
                o = g.value(r, LBNS[prop])
                if o is None:
                    continue
                q_label = labels.get(LB + prop, prop)
                if isinstance(o, rdflib.Literal):
                    yes = str(o) == "true"
                    answers.append((prop, q_label, "sim" if yes else "não", int(yes)))
                else:
                    answers.append((prop, q_label, labels.get(str(o), str(o).split("#")[-1]),
                                    ordinals.get(str(o), 0)))
            paid = val(val(r, LBNS.amountPaid), S.value)
            generated = val(r, P.generatedAtTime)
            modified = val(r, rdflib.URIRef(DCTERMS + "modified"))
            reviews.append({
                "slug": str(r)[len(AV):] if str(r).startswith(AV) else str(r),
                "score": int(score) if score is not None else None,
                "date": _day(val(trip, LBNS.tripDate)),
                "from": str(val(val(trip, LBNS.departurePlace), S.name) or "") or None,
                "to": str(val(val(trip, LBNS.arrivalPlace), S.name) or "") or None,
                "body": str(g.value(r, S.reviewBody) or "") or None,
                "author": str(val(val(r, S.author), S.name) or "") or None,
                "source": str(g.value(r, P.wasDerivedFrom) or "") or None,
                "published": _day(generated),
                "updated": max(str(x) for x in (generated, modified) if x) if (generated or modified) else None,
                "answers": answers,
                "paid": float(paid) if paid is not None else None,
                "photos": [str(o) for o in g.objects(r, S.image)],
            })
        if not reviews:
            continue
        reviews.sort(key=lambda r: r["date"] or r["published"] or "", reverse=True)

        def mean(rs):
            scores = [r["score"] for r in rs if r["score"] is not None]
            return round(sum(scores) / len(scores), 1) if scores else None

        community = [r for r in reviews if not r["source"]]
        mode_iri = str(g.value(comp, LBNS.mode))
        out.append({
            "slug": str(comp).split("/")[-1],
            "iri": str(comp),
            "name": str(g.value(comp, S.name) or comp),
            "alt_names": sorted(str(o) for o in g.objects(comp, S.alternateName)),
            "same_as": sorted(str(o) for o in g.objects(comp, S.sameAs)),
            "mode_iri": mode_iri,
            "mode": MODE_LABEL.get(mode_iri, "outro"),
            "score": mean(reviews),
            "community": community,
            "community_score": mean(community),
            "updated": max((r["updated"] for r in reviews if r["updated"]), default=None),
            "reviews": reviews,
        })
    out.sort(key=lambda c: (-(c["score"] or 0), -len(c["reviews"]), c["name"]))
    return out


def _public_base():
    return PUBLIC_BASE or request.url_root.rstrip("/")


def _fmt_score(s):
    return ("%.1f" % s).replace(".", ",") if s is not None else "—"


def _brl(v):
    return "R$ " + ("%.2f" % v).replace(".", ",")


def _n(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _answer_summary(reviews):
    """[(Pergunta?, "não em 3 relatos · sim em 1 relato")] na ordem do formulário."""
    out = []
    for prop in _QUESTION_PROPS:
        counts, q_label = {}, None
        for r in reviews:
            for p, ql, al, o in r["answers"]:
                if p == prop:
                    q_label = ql
                    counts[(o, al)] = counts.get((o, al), 0) + 1
        if counts:
            parts = [f"{al} em {_n(c, 'relato', 'relatos')}"
                     for (_, al), c in sorted(counts.items())]
            out.append((q_label[:1].upper() + q_label[1:], " · ".join(parts)))
    return out


def _paid_summary(reviews):
    vals = sorted(r["paid"] for r in reviews if r["paid"] is not None)
    if not vals:
        return None
    if vals[0] == vals[-1]:
        return f"{_brl(vals[0])} em {_n(len(vals), 'relato', 'relatos')}"
    return (f"de {_brl(vals[0])} a {_brl(vals[-1])} — mediana "
            f"{_brl(statistics.median(vals))} em {len(vals)} relatos")


def _period(reviews):
    years = sorted({r["date"][:4] for r in reviews if r["date"]})
    if not years:
        return None
    return f"em {years[0]}" if len(years) == 1 else f"entre {years[0]} e {years[-1]}"


def _routes(reviews, limit=12):
    seen = []
    for r in reviews:
        if r["from"] or r["to"]:
            route = f"{r['from'] or '?'} → {r['to'] or '?'}"
            if route not in seen:
                seen.append(route)
    return seen[:limit]


def _jsonld(data):
    # "</" dentro de <script> fecharia a tag — escapa a barra
    return _json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _breadcrumb_jsonld(items):
    return _jsonld({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(items)
        ],
    })


def _crumbs_html(items):
    links = [f'<a href="{_esc(url)}">{_esc(name)}</a>' for name, url in items[:-1]]
    return ('<nav class="hint crumbs" aria-label="Você está em">'
            + " › ".join(links + [_esc(items[-1][0])]) + "</nav>")


def _company_crumbs(c, base):
    items = [("levabici", f"{base}/")]
    if c["mode_iri"] in MODE_PAGES:
        items.append((c["mode"], f"{base}/{MODE_PAGES[c['mode_iri']][0]}"))
    items.append((c["name"], f"{base}/empresa/{c['slug']}"))
    return items


def _company_jsonld(c, base):
    page = f"{base}/empresa/{c['slug']}"
    data = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": c["iri"],
        "name": c["name"],
        "mainEntityOfPage": page,
    }
    if c["alt_names"]:
        data["alternateName"] = c["alt_names"]
    if c["same_as"]:
        data["sameAs"] = c["same_as"]
    rated = [r for r in c["community"] if r["score"] is not None]
    if rated:
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": c["community_score"],
            "reviewCount": len(rated),
            "bestRating": 5,
            "worstRating": 1,
        }
        data["review"] = [
            {
                "@type": "Review",
                "@id": AV + r["slug"],
                "url": f"{page}#{r['slug']}",
                "author": {"@type": "Person", "name": r["author"] or ANON},
                **({"datePublished": r["published"]} if r["published"] else {}),
                **({"reviewBody": r["body"]} if r["body"] else {}),
                "reviewRating": {"@type": "Rating", "ratingValue": r["score"],
                                 "bestRating": 5, "worstRating": 1},
            }
            for r in rated
        ]
    return _jsonld(data)


def _page(title, desc, canonical, jsonld_blocks, body, base, noindex=False):
    scripts = "".join(f'<script type="application/ld+json">{b}</script>\n'
                      for b in jsonld_blocks)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(desc)}">
<link rel="canonical" href="{_esc(canonical)}">
{'<meta name="robots" content="noindex">' if noindex else ''}
<link rel="stylesheet" href="/style.css">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="alternate" type="text/turtle" href="/data/reviews.ttl" title="Grafo RDF completo (Turtle)">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(desc)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{_esc(canonical)}">
<meta property="og:image" content="{base}/icon-512.png">
{scripts}</head>
<body>
<header class="app-header"><h1><a href="/">leva·bici</a></h1>
<p class="tagline">a bici no transporte coletivo — conte como foi, veja onde rola</p></header>
<main id="main"><section class="view">
{body}
<footer class="about"><p><a href="/">ranking completo</a> ·
{" · ".join(f'<a href="/{slug}">{_esc(prep)}</a>' for slug, prep in MODE_PAGES.values())} ·
<a href="/data/reviews.ttl">dados abertos (Turtle/RDF)</a> ·
<a href="/llms.txt">llms.txt</a></p></footer>
</section></main>
</body>
</html>"""


def _clip(text, limit=300):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;·—") + "…"


def _review_card(r):
    route = " → ".join(x for x in (r["from"], r["to"]) if x)
    answers = " · ".join(f"{q} {a}" for _, q, a, _ in r["answers"])
    meta = [f"— {_esc(r['author'] or ANON)}"]
    if r["published"]:
        meta.append(f"publicado em {_esc(r['published'])}")
    return (
        f'<li class="review-card" id="{_esc(r["slug"])}">'
        f'<div class="review-head"><strong>{_fmt_score(r["score"])}/5</strong>'
        + (f'<span class="review-route">{_esc(route)}</span>' if route else "")
        + (f'<span class="review-date">viagem: {_esc(r["date"])}</span>' if r["date"] else "")
        + "</div>"
        + (f'<p class="review-body">{_esc(r["body"])}</p>' if r["body"] else "")
        + (f'<p class="hint">{_esc(answers)}</p>' if answers else "")
        + (f'<p class="hint">pagou {_brl(r["paid"])}</p>' if r["paid"] else "")
        + f'<p class="review-by">{" · ".join(meta)}</p>'
        + (f'<p class="hint"><a href="{_esc(r["source"])}" rel="nofollow">fonte do relato</a></p>'
           if r["source"] else "")
        + "</li>"
    )


def _wants_turtle():
    if request.args.get("format") in ("ttl", "turtle"):
        return True
    best = request.accept_mimetypes.best_match(["text/html", "text/turtle"])
    return best == "text/turtle"


def _turtle_response(g):
    resp = Response(_serialize_graph(g), content_type="text/turtle; charset=utf-8")
    resp.headers["Vary"] = "Accept"
    return resp


@app.get("/empresa/<slug>")
def company_page(slug):
    if not SLUG_RE.match(slug):
        abort(404)
    g, rdflib = _live_graph()
    if _wants_turtle():
        comp = rdflib.URIRef(EMP + slug)
        if (comp, rdflib.RDF.type, None) not in g:
            abort(404)
        out = rdflib.Graph()
        for t in g.triples((comp, None, None)):
            out.add(t)
        for r in g.subjects(rdflib.URIRef(SCHEMA + "itemReviewed"), comp):
            for t in _subtree(g, r):
                out.add(t)
        return _turtle_response(out)

    company = next((c for c in _companies_summary(g, rdflib) if c["slug"] == slug), None)
    if company is None:
        abort(404)
    base = _public_base()
    c = company
    n = len(c["reviews"])
    reviews_n = _n(n, "avaliação", "avaliações")
    title = f"Levar bicicleta na {c['name']} ({c['mode']}): nota {_fmt_score(c['score'])}/5 em {reviews_n} · levabici"
    facts = _answer_summary(c["reviews"])
    paid = _paid_summary(c["reviews"])
    period = _period(c["reviews"])
    routes = _routes(c["reviews"])
    desc = _clip(
        f"Como a {c['name']} ({c['mode']}) trata quem leva bicicleta: nota "
        f"{_fmt_score(c['score'])}/5 em {reviews_n}. "
        + " ".join(f"{q} {a}." for q, a in facts[:3])
    )

    derived = n - len(c["community"])
    if derived and c["community"]:
        provenance = (
            f"<p>{_n(len(c['community']), 'avaliação foi enviada', 'avaliações foram enviadas')} "
            f"direto ao levabici (nota <strong>{_fmt_score(c['community_score'])}/5</strong>); "
            f"{'a outra vem' if derived == 1 else f'as outras {derived} vêm'} do WikiVoyage.</p>")
    elif derived:
        provenance = ("<p>Por enquanto as avaliações vêm do artigo do WikiVoyage "
                      "(CC BY-SA 4.0) — conte como foi a sua viagem pra somar um "
                      "relato do coletivo.</p>")
    else:
        provenance = ""

    fact_items = [f"<li>{_esc(q)} {_esc(a)}.</li>" for q, a in facts]
    if paid:
        fact_items.append(f"<li>Quanto pagou pela bici? {_esc(paid)}.</li>")
    op = _abiru_links().get(c["slug"]) if c["mode_iri"] == LB + "modeBus" else None
    alt = f" ({_esc(', '.join(c['alt_names']))})" if c["alt_names"] else ""

    body = (
        _crumbs_html(_company_crumbs(c, base))
        + f"<h2>Levar bicicleta na {_esc(c['name'])}</h2>"
        + f"<p>A {_esc(c['name'])}{alt}, empresa de {_esc(c['mode'])}, tem nota "
        f"<strong>{_fmt_score(c['score'])}/5</strong> de amigabilidade à bicicleta — média de "
        f"{reviews_n} de quem viajou com a bici"
        + (f", com viagens {_esc(period)}" if period else "") + ".</p>"
        + provenance
        + (f"<h3>O que os relatos dizem</h3><ul>{''.join(fact_items)}</ul>" if fact_items else "")
        + (f"<p>Trajetos relatados: {_esc('; '.join(routes))}.</p>" if routes else "")
        + (f'<p><a href="{_esc(_abiru_url(op))}">ver as linhas da {_esc(c["name"])} '
           f"no mapa dos ônibus rodoviários do Brasil</a> (abiru.to/onibus)</p>" if op else "")
        + f'<p><a class="btn btn-primary" href="/#/empresa/{_esc(slug)}">abrir no app</a> '
        f'<a class="btn" href="/#/nova">＋ avaliar</a></p>'
        + f"<h3>Relatos</h3><ul class=\"review-list\">{''.join(_review_card(r) for r in c['reviews'])}</ul>"
    )
    page = _page(title, desc, f"{base}/empresa/{slug}",
                 [_company_jsonld(c, base), _breadcrumb_jsonld(_company_crumbs(c, base))],
                 body, base)
    resp = Response(page, mimetype="text/html")
    resp.headers["Vary"] = "Accept"
    return resp


@app.get("/<any(onibus, aviao, trem, barca):mode_slug>")
def mode_page(mode_slug):
    mode_iri = MODE_BY_SLUG[mode_slug]
    prep = MODE_PAGES[mode_iri][1]
    label = MODE_LABEL[mode_iri]
    g, rdflib = _live_graph()
    companies = [c for c in _companies_summary(g, rdflib) if c["mode_iri"] == mode_iri]
    base = _public_base()
    crumbs = [("levabici", f"{base}/"), (label, f"{base}/{mode_slug}")]
    if not companies:
        # sem avaliações ainda: página útil pra gente, fora do índice
        body = (_crumbs_html(crumbs)
                + f"<h2>Levar bicicleta {_esc(prep)}</h2>"
                + f"<p>Nenhuma avaliação de {_esc(label)} ainda — seja a primeira pessoa "
                  "a contar como foi levar a bici!</p>"
                + '<p><a class="btn btn-primary" href="/#/nova">＋ avaliar uma viagem</a></p>')
        return Response(_page(f"Levar bicicleta {prep} · levabici",
                              f"Avaliações de levar bicicleta {prep}.",
                              f"{base}/{mode_slug}", [_breadcrumb_jsonld(crumbs)],
                              body, base, noindex=True),
                        mimetype="text/html")
    all_reviews = [r for c in companies for r in c["reviews"]]
    title = (f"Levar bicicleta {prep}: ranking de {_n(len(companies), 'empresa', 'empresas')} "
             f"por amigabilidade à bici · levabici")
    best = [c for c in companies if c["score"] is not None][:3]
    best_txt = ", ".join(f"{c['name']} ({_fmt_score(c['score'])})" for c in best)
    facts = _answer_summary(all_reviews)
    paid = _paid_summary(all_reviews)
    desc = _clip(
        f"Quais empresas de {label} são amigas da bicicleta? {_n(len(companies), 'empresa avaliada', 'empresas avaliadas')} "
        f"em {_n(len(all_reviews), 'relato', 'relatos')}. Melhores notas: {best_txt}."
    )

    fact_items = [f"<li>{_esc(q)} {_esc(a)}.</li>" for q, a in facts]
    if paid:
        fact_items.append(f"<li>Quanto pagou pela bici? {_esc(paid)}.</li>")
    rows = []
    for c in companies:
        top = _answer_summary(c["reviews"])[:3]
        rows.append(
            f'<li><a href="/empresa/{_esc(c["slug"])}"><strong>{_esc(c["name"])}</strong></a>'
            + (f" ({_esc(', '.join(c['alt_names']))})" if c["alt_names"] else "")
            + f": nota {_fmt_score(c['score'])}/5 em {_n(len(c['reviews']), 'avaliação', 'avaliações')}"
            + (f'<br><span class="hint">{_esc(" · ".join(f"{q} {a}" for q, a in top))}</span>' if top else "")
            + "</li>"
        )
    body = (
        _crumbs_html(crumbs)
        + f"<h2>Levar bicicleta {_esc(prep)}: quais empresas são amigas da bici</h2>"
        + f"<p>{_n(len(companies), 'empresa', 'empresas')} de {_esc(label)} "
        f"{'avaliada' if len(companies) == 1 else 'avaliadas'} em "
        f"{_n(len(all_reviews), 'relato', 'relatos')} de quem levou a bicicleta. "
        f"Melhores notas de amigabilidade (1–5): {_esc(best_txt)}.</p>"
        + (f"<h3>No conjunto dos relatos</h3><ul>{''.join(fact_items)}</ul>" if fact_items else "")
        + (f'<p>Pra ver quem liga duas cidades, o <a href="{ABIRU_MAP}">mapa dos ônibus '
           f"rodoviários do Brasil</a> (abiru.to/onibus) colore as estradas pela nota do levabici.</p>"
           if mode_iri == LB + "modeBus" else "")
        + f'<h3>Ranking</h3><ol class="review-list">{"".join(rows)}</ol>'
        + '<p><a class="btn btn-primary" href="/#/nova">＋ avaliar uma viagem</a></p>'
    )
    itemlist = _jsonld({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"Empresas de {label} por amigabilidade à bicicleta",
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "numberOfItems": len(companies),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": c["name"],
             "url": f"{base}/empresa/{c['slug']}"}
            for i, c in enumerate(companies)
        ],
    })
    return Response(_page(title, desc, f"{base}/{mode_slug}",
                          [itemlist, _breadcrumb_jsonld(crumbs)], body, base),
                    mimetype="text/html")


@app.get("/avaliacao/<slug>")
def review_resource(slug):
    """IRI de avaliação: Turtle da subárvore, ou 303 pro cartão na ficha."""
    if not SLUG_RE.match(slug):
        abort(404)
    g, rdflib = _live_graph()
    iri = rdflib.URIRef(AV + slug)
    comp = g.value(iri, rdflib.URIRef(SCHEMA + "itemReviewed"))
    if comp is None:
        abort(404)
    if _wants_turtle():
        out = rdflib.Graph()
        for t in _subtree(g, iri):
            out.add(t)
        return _turtle_response(out)
    resp = redirect(f"/empresa/{str(comp).split('/')[-1]}#{slug}", code=303)
    resp.headers["Vary"] = "Accept"
    return resp


@app.get("/terms")
def terms():
    """IRI do vocabulário (…/levabici/terms#X; o fragmento não chega aqui)."""
    if _wants_turtle():
        return _turtle_response(_rdf()["vocab"])
    return redirect("/data/vocab.ttl", code=303)


@app.get("/sitemap.xml")
def sitemap():
    g, rdflib = _live_graph()
    base = _public_base()
    companies = _companies_summary(g, rdflib)

    def url(loc, updated):
        lastmod = f"<lastmod>{_esc(updated[:10])}</lastmod>" if updated else ""
        return f"<url><loc>{_esc(loc)}</loc>{lastmod}</url>"

    def newest(cs):
        return max((c["updated"] for c in cs if c["updated"]), default=None)

    urls = [url(f"{base}/", newest(companies))]
    for mode_iri, (mode_slug, _) in MODE_PAGES.items():
        of_mode = [c for c in companies if c["mode_iri"] == mode_iri]
        if of_mode:
            urls.append(url(f"{base}/{mode_slug}", newest(of_mode)))
    for c in companies:
        urls.append(url(f"{base}/empresa/{c['slug']}", c["updated"]))
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
        f"Hoje: {total} avaliações sobre {len(companies)} empresas. Relatos sem",
        "assinatura aparecem como anônimos; os marcados com fonte vêm do WikiVoyage.",
        "",
        "## Dados estruturados (preferir estes para leitura por máquina)",
        "",
        f"- [Grafo completo (Turtle)]({base}/data/reviews.ttl): todas as",
        "  empresas e avaliações em RDF, vocabulário schema.org + PROV-O",
        f"- [Ontologia]({base}/data/vocab.ttl) e [shapes SHACL]({base}/data/shapes.ttl)",
        f"- [Mapa dos ônibus rodoviários do Brasil]({ABIRU_MAP}): as linhas de cada",
        "  empresa, com as estradas coloridas pela nota do levabici",
        "",
        "## Rankings por modal",
        "",
    ]
    for mode_iri, (mode_slug, prep) in MODE_PAGES.items():
        n = sum(1 for c in companies if c["mode_iri"] == mode_iri)
        if n:
            lines.append(f"- [Levar bicicleta {prep}]({base}/{mode_slug}): {_n(n, 'empresa', 'empresas')}")
    lines += ["", "## Empresas avaliadas", ""]
    for c in companies:
        n = len(c["reviews"])
        lines.append(
            f"- [{c['name']}]({base}/empresa/{c['slug']}): {c['mode']}, nota "
            f"{_fmt_score(c['score'])}/5 em {_n(n, 'avaliação', 'avaliações')}"
        )
    return Response("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


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
    # o Worker da Cloudflare reescreve "/" → "/index.html" (convenção do
    # amora); sem isto a home sairia estática, sem o ranking em <noscript>
    if path == "index.html":
        return index()
    return send_from_directory(WEB, path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8613)), debug=True)
