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

COPY backend/main.py backend/storage.py ./

# O app estático inteiro (o .dockerignore poda .git, local-state, etc.).
COPY . ./web/

ENV LEVABICI_WEB=/app/web \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# --workers 1: as mutações do grafo assumem lock por processo (house rule);
# Cloud Run escala por instância (max-instances=1 no deploy.sh).
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 4 \
      --timeout 60 --access-logfile - main:app
