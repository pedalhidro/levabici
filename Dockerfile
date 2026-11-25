# levabici — container do backend (mesma receita do amora).
#   • Cloud Run:  STORAGE_BACKEND=gcs + GCS_BUCKET
#   • Local:      docker run --rm -p 8613:8080 levabici   (STORAGE_BACKEND=local)
# Em produção o build é via `gcloud run deploy --source=.` (Cloud Build).

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/main.py backend/storage.py backend/map_operators.py ./

# O app estático inteiro (o .dockerignore poda .git, local-state, etc.).
COPY . ./web/

# Mapa dos ônibus (web/mapa-onibus/, fora do git: quem traz é o
# tools/fetch_onibus_map.sh, que o deploy.sh roda antes). Extrai a lista
# de empresas do mapa UMA vez aqui — ~3 s de rdflib que não cabem em todo
# cold start. Sem a pasta o build FALHA, de propósito: melhor que subir
# o serviço sem o mapa.
RUN python map_operators.py web/mapa-onibus

ENV LEVABICI_WEB=/app/web \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# --workers 1: as mutações do grafo assumem lock por processo (house rule);
# Cloud Run escala por instância (max-instances=1 no deploy.sh).
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 \
      --timeout 60 --access-logfile - main:app
