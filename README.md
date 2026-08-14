# levabici

*A bici no transporte coletivo — conte como foi, veja onde rola.*

PWA estática do [Pedal Hidrográfico](https://pedalhidrografi.co) para
registrar e consultar experiências de **transportar bicicleta em meios de
transporte coletivo** (ônibus, avião, trem, barca…). Cada relato vira uma
avaliação num grafo RDF; as empresas ganham uma **nota de amigabilidade à
bici** (1–5) e aparecem ranqueadas e num mapa do Brasil.

Hospedado no GitHub Pages em **levabici.pedalhidrografi.co**.

## Telas

- **Ranking** — botão de nova avaliação + empresas ordenadas pela nota
  média de amigabilidade.
- **Mapa** — trajetos avaliados desenhados como arcos coloridos pela nota
  da empresa (escala divergente vermelho↔azul, validada para daltonismo;
  a cor nunca aparece sem o número junto).
- **Empresa** — cartão de estatísticas (nota, nº de avaliações e a
  distribuição de cada pergunta) + lista de avaliações.
- **Nova avaliação** — formulário que espelha as severidades SHACL:
  campo obrigatório faltando **bloqueia** (Violation), campo ideal
  faltando **avisa e deixa salvar** (Warning), o resto é opcional (Info).

## Arquitetura

Sem build, sem backend — a mesma receita das PWAs irmãs (`amora`,
`sampasimu`, `quilojaules`):

- `index.html` + `app.js` + `style.css` — um JS só, carregado direto.
- Leaflet e N3 **vendorados** em `lib/` (nada de CDN no caminho crítico).
- `sw.js` — service worker com `VERSION` monotônica; casca em
  stale-while-revalidate, `data/*.ttl` em network-first.

### O estado é um grafo RDF

| arquivo | papel |
|---|---|
| `data/vocab.ttl` | ontologia: classes, propriedades e escalas de resposta (SKOS). Reusa schema.org (`Review`, `Rating`, `Organization`, `Trip`, `Place`), PROV-O e SKOS; termos `lb:` só para o que é específico do domínio. |
| `data/shapes.ttl` | shapes SHACL. Convenção: `sh:Violation` = obrigatório, `sh:Warning` = ideal, `sh:Info` = bom ter; valor malformado é sempre Violation. |
| `data/reviews.ttl` | grafo de dados publicado (empresas + avaliações). |

O app parseia os três com N3.js num `N3.Store` em memória. Avaliações
criadas no aparelho ficam no `localStorage` **como Turtle** e são
mescladas ao grafo ao carregar. IRIs cunhados são determinísticos
(`…/avaliacao/<slug>_rating`, `_trip`, `_trip_from`, `_trip_to`) — nunca
blank nodes — na convenção do ecossistema.

As avaliações da semente são **exemplos ilustrativos**
(`lb:isExample true`, selo "exemplo" na interface): as empresas são
reais, as experiências não. Substitua por relatos reais.

### Contribuindo dados

Não há backend: o botão **exportar dados (.ttl)** baixa o grafo completo
(semente + suas avaliações). Mande o arquivo pro coletivo (ou abra um PR
atualizando `data/reviews.ttl`) para seus relatos entrarem no grafo
publicado. Geocodificação de partida/chegada é melhor-esforço via
Nominatim na hora de salvar; sem coordenadas o relato só não aparece no
mapa.

## Rodando local

```sh
python3 -m http.server 8000
# http://localhost:8000
```

## Verificações antes de publicar

```sh
node --check app.js && node --check sw.js
python3 -c "import rdflib; [rdflib.Graph().parse(f) for f in
  ('data/vocab.ttl','data/shapes.ttl','data/reviews.ttl')]"
python3 -m pyshacl -s data/shapes.ttl -e data/vocab.ttl data/reviews.ttl
```

E **suba a `VERSION` do `sw.js`** em qualquer mudança de arquivo servido.

## Deploy

GitHub Pages servindo a raiz da branch `main` (o `CNAME` aponta
`levabici.pedalhidrografi.co`; DNS na Cloudflare com `CNAME` para
`pedalhidro.github.io`). Publicar = fazer push.

## Licença

GPL-3.0 — ver `LICENSE`.
