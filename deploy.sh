#!/usr/bin/env bash
# Deploy do levabici no Cloud Run + bucket GCS versionado.
# Idempotente: pode rodar de novo à vontade.
set -euo pipefail

PROJECT=pedal-hidrografico
REGION=southamerica-east1
SERVICE=levabici
BUCKET=levabici-pedalhidrografico

# Bucket do estado (reviews.ttl). VERSIONAMENTO LIGADO = histórico estilo
# wiki: toda escrita vira uma geração recuperável (ver backend/README.md).
if ! gcloud storage buckets describe "gs://${BUCKET}" --project "${PROJECT}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" \
    --project "${PROJECT}" --location "${REGION}" \
    --uniform-bucket-level-access
fi
gcloud storage buckets update "gs://${BUCKET}" --versioning --project "${PROJECT}"

# max-instances=1: as mutações do grafo assumem uma instância só (lock por
# processo, mesmo desenho do amora). Não subir sem repensar o locking.
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --max-instances 1 \
  --memory 512Mi \
  --set-env-vars "STORAGE_BACKEND=gcs,GCS_BUCKET=${BUCKET}"

echo
echo "Pronto. DNS: aponte levabici.pedalhidrografi.co (Cloudflare) pro serviço"
echo "Cloud Run '${SERVICE}' (domain mapping ou Worker de proxy, como o amora)."
