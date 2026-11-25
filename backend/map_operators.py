"""Empresas do mapa dos ônibus (mapa-onibus/data/deonibus.trig) → operators.json.

O deonibus.trig tem ~92 mil triplas: ler com rdflib custa ~3 s e >100 MB,
caro demais pra cada cold start do Cloud Run. Então o Dockerfile roda
este arquivo UMA vez no build e o backend só lê o JSON resultante
({IRI da empresa no mapa: nome}). Em dev, sem o JSON, `load()` extrai na
primeira chamada e tenta gravar o cache.

    python map_operators.py <pasta mapa-onibus>
"""

import json
import sys
from pathlib import Path

OPERATOR = "https://abiru.to/onibus/def#Operator"
SCHEMA_NAME = "https://schema.org/name"
CACHE = "operators.json"


def extract(map_dir):
    import rdflib

    d = rdflib.Dataset()
    d.parse(str(Path(map_dir) / "data" / "deonibus.trig"), format="trig")
    name = rdflib.URIRef(SCHEMA_NAME)
    ops = {}
    for s, _, _, _ in d.quads((None, rdflib.RDF.type, rdflib.URIRef(OPERATOR), None)):
        label = next((str(o) for _, _, o, _ in d.quads((s, name, None, None))), None)
        ops[str(s)] = label or str(s).rstrip("/").rsplit("/", 1)[-1]
    return dict(sorted(ops.items()))


def load(map_dir):
    """{IRI: nome}; {} se o mapa não foi trazido (tools/fetch_onibus_map.sh)."""
    map_dir = Path(map_dir)
    cache = map_dir / CACHE
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    if not (map_dir / "data" / "deonibus.trig").is_file():
        return {}
    ops = extract(map_dir)
    try:
        cache.write_text(json.dumps(ops, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # pasta só-leitura: recalcula no próximo boot
    return ops


if __name__ == "__main__":
    target = Path(sys.argv[1])
    found = extract(target)
    if not found:
        sys.exit(f"nenhuma ob:Operator em {target}/data/deonibus.trig")
    (target / CACHE).write_text(
        json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{target / CACHE}: {len(found)} empresas")
