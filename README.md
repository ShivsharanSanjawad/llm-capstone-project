# Meeting Minutes & Task Intelligence System

Turns a meeting conversation into structured minutes and a tracked task list. Transcripts come from a Chrome extension that reads live Google Meet captions, or are pasted or uploaded on the website. The system extracts the tasks people committed to, fills in owners, exact deadlines and priorities, finds dependencies, circular dependencies and bottlenecks, and writes evidence-linked minutes that a person reviews, approves and exports.

The language model is one part of a pipeline, not the final word: dates, priority scores, dependency cycles and source references are checked by plain code, and anything uncertain is flagged for a person.

## Live deployment

| | URL |
|---|---|
| Website | https://capstone-app.shivsharan.me |
| Backend API | https://capstone-api.shivsharan.me (`/health` shows the active AI provider, `/docs` is Swagger) |
| Chrome extension | load `Extension/` as an unpacked extension; it points at the deployed backend by default |

Audio upload needs a speech model that does not fit on Vercel; it works on a local backend (see [Audio](#audio-transcription)).

---

## Features

- **Two-pass task extraction.** Pass 1 records only what was said aloud (task, owner, deadline wording). Pass 2 adds owners, deadlines, priority, status and dependencies, split into two smaller calls (core fields, then dependencies) so each call stays focused.
- **Exact deadlines.** The model proposes a calendar date; code checks it (real date, not before the meeting, weekday matches, within two years) and falls back to date rules for wording like "by Friday" or "within 5 days". Counted from the meeting day (IST).
- **Priority score (0-100).** Deadline urgency (up to 35) + how many tasks this one blocks (up to 30) + the model's importance rating (up to 35). High ≥ 55, Medium ≥ 30, otherwise Low. The breakdown is shown per task.
- **Dependency graph.** NetworkX finds circular dependencies, bottlenecks and whether the plan is feasible. The graph can be edited on the website; manual edits are kept when the meeting is analysed again.
- **Evidence-linked minutes.** Topic-wise summary, decisions, unresolved issues and follow-ups. Every line quotes the transcript, and code checks that the quote really exists. Decisions are always marked for confirmation.
- **Review and export.** Edit drafts, approve with a reviewer name, keep a revision history, export PDF, DOCX and Excel.
- **Meeting Q&A.** Ask questions about one meeting; answers cite the transcript.
- **Tracing.** Every pipeline run can be logged to MLflow (DagsHub) with step timings, counts, tokens and cost. Transcripts and names are never sent.
- **Switchable AI provider.** Gemini, Groq or OpenAI by one setting; a local model through LM Studio for offline testing.

## Architecture

```mermaid
graph LR
    subgraph Browser
        Meet[Google Meet captions] --> Ext[Chrome extension<br/>Manifest V3 side panel]
        Web[Web app<br/>React + TypeScript]
    end
    subgraph Vercel
        API[FastAPI backend<br/>combined_app]
        Site[Website hosting]
    end
    Ext -->|HTTPS / JSON| API
    Web -->|HTTPS / JSON| API
    Site -.->|serves the app| Web
    API --> LLM[Shared LLM service<br/>scheduler, cache, retries]
    LLM --> Prov[Gemini / Groq / OpenAI<br/>or LM Studio locally]
    API --> Graph[NetworkX graph engine]
    API --> DB[(MongoDB Atlas)]
    API -.->|run metrics| MLflow[MLflow on DagsHub]
```

One FastAPI app (`task-backend/combined_app.py`) serves both clients. Its parts:

| Part | File(s) | What it does |
|---|---|---|
| Task pipeline | `main.py`, `deadlines.py`, `priority.py` | Pass 1 and Pass 2, exact dates, priority score |
| Graph engine | `graph_engine.py`, `dependency_edits.py` | Cycles, bottlenecks, feasibility; manual edge edits |
| Minutes pipeline | `minutes_module/` | Segmenting, map / reduce summarising, source-quote check, revisions, exports |
| Workspace and Q&A | `workspace_api.py` | Meeting edits, task status, dependency edits, questions |
| Shared LLM service | `minutes_module/llm_service.py` | One client for every AI call: request pacing, caching, retries, usage and cost records |
| Tracing | `tracking.py`, `pipeline_metrics.py` | MLflow runs; does nothing unless `MLFLOW_TRACKING_URI` is set |
| Audio | `audio_processing.py` | MP3/WAV/M4A to editable transcript with faster-whisper (local only) |

### Processing flow

1. **Capture or upload** the transcript (extension, paste, file or audio).
2. **Pass 1, factual extraction:** only tasks said aloud.
3. **Pass 2, analysis:** owners, deadlines, priority, status (one call) and dependencies (a second call).
4. **Code checks:** exact deadlines, priority score, manual dependency edits re-applied.
5. **Graph analysis:** cycles, bottlenecks, feasibility.
6. **Minutes** (website, on request): map / reduce over transcript chunks, then the source-quote check.
7. **Review, approve, export, ask questions.**

The extension runs steps 1-5. The website runs all of them.

---

## Project layout

```
Extension/         Chrome extension (side panel, Meet caption reader, settings page)
frontend/          React + TypeScript website (Vite)
task-backend/      FastAPI backend, minutes module, evals, tests
.github/workflows/ ci.yml (pull requests), deploy.yml (push to main)
deploy.sh          Manual staged deploy to Vercel
DEPLOYMENT.md      Deployment runbook, domains, GitHub secrets
```

---

## Run it locally

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+, a MongoDB Atlas database and an API key for one provider.

### 1. Backend

```bash
cd task-backend
uv sync                      # installs everything, including the audio packages
cp .env.example .env         # then fill it in (see Configuration)
uv run uvicorn combined_app:app --reload --host 127.0.0.1 --port 8000
```

Check http://127.0.0.1:8000/health (active provider, database status) and http://127.0.0.1:8000/docs. Run only one backend on port 8000, always `combined_app:app`. The backend refuses to start if Atlas cannot be reached; allow your IP in Atlas Network Access.

Without uv: `python -m venv .venv`, activate it, `pip install -r task-backend/requirements.txt` (add `faster-whisper av numpy` for audio), then run the same uvicorn command.

### 2. Website

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. It talks to `http://127.0.0.1:8000` unless `VITE_API_BASE_URL` is set in `frontend/.env.local`. Never put API keys or database credentials in frontend variables.

1. **New meeting:** enter a title and participants, then paste or upload a transcript.
2. The website processes the tasks, then generates the minutes for the saved meeting.
3. Review the tasks, the dependency graph (with **Edit dependencies** to add or remove links) and the minutes with their evidence and flags.
4. Enter a reviewer name and approve, then export PDF, DOCX or Excel.
5. If a step fails, open the saved meeting and retry that step; do not submit the meeting again.

### 3. Chrome extension

1. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose `Extension/`.
2. In the extension's **Settings**, set the backend: the default is the deployed API; for a local backend use `http://127.0.0.1:8000/api/meetings/process`.
3. In Google Meet, turn on captions, open the side panel, choose **Google Meet Captions**, and start recording.
4. Stop when done, then **Process with Backend**, and **Analyze more** for priorities and dependencies.
5. After changing extension files, press the reload button on the extension in `chrome://extensions`.

---

## Configuration

The backend reads only `task-backend/.env` locally; on Vercel the same names are set as environment variables. Real environment variables override `.env`. Restart the backend after changes.

### Required

| Variable | Example | Notes |
|---|---|---|
| `MONGODB_URL` | `mongodb+srv://user:pass@cluster.mongodb.net/...` | One line, no quotes; URL-encode special characters in the password |
| `LLM_PROVIDER` | `gemini`, `groq`, `openai` or `local` | Which AI provider every call uses. If unset, the first key found wins (Gemini, then Groq, then OpenAI); `local` is never picked automatically |
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENAI_API_KEY` | | Set the one you use; the others may stay set |
| `FRONTEND_ORIGINS` | `http://localhost:5173` | Allowed website origins (CORS); `https://*.shivsharan.me` and Chrome extensions are always allowed |

### Model limits

| Variable | Default | Meaning |
|---|---|---|
| `LLM_MODEL` | provider default | Model id; must belong to the chosen provider. Defaults: `gemini-3.5-flash-lite`, `openai/gpt-oss-120b` (Groq), `gpt-4o-mini` |
| `MINUTES_CONTEXT_TOKENS` | 32000 | Largest request (input + answer) the app will send |
| `MINUTES_MAX_OUTPUT_TOKENS` | 16384 | Answer limit; starts high so answers are not cut off and retried |
| `MINUTES_TOKENS_PER_MINUTE` | 36000 | The app's own pacing; keep it at or below your account's real limit |
| `MINUTES_TIMEOUT_SECONDS` | 90 | Time limit per AI call; keep under Vercel's 300-second limit |

### Optional

| Variable | Default | Meaning |
|---|---|---|
| `MONGODB_DATABASE` | `meeting_db` | Database name |
| `SPLIT_ANALYSIS` | `on` | `off` runs Pass 2 as one call instead of two |
| `GEMINI_REASONING_EFFORT` | `low` | Gemini thinking level: `low` (fastest), `medium` or `high` (slowest, most output tokens) |
| `MINUTES_ENABLE_VERIFICATION` | `true` | Free check that every minutes line quotes real transcript text |
| `MINUTES_ENABLE_SEMANTIC_REVIEW` | `false` | Extra AI review of every minutes claim (many more calls and tokens) |
| `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD` | unset | Turn on MLflow tracing (DagsHub: your username and an access token) |
| `MLFLOW_EXPERIMENT_NAME` | `meeting-intelligence` | MLflow experiment |
| `AUDIO_WHISPER_MODEL` | `base` | Speech model for audio upload |

### Switching providers

Change `LLM_PROVIDER` and the limits together; the limits must suit the provider.

| Variable | Gemini (paid) | Groq (free tier) | LM Studio (local) |
|---|---|---|---|
| `LLM_PROVIDER` | `gemini` | `groq` | `local` |
| `MINUTES_CONTEXT_TOKENS` | `64000` | `32000` | the context length loaded in LM Studio, e.g. `16384` |
| `MINUTES_TOKENS_PER_MINUTE` | `400000` | `8000` | `1000000` |
| `MINUTES_MAX_OUTPUT_TOKENS` | `32768` | unset | unset |
| other | | | `LOCAL_LLM_MODEL` (id from `http://127.0.0.1:1234/v1/models`), `MINUTES_TIMEOUT_SECONDS=900` |

Groq's free tier allows about 8,000 tokens per minute and 200,000 per day, so roughly 5-10 meetings a day; with Gemini values it returns 429 or 413 errors. LM Studio works only on a local backend: the deployed site cannot reach your computer. Use a non-thinking model (for example Qwen3 4B Instruct 2507) or turn thinking off; small local models miss more dependencies than the cloud models.

---

## Testing and evaluation

```bash
cd task-backend && uv run pytest tests_minutes -q      # backend, offline (mocks the AI and the database)
npm --prefix frontend run test                         # frontend unit tests
npm --prefix frontend run build                        # type check and build
```

Evals on six hand-labelled meetings (`task-backend/evals/`):

```bash
cd task-backend
uv run python -m evals.run_evals                 # deterministic checks, no AI calls, free
uv run python -m evals.run_evals --live          # also runs the real model (uses your API quota)
uv run python -m evals.run_evals --live --mlflow # and logs the run to MLflow
```

Results go to `evals/results/` (`REPORT.md`, `report.html`, `latest.json`). The offline tests never call a paid provider.

---

## API

All paths are served by `combined_app:app`. Errors carry a `detail.code` (for example `provider_quota_exhausted`).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Status, active provider, database |
| `POST` | `/api/meetings/process` | Full task pipeline (used by the extension and website) |
| `POST` | `/api/v1/extract-factual` | Pass 1 only |
| `POST` | `/api/v1/infer-context/{id}` | Pass 2 and graph analysis for a saved meeting |
| `GET` | `/api/meetings`, `/api/meetings/{id}` | List meetings, get one with its tasks and graph |
| `PATCH` | `/api/meetings/{id}` | Rename or edit participants |
| `DELETE` | `/api/meetings/{id}` | Delete a meeting and its minutes |
| `PATCH` | `/api/meetings/{id}/tasks/{task_id}/status` | Change a task's status |
| `PATCH` | `/api/meetings/{id}/dependencies` | Add or remove dependency links |
| `POST` | `/api/meetings/{id}/query` | Ask a question about the meeting |
| `POST` | `/api/meetings/transcribe` | Audio to transcript (local only) |
| `POST` | `/api/v1/minutes/generate/{id}` | Generate minutes for a saved meeting |
| `GET`, `PUT` | `/api/v1/minutes/{id}` | Latest revision; save an edited draft |
| `POST` | `/api/v1/minutes/{id}/approve`, `/regenerate` | Approve; generate a new revision |
| `GET` | `/api/v1/minutes/{id}/history` | Revision history |
| `GET` | `/api/v1/minutes/{id}/export/pdf`, `/docx`, `/xlsx` | Export (approved revision unless `draft=true`) |
| `GET` | `/api/v1/minutes/health`, `/monitoring/stats` | Minutes configuration, usage and cost |

---

## Deployment

Two Vercel projects: `vizura-backend` (API) and `vizura-frontend` (website). Domains are Cloudflare CNAME records set to **DNS only**. Full runbook: [DEPLOYMENT.md](DEPLOYMENT.md).

- **Automatic:** a push to `main` runs `.github/workflows/deploy.yml`: tests, a staged build, a smoke test, then promotion to production. It needs the GitHub secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_BACKEND_PROJECT_ID` and `VERCEL_FRONTEND_PROJECT_ID`. Pull requests run `ci.yml`.
- **Manual:** `./deploy.sh` (both), `./deploy.sh backend` or `./deploy.sh frontend`. It deploys the current working tree, does not commit or push, and only promotes a build that passes its checks.
- **Environment variables:** set them in the backend project (Production), from the `task-backend` folder: `vercel env add NAME production --value VALUE --force --yes`. Mark keys, passwords and `MONGODB_URL` as sensitive. Changes apply only to the next deployment, so redeploy after changing them. The website needs only `VITE_API_BASE_URL=https://capstone-api.shivsharan.me`.
- **Requirements:** Vercel installs `task-backend/requirements.txt`, so anything the backend imports must be listed there.

### Vercel limits to know

- A request is stopped after 300 seconds, so minutes for a very long meeting can time out; the website retries and recovers a half-saved meeting.
- Function logs are kept for a short time only; MLflow tracing keeps the run history.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `provider_quota_exhausted` / 429 | The provider's daily or per-minute quota is used up. Wait, or switch provider. On Groq, keep `MINUTES_TOKENS_PER_MINUTE=8000` |
| `finish_reason: "length"` in logs | The answer hit the output limit. Raise `MINUTES_MAX_OUTPUT_TOKENS` (Gemini allows 32768) |
| `request_exceeds_context_budget` | The transcript is larger than `MINUTES_CONTEXT_TOKENS`; raise it if the provider allows |
| "Failed to fetch" in the extension | Wrong backend address in Settings, the local backend is not running, or the extension needs a reload |
| `/health` shows the wrong provider | Check `LLM_PROVIDER`, then redeploy: Vercel variables apply only to new deployments |
| Backend does not start | MongoDB Atlas unreachable: check `MONGODB_URL` and Atlas Network Access |
