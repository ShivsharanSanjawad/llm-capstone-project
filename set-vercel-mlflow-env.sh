#!/usr/bin/env bash
# Copy the three MLflow settings from task-backend/.env into the Vercel backend project (Production).
# The values are read from the file and sent straight to Vercel; they are never printed.
# Run it yourself:  ./set-vercel-mlflow-env.sh      then redeploy:  ./deploy.sh backend
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/task-backend/.env"
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }

value_of() { # last assignment of NAME in the file, quotes and CR stripped
  grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

cd "$ROOT/task-backend"
for name in MLFLOW_TRACKING_URI MLFLOW_TRACKING_USERNAME MLFLOW_TRACKING_PASSWORD; do
  value="$(value_of "$name")"
  [[ -n "$value" ]] || { echo "$name is empty or missing in task-backend/.env. Add it first." >&2; exit 1; }
  npx --yes vercel@latest env rm "$name" production --yes >/dev/null 2>&1 || true   # replace an older value if there is one
  printf '%s' "$value" | npx --yes vercel@latest env add "$name" production >/dev/null
  echo "set $name"
done
echo "Done. Redeploy so the backend picks them up: ./deploy.sh backend"
