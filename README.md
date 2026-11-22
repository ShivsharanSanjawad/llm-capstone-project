# Meeting Minutes & Task Intelligence System

An end-to-end meeting intelligence system that captures live transcripts through a Chrome Extension or accepts uploaded and pasted transcripts through a React website. It extracts factual action items, infers contextual task details, analyzes dependencies and blockers, generates evidence-linked meeting minutes, and supports human review, approval, revision history, PDF/Excel/DOCX export, meeting Q&A, and LLM monitoring.

## Live Deployment

- **Web app:** https://capstone-app.shivsharan.me (also https://vizura-frontend.vercel.app)
- **Backend API:** https://capstone-api.shivsharan.me (also https://vizura-backend.vercel.app); health check at `/`, Swagger at `/docs`
- **Chrome extension:** load the `Extension/` folder as an unpacked extension; `Extension/config.js` points it at the deployed backend by default.
- **Audio upload** needs the speech model, which does not fit on Vercel. It works on a local backend or on the optional Docker/GCP backend (see [Deployment](#deployment)).

---

## Architecture Overview

```mermaid
graph LR
    Extension[Chrome Side Panel Extension] -->|"POST /api/v1/extract-factual"| Backend[FastAPI Backend Engine]
    Extension -->|"POST /api/v1/infer-context/{meeting_id}"| Backend
    Backend -->|"Pass 1: Strict Factual Extraction"| LLM["LLM: Gemini / Groq / OpenAI"]
    Backend -->|"Pass 2: Contextual Inference"| LLM
    Backend -->|"Pass 3: Dependency Graph Analysis"| NetworkX["NetworkX Graph Engine"]
    Backend -->|"Persistent Storage"| DB[("MongoDB Atlas / Local JSON")]
    Backend -->|"Tasks + Feasibility + Bottlenecks"| Extension
```

1. **Frontend (Browser Extension)**:
   - Manifest V3 Side Panel extension (`Extension/`).
   - Captures speech in real-time with timestamps.
   - Extracts factual tasks from the transcript and displays the meeting title and task descriptions.
   - Runs contextual inference and graph analysis on request, then displays priorities, deadlines, and schedule bottlenecks.

2. **Backend (`task-backend/`)**:
   - **FastAPI** with CORS enabled for extension and web frontends.
   - **Pass 1 (`/api/v1/extract-factual`)**: Strict factual extraction (only tasks explicitly spoken aloud); returns a meeting ID.
   - **Pass 2 (`/api/v1/infer-context/{meeting_id}`)**: Optional AI Project Manager inference (priorities, missing owners, deadlines, inferred dependencies, external blockers).
   - **Graph Engine (`graph_engine.py`)**: Uses NetworkX to build a directed acyclic graph (DAG), detecting circular dependency deadlocks and surfacing schedule bottlenecks.
   - **Minutes module (`minutes_module/`)**: evidence-linked minutes with verification flags, human review, revisions, exports and monitoring. Speaker names are only used when the transcript carries a speaker label or says the name; otherwise minutes say "a participant".
   - **Audio (`audio_processing.py`)**: `POST /api/meetings/transcribe` turns an MP3/WAV/M4A (up to 50 MB / 30 min) into an editable transcript with `faster-whisper`. It runs on CPU, does no speaker recognition, and returns an unsaved transcript for review.
   - **Storage**: Persists to MongoDB Atlas when configured. If Atlas is missing or unreachable, `main.py` falls back to local JSON storage (`storage/meetings_store.json`, or `/tmp` on Vercel); use that only for quick local demos.

---

## Quickstart

### 1. Environment Setup

Use **Python 3.11+**, **Node.js 22.12+**, a MongoDB Atlas database user, and an API key for one supported provider. Allow your current IP in Atlas Network Access and give the database user appropriate read/write permissions.

From the project root, create one Python environment and install the backend dependencies. `task-backend/requirements.txt` is the list Vercel installs; `task-backend/pyproject.toml` + `uv.lock` (use `uv sync` inside `task-backend`) additionally include the audio packages:

```powershell
# Skip environment creation if the root .venv already exists.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r task-backend\requirements.txt
# Optional, only for audio transcription (not needed on Vercel):
python -m pip install faster-whisper av numpy

# Create the configuration file only if it does not already exist.
if (!(Test-Path task-backend\.env)) {
    Copy-Item task-backend\.env.example task-backend\.env
}
```

Configure **only `task-backend/.env`**. Example using Groq:

```env
# MongoDB Atlas is required; replace the placeholders.
MONGODB_URL=mongodb+srv://YOUR_USER:YOUR_ENCODED_PASSWORD@YOUR_CLUSTER.mongodb.net/?retryWrites=true&w=majority
MONGODB_DATABASE=meeting_db

# Fill one provider key. Keep unused provider keys blank.
GEMINI_API_KEY=
GROQ_API_KEY=your_groq_key_here
OPENAI_API_KEY=

MINUTES_LLM_PROVIDER=groq
MINUTES_LLM_MODEL=openai/gpt-oss-20b
LLM_MODEL=

MINUTES_ENABLE_VERIFICATION=true
MINUTES_STORAGE_MODE=mongodb
MINUTES_REQUESTS_PER_MINUTE=20
MINUTES_TOKENS_PER_MINUTE=36000
MINUTES_MAX_CONCURRENCY=1

FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
HOST=127.0.0.1
PORT=8000
```

Use a model available to your provider account and set RPM/TPM at or below its actual account/model limits. The scheduler defaults are not a guarantee of your account's quota. URL-encode special characters in the Atlas password.

For Gemini, fill `GEMINI_API_KEY`, set provider to `gemini`, and choose a compatible Gemini model. For OpenAI, fill `OPENAI_API_KEY`, set provider to `openai`, and choose a compatible chat-completions model. Explicit provider selection takes precedence; otherwise the first configured key is selected in Gemini, Groq, OpenAI order. Model selection is `MINUTES_LLM_MODEL`, then `LLM_MODEL`, then the provider default.

The backend reads only `task-backend/.env`; root `.env` and example files are not loaded. Process environment variables take precedence. Restart the backend after configuration changes. Keep `.env`, API keys, Atlas credentials and `.venv/` out of Git.

### 2. Start the Backend

In terminal one, from the project root:

```powershell
.\.venv\Scripts\Activate.ps1
cd task-backend
python -m uvicorn combined_app:app --reload --host 127.0.0.1 --port 8000
```

Or invoke the shared environment's Python directly without activation:

```powershell
.\.venv\Scripts\python.exe -m uvicorn combined_app:app --app-dir task-backend --reload --host 127.0.0.1 --port 8000
```

Start only one backend on port 8000. Use `combined_app:app` for the complete website and extension API, rather than starting separate `main.py` and `main2.py` servers.

Verify backend health at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) and minutes configuration at [http://127.0.0.1:8000/api/v1/minutes/health](http://127.0.0.1:8000/api/v1/minutes/health). A configured key does not prove provider authentication or quota; generation exercises those. Startup fails if Atlas cannot connect.

Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 3. Start the Web Frontend

In terminal two, from the project root:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). No frontend env file is required for this local setup: the API client defaults to `http://127.0.0.1:8000`. If your backend is elsewhere, optionally set `VITE_API_BASE_URL` in `frontend/.env.local` and restart Vite. Never put provider keys or database credentials in frontend variables.

1. Choose **New meeting**, enter metadata and paste or upload a transcript. `task-backend/minutes_sample_request.json` provides a sample payload.
2. Submit once: the frontend calls meeting processing, obtains the stored meeting ID, then calls minutes generation for that ID.
3. Inspect the meeting tabs, edit draft minutes, review evidence/flags, enter a reviewer name and approve. Reviewer names are audit labels, not authenticated identities.
4. Download PDF/Excel/DOCX, update task statuses, inspect dependencies and ask the assistant about the selected meeting.
5. If enrichment or minutes generation fails, open the saved meeting and retry that stage instead of submitting another copy.
6. Use **Delete** in the Meetings list only when you want to permanently remove the meeting and its associated saved records, including approved minutes. Download needed reports first and avoid deletion while generation is running.

### 4. Load the Chrome Extension

1. Open Chrome at `chrome://extensions` or Edge at `edge://extensions`.
2. Enable **Developer mode**, click **Load unpacked**, and select the `Extension/` directory.
3. Open the extension side panel and confirm the processing endpoint in its settings. The default is `https://capstone-api.shivsharan.me/api/meetings/process`; for a local backend use `http://127.0.0.1:8000/api/meetings/process` (edit `Extension/config.js` or the extension's Settings page; any other host must also be listed in `host_permissions` in `Extension/manifest.json`).
4. Enter the meeting title and participants, then select a capture mode. For Google Meet, enable meeting captions and select **Google Meet Captions** in the extension.
5. Click **Start recording**, capture the conversation, and stop when finished. Microphone mode captures your voice; available capture behavior depends on browser permissions and support.
6. Click **Process with Backend** to extract tasks and analyze dependencies.
7. Open the website to view the saved meeting and generate its minutes if needed. The website also works without the extension running.

### 5. Verify and Test

From the project root, run the offline backend suite and frontend checks:

```powershell
.\.venv\Scripts\python.exe -B -m pytest task-backend\tests_minutes -q -p no:cacheprovider
npm --prefix frontend run test
npm --prefix frontend run build
```

Backend tests mock provider and MongoDB calls; they do not verify your real quota, credentials or Atlas transaction permissions. Test the live workflow through the website or Swagger: process a transcript, generate minutes, edit/approve, export, change a task status, ask a question, inspect monitoring and refresh saved data.

Use the `meeting_id` returned by processing; it can differ from the transcript payload's original metadata ID. Before minutes exist, a minutes lookup can legitimately return 404. Exports require an approved revision unless `draft=true` is explicitly requested. Generation errors include a safe `detail.code` and available diagnostics; do not repeatedly resubmit a whole meeting to retry a failed later stage.

Deployment and domain setup: [DEPLOYMENT.md](DEPLOYMENT.md). Backend optimization and monitoring notes: [MINUTES_MODULE_README.md](task-backend/MINUTES_MODULE_README.md).

---

## API Endpoints

All paths below are exposed by `combined_app:app`. Meeting and minutes list endpoints accept `offset` and `limit` pagination parameters. The minutes list path has no trailing slash.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Healthcheck and active provider status |
| `POST` | `/api/meetings/process` | End-to-end meeting processing (consumed by extension) |
| `POST` | `/api/v1/extract-factual` | Pass 1: Strict factual task extraction |
| `POST` | `/api/v1/infer-context/{id}` | Pass 2: Contextual enrichment & graph analysis |
| `GET` | `/api/meetings` | List all processed meetings |
| `GET` | `/api/meetings/{id}` | Retrieve meeting document and graph analysis |
| `PATCH` | `/api/meetings/{id}` | Rename a meeting or edit its participants |
| `DELETE` | `/api/meetings/{id}` | Delete a meeting and its saved minutes |
| `POST` | `/api/meetings/transcribe` | Audio file to transcript (needs faster-whisper; not on Vercel) |
| `POST` | `/api/meetings/{id}/query` | Ask a question about one meeting |
| `PATCH` | `/api/meetings/{id}/tasks/{task_id}/status` | Update a task's status |
| `GET/POST` | `/api/v1/minutes/...` | Generate, edit, approve, export (PDF/XLSX/DOCX) and inspect minutes |

---

## Deployment

Two interchangeable backends; the website picks one through `VITE_API_BASE_URL`. Full runbook, Cloudflare DNS records and GCP notes: [DEPLOYMENT.md](DEPLOYMENT.md).

| | Vercel (default) | GCP Cloud Run (optional) |
|---|---|---|
| Audio transcription | No | Yes |
| Idle cost | none | none (scales to zero) |
| Deploy | `./deploy.sh backend` | `./gcp.sh deploy` |

```bash
./deploy.sh              # deploy backend then frontend to Vercel production from the CURRENT working tree
./deploy.sh frontend     # or: backend. Add --skip-checks to skip local tests/builds
BACKEND_URL=<url> ./deploy.sh frontend   # point the website at a different backend
./gcp.sh setup           # first-time GCP deploy (needs `gcloud auth login`, a billing-enabled project and Docker)
```

- Neither script commits or pushes to git. Each deploys a staged build, smoke-tests it and only then promotes it, so a failed check never changes the live site. Roll back with `npx vercel rollback <deployment url>`.
- **Auto-deploy:** pushing to `main` deploys whatever changed through GitHub Actions (`.github/workflows/deploy.yml`), using the same staged build, smoke test and promote steps. Pull requests run `ci.yml`. One-time secret setup is in [DEPLOYMENT.md](DEPLOYMENT.md#auto-deploy-pipeline-github-actions).
- Vercel installs `task-backend/requirements.txt` (not `pyproject.toml`), so anything the deployed backend imports must be listed there.
- Domains: `capstone-app.shivsharan.me` (frontend) and `capstone-api.shivsharan.me` (backend) are CNAME records in Cloudflare set to **DNS only** (grey cloud). A proxied (orange) record breaks Vercel's certificate issuance and returns Cloudflare error 525.
- The backend's CORS accepts `FRONTEND_ORIGINS`, any `https://*.shivsharan.me`, and Chrome extensions.
