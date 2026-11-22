# Deployment runbook

Two interchangeable backends. The frontend picks one via `VITE_API_BASE_URL`.

| | Vercel (default) | GCP Cloud Run (optional) |
|---|---|---|
| Backend URL | https://capstone-api.shivsharan.me (or vizura-backend.vercel.app) | https://capstone-gcp.shivsharan.me (or the `*.run.app` URL from `./gcp.sh url`) |
| Audio transcription | No (too heavy for serverless) | **Yes** (faster-whisper baked into the image) |
| Cost | free tier | scales to zero; pay only while a request runs |
| Deploy | `./deploy.sh backend` | `./gcp.sh deploy` |

Frontend: https://capstone-app.shivsharan.me (also https://vizura-frontend.vercel.app). Deploy with `./deploy.sh frontend`.
To point the frontend at GCP: `BACKEND_URL=https://capstone-gcp.shivsharan.me ./deploy.sh frontend`
To point it back at Vercel: `./deploy.sh frontend` (default API is https://capstone-api.shivsharan.me).
Chrome extension: edit `Extension/config.js` endpoints to the backend you want.

Nothing here commits or pushes to git; the scripts upload the local working tree.

## Cloudflare DNS records (shivsharan.me)

Set every record to **DNS only (grey cloud)**. Vercel and Cloud Run issue their own certificates.

| Type | Name | Target | Purpose |
|---|---|---|---|
| CNAME | `capstone-app` | `3d6c013c6bf9d87f.vercel-dns-017.com` | frontend on Vercel |
| CNAME | `capstone-api` | `b9a951d74e55bac0.vercel-dns-017.com` | backend on Vercel |
| CNAME | `capstone-gcp` | `ghs.googlehosted.com` | backend on Cloud Run (after `./gcp.sh domain`) |

## GCP: first-time setup

```bash
gcloud auth login                       # token had expired
gcloud config set project <PROJECT_ID>  # a project with billing enabled
./gcp.sh setup                          # enable APIs, build the image locally with Docker, push, deploy
./gcp.sh domain                         # map capstone-gcp.shivsharan.me, prints DNS record
```
`gcp.sh domain` may ask you to verify ownership of shivsharan.me once at
https://search.google.com/search-console (TXT record in Cloudflare, same Google account as gcloud).

Settings (MONGODB_URL, GROQ_API_KEY, ...) are copied from `task-backend/.env` into the service's environment.
The image is built locally with Docker, so no Cloud Build / IAM-admin permissions are needed.

**Public access needs an IAM admin.** The service is deployed but only callable with a Google identity token until
someone with `run.services.setIamPolicy` (Owner / Cloud Run Admin) runs:
`gcloud run services add-iam-policy-binding vizura-backend --region asia-south1 --member=allUsers --role=roles/run.invoker`
(or uses a project where you are Owner). Browsers and the Chrome extension can't send identity tokens.

Day to day: `./gcp.sh deploy`, `./gcp.sh stop` (blocks all traffic), `./gcp.sh start`, `./gcp.sh logs`.

## Vercel notes

* Production env vars on the backend project: `MONGODB_URL`, `GROQ_API_KEY`, `FRONTEND_ORIGINS`, ...
  CORS also allows https://*.shivsharan.me in code, so new subdomains need no env change.
* `./deploy.sh` deploys a staged build, smoke-tests it, then promotes. A failed test never touches the live site.
* Rollback: `npx vercel rollback <previous deployment url>`.

## Auto-deploy pipeline (GitHub Actions)

`.github/workflows/deploy.yml` deploys to Vercel production whenever `main` changes, and `ci.yml` checks pull requests
(frontend tests + build, backend tests, `requirements.txt` import check). Both run the same steps as `./deploy.sh`:
test, stage a production build, smoke-test it, then promote. A failed check never changes the live site.

* Only what changed is deployed: `task-backend/**` redeploys the backend, `frontend/**` the frontend, both for
  `deploy.sh` or the workflow itself, and a push that touches neither (docs, extension) deploys nothing.
* Run it by hand: GitHub, Actions, Deploy, "Run workflow", then choose all, backend or frontend.
* Two deploys never run at once (the second waits).
* The workflow needs `deploy.sh` committed, since it runs that script.

### One-time setup

1. Create a Vercel access token at https://vercel.com/account/tokens (scope: your team).
2. Add four repository secrets (Settings, Secrets and variables, Actions). With the GitHub CLI, from the repo root:

```bash
gh secret set VERCEL_TOKEN                       # paste the token when prompted
gh secret set VERCEL_ORG_ID              --body "$(python3 -c "import json;print(json.load(open('frontend/.vercel/project.json'))['orgId'])")"
gh secret set VERCEL_FRONTEND_PROJECT_ID --body "$(python3 -c "import json;print(json.load(open('frontend/.vercel/project.json'))['projectId'])")"
gh secret set VERCEL_BACKEND_PROJECT_ID  --body "$(python3 -c "import json;print(json.load(open('task-backend/.vercel/project.json'))['projectId'])")"
```

3. Optional: a repository *variable* `BACKEND_URL` to point the website at another backend (defaults to
   https://capstone-api.shivsharan.me).

If a secret is missing the workflow stops at its first step and says which one.

### Notes

* The Vercel projects are not connected to Git, so only this workflow deploys; there are no duplicate builds.
* The test step runs the whole backend suite (no exclusions). Tests are forced to use an in-memory database and can never reach your real Atlas.
* Backend environment variables (MONGODB_URL, GROQ_API_KEY, ...) stay in the Vercel project settings, not in GitHub.
* Rollback is still `npx vercel rollback <previous deployment url>`.
