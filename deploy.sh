#!/usr/bin/env bash
# Deploy the CURRENT working tree (committed or not) to Vercel production.
# No git commit/push involved: the Vercel CLI uploads the local files directly.
#
# Usage:  ./deploy.sh [all|backend|frontend] [--skip-checks]
#   all (default)  backend first, then frontend
#
# Safety: each project is deployed as a *staged* production build (not on the live
# domain yet), smoke-tested, and only then promoted. If a smoke test fails the
# live site is left untouched.
#
# Requirements: node/npm (uses `npx vercel`), `vercel login` done once, and the
# projects already linked (frontend/.vercel and task-backend/.vercel exist).
# Optional: BACKEND_URL=https://... to override the API URL baked into the frontend.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="all"
SKIP_CHECKS=0
for arg in "$@"; do
  case "$arg" in
    all|backend|frontend) TARGET="$arg" ;;
    --skip-checks) SKIP_CHECKS=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg (use all|backend|frontend|--skip-checks)" >&2; exit 2 ;;
  esac
done

BACKEND_URL="${BACKEND_URL:-https://capstone-api.shivsharan.me}"
BACKEND_URL="${BACKEND_URL%/}"
VERCEL=(npx --yes vercel@latest)

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v npx >/dev/null || fail "npx (Node.js) is required"
"${VERCEL[@]}" whoami >/dev/null 2>&1 || fail "Not logged in to Vercel. Run: npx vercel login"

# Deploy as a staged production build and print its unique URL.
stage() { # $1=dir, remaining args = extra vercel flags
  local dir="$1"; shift
  [[ -f "$dir/.vercel/project.json" ]] || fail "$dir is not linked to a Vercel project (run 'npx vercel link' there)"
  local out url
  out="$(cd "$dir" && "${VERCEL[@]}" deploy --prod --skip-domain --yes "$@" 2>&1)" || { echo "$out" | tail -30 >&2; fail "Vercel deploy failed in $dir"; }
  url="$(printf '%s\n' "$out" | grep -oE 'https://[a-zA-Z0-9.-]+\.vercel\.app' | grep -v '^https://vizura-[a-z]*\.vercel\.app$' | tail -1 || true)"
  [[ -n "$url" ]] || { echo "$out" | tail -30 >&2; fail "Could not find the deployment URL in the Vercel output"; }
  printf '%s' "$url"
}

promote() { # $1=dir $2=url
  (cd "$1" && "${VERCEL[@]}" promote "$2" --yes 2>&1 | tail -2)
}

# `vercel curl` works even when Deployment Protection is on.
vcurl() { # $1=dir $2=url $3=path
  (cd "$1" && "${VERCEL[@]}" curl "$3" --deployment "$2" 2>/dev/null)
}

deploy_backend() {
  local dir="$ROOT/task-backend"
  if [[ $SKIP_CHECKS -eq 0 ]]; then
    log "Backend: checking that the Vercel dependency list (requirements.txt) imports cleanly"
    command -v uv >/dev/null || fail "uv is required for backend checks (or pass --skip-checks)"
    (cd "$dir" && uv run --no-project --isolated --python 3.12 --with-requirements requirements.txt \
      python -c "import main, docx, openpyxl, reportlab; assert '/api/meetings/transcribe' in main.app.openapi()['paths']") \
      || fail "Backend does not import with requirements.txt (a package is probably missing there)"
  fi
  log "Backend: staging production build"
  local url; url="$(stage "$dir")"
  echo "staged: $url"
  log "Backend: smoke test"
  local health; health="$(vcurl "$dir" "$url" /)"
  grep -q '"status":"ok"' <<<"$health"            || fail "Backend smoke test failed (no ok status). Live site NOT changed. Response: ${health:0:300}"
  grep -q '"database_connected":true' <<<"$health" || fail "Backend cannot reach MongoDB. Live site NOT changed. Check MONGODB_URL in Vercel env."
  log "Backend: promoting to $BACKEND_URL"
  promote "$dir" "$url"
}

deploy_frontend() {
  local dir="$ROOT/frontend"
  if [[ $SKIP_CHECKS -eq 0 ]]; then
    log "Frontend: type-check, tests and build"
    (cd "$dir" && { [[ -d node_modules ]] || npm ci; } && npm test --silent && npm run build --silent) \
      || fail "Frontend checks failed"
  fi
  log "Frontend: staging production build (API = $BACKEND_URL)"
  local url; url="$(stage "$dir" --build-env "VITE_API_BASE_URL=$BACKEND_URL")"
  echo "staged: $url"
  log "Frontend: smoke test"
  local js; js="$(vcurl "$dir" "$url" / | grep -oE '/assets/[^"]+\.js' | head -1 || true)"
  [[ -n "$js" ]] || fail "Frontend smoke test failed (no JS bundle found). Live site NOT changed."
  local bundle; bundle="$(vcurl "$dir" "$url" "$js")"
  grep -qF "$BACKEND_URL" <<<"$bundle" \
    || fail "Built bundle does not contain $BACKEND_URL (would call localhost). Live site NOT changed."
  log "Frontend: promoting"
  promote "$dir" "$url"
}

case "$TARGET" in
  backend)  deploy_backend ;;
  frontend) deploy_frontend ;;
  all)      deploy_backend; deploy_frontend ;;
esac

log "Done. Live:"
# `if`, not `a && b`: when the test is false, `&&` leaves exit status 1 and the finished script would report failure.
if [[ "$TARGET" != "frontend" ]]; then echo "  backend : $BACKEND_URL"; fi
if [[ "$TARGET" != "backend"  ]]; then echo "  frontend: https://vizura-frontend.vercel.app"; fi
