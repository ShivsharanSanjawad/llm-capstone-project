#!/usr/bin/env bash
# Copy named settings from task-backend/.env to the Vercel backend project (Production), replacing any old value.
# The values are read from the file and passed straight to Vercel; they are never printed.
# Usage:  ./set-vercel-env-from-dotenv.sh GROQ_API_KEY            then redeploy:  ./deploy.sh backend
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/task-backend/.env"
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }
[[ $# -gt 0 ]] || { echo "Usage: $0 NAME [NAME...]" >&2; exit 2; }

value_of() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

cd "$ROOT/task-backend"
for name in "$@"; do
  value="$(value_of "$name")"
  [[ -n "$value" ]] || { echo "$name is empty or missing in task-backend/.env" >&2; exit 1; }
  npx --yes vercel@latest env add "$name" production --value "$value" --force --sensitive --yes >/dev/null 2>&1 \
    && echo "set $name (ends ...${value: -4})" || { echo "FAILED to set $name" >&2; exit 1; }
done
echo "Done. Redeploy so the backend picks them up: ./deploy.sh backend"
