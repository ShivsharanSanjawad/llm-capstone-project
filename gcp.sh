#!/usr/bin/env bash
# Run the FULL backend (including audio transcription) on Google Cloud Run.
# Cloud Run scales to zero, so it costs ~nothing while idle. Vercel is NOT touched.
#
#   ./gcp.sh setup     one-time: enable APIs and do the first deploy (settings come from task-backend/.env)
#   ./gcp.sh deploy    build + deploy the CURRENT local code (no git needed)
#   ./gcp.sh start     open the service to the public (default state after deploy)
#   ./gcp.sh stop      close it: all requests get 403, so nothing can bill (data in Atlas is safe)
#   ./gcp.sh url       print the service URL (use it as VITE_API_BASE_URL / extension endpoint)
#   ./gcp.sh domain    map capstone-gcp.shivsharan.me to the service and print the DNS records for Cloudflare
#   ./gcp.sh status | logs
#
# First, once:  gcloud auth login   (and pick a project: gcloud config set project <ID>)
# Overrides (env): GCP_PROJECT, GCP_REGION (default asia-south1), GCP_SERVICE (default vizura-backend),
#                  GCP_DOMAIN (default capstone-gcp.shivsharan.me), GCP_MEMORY (default 2Gi), GCP_CPU (default 2)

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$ROOT/task-backend"
SERVICE="${GCP_SERVICE:-vizura-backend}"
REGION="${GCP_REGION:-asia-south1}"
DOMAIN="${GCP_DOMAIN:-capstone-gcp.shivsharan.me}"
MEMORY="${GCP_MEMORY:-2Gi}"
CPU="${GCP_CPU:-2}"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null || fail "gcloud is not installed"
gcloud auth print-access-token >/dev/null 2>&1 || fail "gcloud login expired. Run: gcloud auth login"
[[ -n "$PROJECT" ]] || fail "No GCP project. Run: gcloud config set project <PROJECT_ID>  (or export GCP_PROJECT)"
G=(gcloud --project "$PROJECT")

service_url() { "${G[@]}" run services describe "$SERVICE" --region "$REGION" --format='value(status.url)' 2>/dev/null; }

# Settings copied from task-backend/.env into the Cloud Run service's environment.
# (Secret Manager is NOT used: it needs IAM-admin rights, which restricted/school projects often deny.)
ENV_KEYS=(MONGODB_URL GROQ_API_KEY GEMINI_API_KEY OPENAI_API_KEY MINUTES_LLM_PROVIDER MINUTES_LLM_MODEL LLM_MODEL MINUTES_TOKENS_PER_MINUTE MINUTES_REQUESTS_PER_MINUTE MINUTES_STORAGE_MODE MINUTES_ENABLE_VERIFICATION)

envval() { # read KEY from task-backend/.env, strip quotes/whitespace/control chars
  local v; v="$(grep -E "^$1=" "$DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v" | tr -d '\r\v\f' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

cmd_setup() {
  log "Enabling APIs (Cloud Run, Artifact Registry)"
  "${G[@]}" services enable run.googleapis.com artifactregistry.googleapis.com
  cmd_deploy
}

cmd_deploy() {
  command -v docker >/dev/null || fail "docker is required (the image is built locally, so no Cloud Build permissions are needed)"
  [[ -f "$DIR/.env" ]] || fail "$DIR/.env not found (needed to copy MONGODB_URL / API keys into the service)"
  local host="$REGION-docker.pkg.dev" repo="cloud-run-source-deploy"
  "${G[@]}" artifacts repositories describe "$repo" --location "$REGION" >/dev/null 2>&1 \
    || "${G[@]}" artifacts repositories create "$repo" --repository-format=docker --location "$REGION" >/dev/null
  local image="$host/$PROJECT/$repo/$SERVICE:$(date +%Y%m%d-%H%M%S)"

  log "Building image locally"
  docker build -t "$image" "$DIR"
  log "Pushing image"
  gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin "https://$host" >/dev/null
  docker push "$image"

  local vars=() k v
  for k in "${ENV_KEYS[@]}"; do v="$(envval "$k")"; [[ -n "$v" ]] && vars+=("$k=$v"); done
  # Allow the Vercel frontend and the custom domain (the '^##^' prefix changes gcloud's list separator).
  vars+=("FRONTEND_ORIGINS=https://vizura-frontend.vercel.app,https://capstone-app.shivsharan.me,https://shivsharan.me,http://localhost:5173")
  local joined; joined="$(IFS='#'; printf '%s' "${vars[*]}" | sed 's/#/##/g')"

  log "Deploying $SERVICE to $REGION (project $PROJECT)"
  "${G[@]}" run deploy "$SERVICE" \
    --image "$image" --region "$REGION" \
    --memory "$MEMORY" --cpu "$CPU" --cpu-boost \
    --min-instances 0 --max-instances 2 --concurrency 4 --timeout 900 \
    --port 8080 \
    --set-env-vars "^##^$joined" \
    --allow-unauthenticated --quiet

  local url; url="$(service_url)"
  log "Smoke test: $url"
  local body; body="$(curl -fsS -m 90 "$url/" || true)"
  grep -q '"status":"ok"' <<<"$body" || fail "Service did not answer healthy. See: ./gcp.sh logs"
  grep -q '"database_connected":true' <<<"$body" || echo "WARNING: database not connected (check MONGODB_URL)"
  echo "Healthy. Backend URL: $url"
  echo "Audio route: $url/api/meetings/transcribe"
}

cmd_start() {
  "${G[@]}" run services add-iam-policy-binding "$SERVICE" --region "$REGION" --member=allUsers --role=roles/run.invoker >/dev/null
  echo "started (public). URL: $(service_url)"
}
cmd_stop() {
  "${G[@]}" run services remove-iam-policy-binding "$SERVICE" --region "$REGION" --member=allUsers --role=roles/run.invoker >/dev/null
  echo "stopped: public access removed, requests now get 403 and cost nothing. './gcp.sh start' re-opens it."
}

cmd_domain() {
  log "Mapping $DOMAIN -> $SERVICE"
  "${G[@]}" beta run domain-mappings create --service "$SERVICE" --domain "$DOMAIN" --region "$REGION" 2>&1 || true
  echo
  echo "Add this record in Cloudflare (DNS only / grey cloud, until the certificate is issued):"
  "${G[@]}" beta run domain-mappings describe --domain "$DOMAIN" --region "$REGION" \
    --format='table(status.resourceRecords.type,status.resourceRecords.name,status.resourceRecords.rrdata)' 2>&1 || true
  echo
  echo "If it says the domain is not verified, verify shivsharan.me once at https://search.google.com/search-console"
  echo "(add the TXT record it gives you in Cloudflare) using the SAME Google account as gcloud, then re-run this command."
}

case "${1:-}" in
  setup)  cmd_setup ;;
  deploy) cmd_deploy ;;
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  url)    service_url ;;
  domain) cmd_domain ;;
  status) "${G[@]}" run services describe "$SERVICE" --region "$REGION" --format='yaml(status.url,status.conditions,spec.template.spec.containers[0].resources)' ;;
  logs)   "${G[@]}" run services logs read "$SERVICE" --region "$REGION" --limit 80 ;;
  *) sed -n 2,17p "$0"; exit 2 ;;
esac
