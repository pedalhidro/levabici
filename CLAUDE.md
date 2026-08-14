# levabici — instruções para assistentes

Leia o `README.md` primeiro; o `../CLAUDE.md` (mapa do workspace) também
vale aqui. Este arquivo guarda só os invariantes deste repo.

## Invariantes

- **PWA estática sem build.** Um `app.js` só, carregado direto pelo
  `index.html`. Nada de bundler, `package.json`, framework. Leaflet e N3
  são vendorados em `lib/` — não trocar por CDN.
- **`sw.js` `VERSION`**: subir a cada mudança em arquivo servido, senão
  usuários ficam com cache velho. Convenção `levabici-vN`, monotônica.
- **O grafo é a fonte de verdade.** Interface toda derivada do
  `N3.Store` (vocab + semente + localStorage). Rótulos e opções do
  formulário vêm de `data/vocab.ttl` (`rdfs:label`, `skos:prefLabel`,
  `lb:ordinal`) — não duplicar strings de domínio no JS.
- **Severidade SHACL**: `Violation` = obrigatório (bloqueia o envio),
  `Warning` = ideal (avisa, deixa salvar), `Info` = bom ter. Presença e
  boa-formação são shapes separados em `data/shapes.ttl`; valor
  malformado é sempre Violation. `validateForm()`/`FORM_CONSTRAINTS`
  em `app.js` espelham as shapes — mudou shape, muda o espelho.
- **IRIs**: vocab `https://id.pedalhidrografi.co/levabici/terms#`,
  instâncias `…/levabici/empresa/<slug>` e `…/levabici/avaliacao/<slug>`.
  Nós aninhados = IRIs determinísticos `<pai>_rating` / `_trip` /
  `_trip_from` / `_trip_to` — nunca blank nodes.
- **Escala de cor da nota** (1→5 `#b32424 #ef8888 #7a776f #6da7ec
  #1c5cab`): divergente vermelho↔azul, **validada** com o
  `validate_palette.js` do método de dataviz (braços ordinais OK, pior
  par adjacente CVD ΔE 11,4). Não trocar sem revalidar; nunca
  vermelho↔verde. A cor nunca aparece sem o número junto.
- **Semente é exemplo.** Avaliações ilustrativas levam
  `lb:isExample true` e corpo começando com "Exemplo ilustrativo:" — a
  interface mostra o selo. Não criar relatos fictícios sem essa marca.
- **UI em português, identificadores em inglês.** App só em PT (sem
  tabela i18n, como o amora).

## Verificar antes de terminar

1. `node --check app.js && node --check sw.js`
2. TTLs: parsear com `rdflib`; `pyshacl -s data/shapes.ttl -e
   data/vocab.ttl data/reviews.ttl` → zero Violations (Warnings/Infos
   da semente são esperados: fotos/etc.).
3. Abrir num navegador de verdade (`python3 -m http.server`) e passar
   pelas quatro telas. Headless: janela mínima do Chrome no macOS é
   ~500px — screenshot de 390 sai cortado; não é overflow do layout.
4. Mudou arquivo servido → `sw.js` `VERSION` +1.

## Commits

Neste repo, **commit + push por padrão** ao concluir um trabalho (pedido
do Danilo, 2026-08). Mensagens em inglês, autoria padrão do usuário.
