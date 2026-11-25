#!/usr/bin/env bash
# Traz o mapa dos ônibus rodoviários (danlessa/onibus, publicado em
# abiru.to/onibus) pra dentro do levabici: copia o `site/` dele pra
# mapa-onibus/ (pasta fora do git), que o backend serve em /mapa-onibus/.
# O deploy.sh roda isto antes de subir; local, rode uma vez à mão.
#
#   tools/fetch_onibus_map.sh               # clona o main do GitHub
#   ONIBUS_SRC=../../danlessa/onibus tools/fetch_onibus_map.sh   # checkout local
set -euo pipefail
cd "$(dirname "$0")/.."

REPO=https://github.com/danlessa/onibus.git
DEST=mapa-onibus
SRC="${ONIBUS_SRC:-}"

if [ -z "${SRC}" ]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp}"' EXIT
  git clone --quiet --depth 1 "${REPO}" "${tmp}/onibus"
  SRC="${tmp}/onibus"
fi

for f in site/index.html site/app.js site/data/catalog.ttl site/data/deonibus.trig \
         pipeline/levabici_aliases.json; do
  [ -f "${SRC}/${f}" ] || { echo "faltou ${f} em ${SRC}" >&2; exit 1; }
done

rm -rf "${DEST}.new"
mkdir "${DEST}.new"
cp -R "${SRC}/site/." "${DEST}.new/"
# empresa do mapa → empresa do levabici quando slug e nome não batem
cp "${SRC}/pipeline/levabici_aliases.json" "${DEST}.new/levabici_aliases.json"
# O linkset pré-gerado NÃO vem: aqui ele é calculado ao vivo do grafo
# (backend, /mapa-onibus/data/levabici-links.ttl). Sem o arquivo, um
# deslize de rota dá 404 em vez de servir link velho.
rm -f "${DEST}.new/data/levabici-links.ttl"
git -C "${SRC}" rev-parse HEAD > "${DEST}.new/SOURCE" 2>/dev/null || echo "desconhecido" > "${DEST}.new/SOURCE"

# estradas.ttl tem ~18 MB: o backend serve o .gz a quem aceita gzip
find "${DEST}.new" -type f \( -name '*.ttl' -o -name '*.trig' -o -name '*.js' -o -name '*.css' \) \
  -size +20k -exec gzip -9 -k -n {} \;

rm -rf "${DEST}"
mv "${DEST}.new" "${DEST}"
echo "mapa-onibus/ ← danlessa/onibus@$(cut -c1-7 "${DEST}/SOURCE") ($(du -sh "${DEST}" | cut -f1))"
