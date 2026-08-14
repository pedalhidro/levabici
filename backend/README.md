# backend — levabici auto-hospedado

Um único serviço Flask que **serve o app estático e mantém o grafo de
avaliações**, sem SQLite — mesma receita do `amora/backend`. Roda igual
num host local e no Cloud Run; `STORAGE_BACKEND` escolhe onde vive o
estado (filesystem ou bucket GCS).

```text
gunicorn → main.py (Flask, --workers 1)
  ├─ GET  /                      → index.html (o app)
  ├─ GET  /<path>                → estáticos do repo
  ├─ GET  /health, /api/health   → liveness (o app usa /api/health pra
  │                                decidir entre modo API e modo local)
  ├─ GET  /data/reviews.ttl      → o grafo VIVO (bucket-first; semeado
  │                                do container no primeiro boot)
  ├─ POST /api/reviews           → cria 1 avaliação (text/turtle)
  ├─ PUT  /api/reviews/<slug>    → substitui a subárvore da avaliação
  └─ DELETE /api/reviews/<slug>  → remove a subárvore
```

## Moderação estilo wiki

Sem auth, por desenho (como todo o ecossistema): **qualquer pessoa cria,
edita e apaga**. As proteções são:

1. **Portão SHACL** — toda mutação valida o grafo resultante contra
   `data/shapes.ttl` (+ `vocab.ttl`); qualquer `sh:Violation` → **422**
   com as mensagens. Warnings/Infos passam (mesma semântica do
   formulário).
2. **Escopo do payload** — os sujeitos precisam ser a própria avaliação,
   seus filhos determinísticos (`<iri>_rating`, `_trip`, …) ou uma
   empresa (`emp:`); tentar tocar em avaliação alheia → **400**.
3. **Histórico** — o bucket tem **versionamento de objetos ligado**
   (deploy.sh garante): toda escrita de `reviews.ttl` vira uma geração
   recuperável, como o histórico de um wiki.

Empresas nunca são apagadas em cascata (ficam como "páginas vazias":
somem do ranking, permanecem no seletor do formulário).

### Ver e restaurar o histórico

```sh
BUCKET=levabici-pedalhidrografico
# listar todas as gerações (o histórico)
gcloud storage ls -a "gs://${BUCKET}/reviews.ttl"
# inspecionar uma geração específica
gcloud storage cat "gs://${BUCKET}/reviews.ttl#GENERATION"
# restaurar (reverter vandalismo): copia a geração antiga por cima
gcloud storage cp "gs://${BUCKET}/reviews.ttl#GENERATION" "gs://${BUCKET}/reviews.ttl"
```

A restauração também é uma escrita versionada — nada se perde nunca.

## Rodando local

```sh
pip install -r backend/requirements.txt
cd backend && LEVABICI_STATE=../local-state PORT=8613 python3 main.py
# http://localhost:8613 — estado em local-state/reviews.ttl
```

`rm local-state/reviews.ttl` re-semeia do `data/reviews.ttl` do repo.

## Deploy

`./deploy.sh` (na raiz): cria/atualiza o bucket **com versionamento**,
e sobe o serviço `levabici` no Cloud Run (projeto `pedal-hidrografico`,
`southamerica-east1`, `--max-instances 1` — o locking das mutações é por
processo, não subir sem repensar). DNS: apontar
`levabici.pedalhidrografi.co` (Cloudflare) pro serviço.

## Concorrência e consistência

- `--workers 1` + `threading.RLock` em todo read-modify-write; pyshacl
  roda sob lock próprio (não é thread-safe).
- Nenhum cache do grafo em memória: cada GET lê do store — bucket
  restaurado na mão aparece na hora, sem endpoint de reload.
- `storage.py` é cópia de `amora/backend/storage.py` — corrigiu bug num,
  corrige no outro.
