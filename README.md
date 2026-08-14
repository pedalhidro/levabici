# levabici

*A bici no transporte coletivo — conte como foi, veja onde rola.*

App do [Pedal Hidrográfico](https://pedalhidrografi.co) para registrar e
consultar experiências de **transportar bicicleta em meios de transporte
coletivo** (ônibus, avião, trem, barca…). Cada relato vira uma avaliação
num grafo RDF compartilhado; as empresas ganham uma **nota de
amigabilidade à bici** (1–5) e aparecem ranqueadas e num mapa.

Ao vivo em **levabici.pedalhidrografi.co** (Cloud Run). O grafo funciona
**como um wiki**: qualquer pessoa cria, edita e apaga avaliações; toda
mudança fica no histórico de versões do bucket, e vandalismo se reverte
restaurando uma geração anterior (ver `backend/README.md`).

## Telas

- **Ranking** — botão de nova avaliação + empresas ordenadas pela nota
  média de amigabilidade.
- **Mapa** — trajetos avaliados desenhados como arcos coloridos pela nota
  da empresa (escala divergente vermelho↔azul validada para daltonismo;
  a cor nunca aparece sem o número junto).
- **Empresa** — cartão de estatísticas (nota, nº de avaliações e a
  distribuição de cada pergunta) + avaliações com **editar / apagar**
  (e **publicar**, para relatos guardados offline).
- **Nova avaliação / edição** — formulário que espelha as severidades
  SHACL: obrigatório faltando **bloqueia** (Violation), ideal faltando
  **avisa e deixa salvar** (Warning), o resto é opcional (Info).

## Arquitetura

Frontend na receita das PWAs irmãs (`amora`, `sampasimu`, `quilojaules`):
sem build, um `app.js` só, Leaflet e N3 vendorados em `lib/`, `sw.js` com
`VERSION` monotônica. Backend na receita do `amora/backend`: um Flask
que serve o app **e** mantém o estado como Turtle — local no filesystem,
no Cloud Run num bucket GCS **com versionamento de objetos** (o
histórico-de-wiki). Detalhes e rotas: `backend/README.md`.

### O estado é um grafo RDF

| arquivo | papel |
|---|---|
| `data/vocab.ttl` | ontologia: classes, propriedades e escalas de resposta (SKOS). Reusa schema.org (`Review`, `Rating`, `Organization`, `Trip`, `Place`), PROV-O e SKOS. |
| `data/shapes.ttl` | shapes SHACL. `sh:Violation` = obrigatório, `sh:Warning` = ideal, `sh:Info` = bom ter; valor malformado é sempre Violation. **É o portão de escrita do backend** (mutação com Violation → 422). |
| `data/reviews.ttl` | a SEMENTE do grafo (empresas + avaliações). No primeiro boot o backend copia ela pro bucket; daí em diante o grafo vivo mora lá e esta cópia é só bootstrap/fallback. |

O app parseia tudo com N3.js num `N3.Store`. Com o backend no ar
(`/api/health`), criar/editar/apagar vai direto pro grafo compartilhado;
sem conexão, a avaliação fica no `localStorage` **como Turtle**, marcada
"só neste aparelho", com um botão **publicar** para quando a conexão
voltar. IRIs cunhados são determinísticos
(`…/avaliacao/<slug>_rating`, `_trip`, `_trip_from`, `_trip_to`) — nunca
blank nodes — o que torna editar/apagar uma troca limpa de subárvore.

### Semente: WikiVoyage

As avaliações iniciais (35 empresas de ônibus) são derivadas do artigo
[Bicicletas como Bagagem em Ônibus Rodoviários do
Brasil](https://pt.wikivoyage.org/wiki/Bicicletas_como_Bagagem_em_%C3%94nibus_Rodovi%C3%A1rios_do_Brasil)
(**CC BY-SA 4.0** — cada avaliação carrega `prov:wasDerivedFrom` e a
interface mostra o selo "fonte"). A nota 1–5 vem da coluna "soma" (0–7)
do próprio artigo. Geração reproduzível:
`python3 tools/import_wikivoyage.py > data/reviews.ttl` — a transcrição
da tabela e os mapeamentos estão documentados no script.

## Rodando local

```sh
# só o frontend (modo local, sem grafo compartilhado):
python3 -m http.server 8000
# app + backend (grafo em local-state/):
pip install -r backend/requirements.txt
cd backend && LEVABICI_STATE=../local-state PORT=8613 python3 main.py
```

## Verificações antes de publicar

```sh
node --check app.js && node --check sw.js
python3 -m py_compile backend/main.py backend/storage.py
python3 -m pyshacl -s data/shapes.ttl -e data/vocab.ttl data/reviews.ttl
```

E **suba a `VERSION` do `sw.js`** em qualquer mudança de arquivo servido.

## Deploy

`./deploy.sh` — cria o bucket (versionado) e sobe o Cloud Run (projeto
`pedal-hidrografico`, região `southamerica-east1`). DNS na Cloudflare:
`levabici.pedalhidrografi.co` → serviço `levabici`. O GitHub Pages pode
seguir servindo o app como espelho estático (funciona em modo só-local).

## Licença

Código GPL-3.0 (`LICENSE`). Conteúdo derivado do WikiVoyage em
`data/reviews.ttl`: CC BY-SA 4.0.
