"""Experiment tracking and traceability through MLflow (works with a DagsHub MLflow server).

Each pipeline run (processing a meeting, extracting tasks, analysing dependencies, generating minutes) becomes ONE MLflow run:
parameters (model, settings, input size), a metric per step (duration, counts), LLM usage (tokens, calls, retries, cost),
the final outcome, and tags that tie the run to a meeting. It talks to MLflow's REST API with httpx, so nothing heavy is
added to the serverless bundle.

Privacy: transcripts, task text, names and participants are never sent. Only sizes, counts, scores, ids and timings are.

Off by default. Turn on by setting MLFLOW_TRACKING_URI (plus credentials) in the backend environment:
  MLFLOW_TRACKING_URI=https://dagshub.com/<user>/<repo>.mlflow
  MLFLOW_TRACKING_USERNAME=<user>          MLFLOW_TRACKING_PASSWORD=<dagshub token>
Optional: MLFLOW_EXPERIMENT_NAME (default "meeting-intelligence"), MLFLOW_BACKGROUND=true (do not wait for the upload),
MLFLOW_DISABLED=true. Tracking can never fail a request: every error is swallowed and logged.
"""
import asyncio
import contextvars
import hashlib
import logging
import math
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("uvicorn.error")
CURRENT: contextvars.ContextVar = contextvars.ContextVar("current_trace", default=None)
_transport: Optional[httpx.AsyncBaseTransport] = None      # tests inject a fake server here
_experiment_ids: Dict[str, str] = {}
_UPLOAD_TIMEOUT = 6.0


def settings() -> Optional[Dict[str, Any]]:
    uri = (os.getenv("MLFLOW_TRACKING_URI") or "").strip().strip("\"'").rstrip("/")
    if not uri or (os.getenv("MLFLOW_DISABLED") or "").strip().lower() in {"1", "true", "yes"}:
        return None
    token = (os.getenv("MLFLOW_TRACKING_TOKEN") or "").strip()
    user = (os.getenv("MLFLOW_TRACKING_USERNAME") or "").strip()
    password = (os.getenv("MLFLOW_TRACKING_PASSWORD") or "").strip()
    auth = httpx.BasicAuth(user, password) if user and password else None
    headers = {"Authorization": f"Bearer {token}"} if token and not auth else {}
    return {
        "uri": uri, "auth": auth, "headers": headers,
        "experiment": (os.getenv("MLFLOW_EXPERIMENT_NAME") or "meeting-intelligence").strip(),
        "background": (os.getenv("MLFLOW_BACKGROUND") or "").strip().lower() in {"1", "true", "yes"},
    }


def enabled() -> bool:
    return settings() is not None


def fingerprint(text: str) -> str:
    """A short one-way id for a transcript, so repeated runs on the same text can be grouped without storing it."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _clip(value: Any, limit: int = 250) -> str:
    return str(value)[:limit]


async def send_run(name: str, params: Dict[str, Any], metrics: Dict[str, Any], tags: Dict[str, Any],
                   status: str = "FINISHED", start_ms: Optional[int] = None, end_ms: Optional[int] = None,
                   experiment: Optional[str] = None, steps: Optional[Dict[str, int]] = None) -> Optional[str]:
    """Create one MLflow run with everything attached. Returns the run id, or None when disabled or on any failure."""
    cfg = settings()
    if not cfg:
        return None
    start_ms = start_ms or int(time.time() * 1000)
    end_ms = end_ms or int(time.time() * 1000)
    try:
        async with httpx.AsyncClient(base_url=cfg["uri"], auth=cfg["auth"], headers=cfg["headers"],
                                     timeout=_UPLOAD_TIMEOUT, transport=_transport) as client:
            experiment_name = experiment or cfg["experiment"]
            experiment_id = _experiment_ids.get(experiment_name)
            if not experiment_id:
                found = await client.get("/api/2.0/mlflow/experiments/get-by-name", params={"experiment_name": experiment_name})
                if found.status_code == 200:
                    experiment_id = found.json()["experiment"]["experiment_id"]
                else:
                    created = await client.post("/api/2.0/mlflow/experiments/create", json={"name": experiment_name})
                    created.raise_for_status()
                    experiment_id = created.json()["experiment_id"]
                _experiment_ids[experiment_name] = experiment_id
            run = await client.post("/api/2.0/mlflow/runs/create", json={
                "experiment_id": experiment_id, "start_time": start_ms, "run_name": _clip(name, 100),
                "tags": [{"key": "mlflow.runName", "value": _clip(name, 100)}]})
            run.raise_for_status()
            run_id = run.json()["run"]["info"]["run_id"]
            metric_rows = []
            for key, value in metrics.items():
                number = _number(value)
                if number is not None:
                    metric_rows.append({"key": _clip(key, 250), "value": number, "timestamp": end_ms,
                                        "step": int((steps or {}).get(key, 0))})
            param_rows = [{"key": _clip(k, 250), "value": _clip(v)} for k, v in params.items() if v is not None]
            tag_rows = [{"key": _clip(k, 250), "value": _clip(v, 5000)} for k, v in tags.items() if v is not None]
            # MLflow accepts at most 1000 metrics, 100 params and 100 tags per call.
            batches = max(-(-len(metric_rows) // 900), -(-len(param_rows) // 90), -(-len(tag_rows) // 90), 1)
            for j in range(batches):
                sent = await client.post("/api/2.0/mlflow/runs/log-batch", json={
                    "run_id": run_id, "metrics": metric_rows[j * 900:(j + 1) * 900],
                    "params": param_rows[j * 90:(j + 1) * 90], "tags": tag_rows[j * 90:(j + 1) * 90]})
                sent.raise_for_status()
            await client.post("/api/2.0/mlflow/runs/update", json={"run_id": run_id, "status": status, "end_time": end_ms})
            return run_id
    except Exception as exc:  # tracking must never break the product
        log.warning("MLflow tracking failed (%s: %.200s)", type(exc).__name__, exc)
        return None


class Trace:
    """Collects the steps, metrics and LLM usage of one pipeline run, then uploads them as one MLflow run."""

    def __init__(self, name: str, **params: Any):
        self.name = name
        self.params: Dict[str, Any] = {"pipeline": name, **params}
        self.metrics: Dict[str, Any] = {}
        self.tags: Dict[str, Any] = {"pipeline": name}
        self.steps: List[str] = []
        self.step_index: Dict[str, int] = {}
        self.started = time.time()
        self._t0 = time.monotonic()
        self._token = None
        for key in ("VERCEL_ENV", "VERCEL_REGION", "VERCEL_DEPLOYMENT_ID", "VERCEL_GIT_COMMIT_SHA"):
            if os.getenv(key):
                self.tags[key.lower()] = os.getenv(key)

    def metric(self, key: str, value: Any) -> None:
        self.metrics[key] = value

    def param(self, key: str, value: Any) -> None:
        self.params[key] = value

    def tag(self, key: str, value: Any) -> None:
        self.tags[key] = value

    @contextmanager
    def step(self, name: str, **info: Any):
        """Time one stage of the pipeline. Numbers set on the yielded dict become metrics named step.<name>.<key>."""
        details: Dict[str, Any] = dict(info)
        index = len(self.steps)
        self.steps.append(name)
        t0 = time.monotonic()
        status = "ok"
        try:
            yield details
        except BaseException as exc:
            status = "error:" + type(exc).__name__
            raise
        finally:
            key = f"step.{name}.duration_s"
            self.metrics[key] = round(time.monotonic() - t0, 3)
            self.step_index[key] = index
            self.tags[f"step.{name}.status"] = status
            for k, v in details.items():
                if _number(v) is not None:
                    self.metrics[f"step.{name}.{k}"] = v
                    self.step_index[f"step.{name}.{k}"] = index
                elif v is not None:
                    self.tags[f"step.{name}.{k}"] = _clip(v, 500)

    def add_llm(self, record: Dict[str, Any]) -> None:
        """Attach one LLM request (task extraction, inference, minutes, ...) to this pipeline run."""
        kind = str(record.get("meeting_id") if str(record.get("meeting_id", "")).startswith("task-") else "minutes")
        prefix = f"llm.{kind}."
        for key in ("calls", "retries", "input_tokens", "output_tokens", "estimated_input_tokens", "estimated_output_tokens",
                    "duration_seconds", "scheduler_wait_seconds", "estimated_cost_usd", "chunk_count"):
            if _number(record.get(key)) is not None:
                self.metrics[prefix + key] = record[key]
        self.tags[prefix + "status"] = record.get("status")
        self.tags[prefix + "request_id"] = record.get("request_id")
        if record.get("error_type"):
            self.tags[prefix + "error"] = record["error_type"]
        if record.get("usage_complete") is False:
            self.tags[prefix + "usage"] = "estimated (provider reported no tokens)"
        for key in ("input_tokens", "output_tokens", "calls", "retries"):
            if _number(record.get(key)) is not None:
                total = "llm.total_" + key
                self.metrics[total] = (self.metrics.get(total) or 0) + record[key]

    def discard(self) -> None:
        """Stop collecting without uploading anything (used when the caller only wants the numbers)."""
        if self._token is not None:
            try:
                CURRENT.reset(self._token)
            except ValueError:
                pass
            self._token = None

    async def finish(self, status: str = "succeeded", error: Optional[str] = None) -> Optional[str]:
        if self._token is not None:
            try:
                CURRENT.reset(self._token)
            except ValueError:
                pass
            self._token = None
        if not enabled():
            return None
        self.metrics["duration_s"] = round(time.monotonic() - self._t0, 3)
        self.tags["status"] = status
        if error:
            self.tags["error"] = _clip(error, 500)
        self.tags["steps"] = " > ".join(self.steps)
        job = send_run(f"{self.name} {time.strftime('%H:%M:%S', time.localtime(self.started))}", self.params, self.metrics,
                       self.tags, "FINISHED" if status == "succeeded" else "FAILED", int(self.started * 1000), None,
                       steps=self.step_index)
        cfg = settings() or {}
        if cfg.get("background"):
            asyncio.get_running_loop().create_task(job)
            return None
        try:
            return await asyncio.wait_for(job, _UPLOAD_TIMEOUT * 2)
        except Exception as exc:
            log.warning("MLflow upload gave up (%s)", type(exc).__name__)
            return None


def begin(name: str, **params: Any) -> Trace:
    """Start a trace and make it the current one, so LLM calls made inside attach themselves to it."""
    trace = Trace(name, **params)
    trace._token = CURRENT.set(trace)
    return trace


def record_llm(record: Dict[str, Any]) -> None:
    trace = CURRENT.get()
    if trace is not None:
        try:
            trace.add_llm(record)
        except Exception:
            pass
