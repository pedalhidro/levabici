# levabici — instruções para assistentes

Leia o `README.md` primeiro; o `../CLAUDE.md` (mapa do workspace) também
vale aqui. Este arquivo guarda só os invariantes deste repo.

## Invariantes

- **PWA estática sem build + backend Flask** (receita do amora). Um
  `app.js` só; Leaflet e N3 vendorados em `lib/` — não trocar por CDN.
  `backend/main.py` serve o app e mantém o grafo; `backend/storage.py`
  é cópia de `amora/backend/storage.py` (bug corrigido num, corrige no
  outro).
- **`sw.js` `VERSION`**: subir a cada mudança em arquivo servido
  (`levabici-vN`, monotônica). `/api/` e `/health` NUNCA passam pelo
  cache do service worker.
- **O grafo é a fonte de verdade.** Interface toda derivada do
  `N3.Store` (vocab + grafo publicado + localStorage). Rótulos e opções
  do formulário vêm de `data/vocab.ttl` — não duplicar strings de
  domínio no JS.
- **Severidade SHACL**: `Violation` = obrigatório, `Warning` = ideal,
  `Info` = bom ter. Vale no formulário (`validateForm()`) E no backend
  (mutação que introduz Violation → 422). Mudou shape, muda o espelho
  do formulário — o backend valida as shapes de verdade.
- **Moderação estilo wiki, sem auth (por desenho).** Qualquer pessoa
  cria/edita/apaga; as proteções são o portão SHACL, o escopo de
  sujeitos do payload e o VERSIONAMENTO do bucket (histórico
  recuperável — comandos em `backend/README.md`). Não reintroduzir
  tokens/login.
- **IRIs**: vocab `https://id.pedalhidrografi.co/levabici/terms#`,
  instâncias `…/levabici/empresa/<slug>` e `…/levabici/avaliacao/<slug>`.
  Nós aninhados = IRIs determinísticos `<pai>_rating` / `_trip` /
  `_trip_from` / `_trip_to` — nunca blank nodes (é o que faz
  editar/apagar ser troca de subárvore). Na edição o IRI da avaliação é
  PRESERVADO, e `prov:generatedAtTime`/`prov:wasDerivedFrom` sobrevivem;
  o backend carimba `dcterms:modified`.
- **`data/reviews.ttl` = SNAPSHOT do grafo vivo** (arquivado do bucket
  com `gcloud storage cp`; já contém avaliações da comunidade — NÃO
  regenerar por cima, senão elas somem). `tools/import_wikivoyage.py`
  gerou só a semente original derivada do WikiVoyage (CC BY-SA 4.0;
  atribuição via `prov:wasDerivedFrom` + selo "fonte" na interface; a
  nota 1–5 deriva da coluna "soma" 0–7 do artigo). Pra atualizar dados
  do wiki hoje: editar/apagar avaliações `av:wikivoyage-*` no grafo
  vivo (API ou bucket), não regenerar o arquivo.
- **Escala de cor da nota** (1→5 `#90272d #a35303 #a37d1e #9fa531
  #6bc87b`): vermelho (pior) → verde (melhor) — DECISÃO do Danilo
  (2026-08, sobrepondo o "nunca vermelho↔verde" do método de dataviz).
  A salvaguarda é a interpolação em OKLCH com LUMINOSIDADE MONOTÔNICA
  (L 0.44→0.755): a ordem fica legível pra daltonismo pelo claro/escuro
  mesmo sem distinguir as matizes. Extremos com ≥2:1 de contraste nos
  DOIS temas e ΔL adjacente ≥0.06 (checado com o `validate_palette.js`;
  o check "single hue" falha de propósito — rampa multi-matiz). Não
  trocar sem revalidar. A cor NUNCA aparece sem o número junto (é o
  que segura os pares adjacentes na banda 6–8 de CVD). As barrinhas de
  atrito usam AZUIS pra não disputar com a rampa da nota.
- **Cloud Run com `--max-instances 1` e gunicorn `--workers 1`**: todo o
  locking de mutação é por processo. Não subir nenhum dos dois sem
  repensar a concorrência.
- **UI em português, identificadores em inglês.**

## Verificar antes de terminar

1. `node --check app.js && node --check sw.js` ;
   `python3 -m py_compile backend/main.py backend/storage.py`
2. TTLs: `python3 -m pyshacl -s data/shapes.ttl -e data/vocab.ttl
   data/reviews.ttl` → zero Violations (Warnings/Infos são esperados).
3. Backend local (`cd backend && LEVABICI_STATE=../local-state
   PORT=8613 python3 main.py`) e passar pelas telas num navegador de
   verdade — inclusive criar/editar/apagar. Headless no macOS: janela
   mínima do Chrome é ~500px; screenshot de 390 sai cortado — não é
   overflow (confira com `document.documentElement.scrollWidth`).
4. Mudou arquivo servido → `sw.js` `VERSION` +1.

## Commits & deploy

Neste repo, **commit + push + deploy (`./deploy.sh`) por padrão** ao
concluir um trabalho (pedido do Danilo, 2026-08). Mensagens em inglês,
autoria padrão do usuário. Lembrete: mudou arquivo servido → a VERSION
do sw.js já deve ter subido antes do commit.
