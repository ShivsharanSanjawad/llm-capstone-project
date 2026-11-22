import asyncio
import json

import httpx
import pytest

import tracking


@pytest.fixture(autouse=True)
def clean_context():
    # begin() in a synchronous test is not finished in the same context; do not let it leak into the next test.
    tracking.CURRENT.set(None)
    yield
    tracking.CURRENT.set(None)


class FakeMlflow:
    """A tiny in-memory MLflow REST server."""
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, body, request.headers.get("authorization")))
        if self.fail_on and self.fail_on in path:
            return httpx.Response(500, json={"error_code": "INTERNAL_ERROR"})
        if path.endswith("experiments/get-by-name"):
            return httpx.Response(404, json={"error_code": "RESOURCE_DOES_NOT_EXIST"})
        if path.endswith("experiments/create"):
            return httpx.Response(200, json={"experiment_id": "7"})
        if path.endswith("runs/create"):
            return httpx.Response(200, json={"run": {"info": {"run_id": "run-1"}}})
        return httpx.Response(200, json={})


@pytest.fixture
def mlflow(monkeypatch):
    server = FakeMlflow()
    monkeypatch.setattr(tracking, "_transport", httpx.MockTransport(server))
    monkeypatch.setattr(tracking, "_experiment_ids", {})
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://dagshub.com/user/repo.mlflow/")
    monkeypatch.setenv("MLFLOW_TRACKING_USERNAME", "user")
    monkeypatch.setenv("MLFLOW_TRACKING_PASSWORD", "secret-token")
    monkeypatch.delenv("MLFLOW_BACKGROUND", raising=False)
    monkeypatch.delenv("MLFLOW_DISABLED", raising=False)
    return server


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_off_by_default_and_never_calls_out(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert not tracking.enabled()
    trace = tracking.begin("process_meeting")
    with trace.step("x"):
        pass
    assert run(trace.finish()) is None


def test_disabled_flag_wins(mlflow, monkeypatch):
    monkeypatch.setenv("MLFLOW_DISABLED", "true")
    assert not tracking.enabled()


def test_one_run_with_steps_metrics_params_and_llm_usage(mlflow):
    trace = tracking.begin("process_meeting", model="gpt-oss-120b", transcript_chars=420)
    with trace.step("pass1_extraction") as s:
        s["tasks_extracted"] = 3
        s["note"] = "fine"
    with trace.step("graph") as s:
        s["cycles"] = 1
    tracking.record_llm({"meeting_id": "task-extraction", "calls": 1, "retries": 0, "input_tokens": 800, "output_tokens": 300,
                         "duration_seconds": 0.9, "status": "succeeded", "request_id": "r1", "estimated_cost_usd": 0.0004})
    tracking.record_llm({"meeting_id": "task-inference", "calls": 2, "retries": 1, "input_tokens": 2000, "output_tokens": 900,
                         "status": "succeeded", "request_id": "r2"})
    assert run(trace.finish("succeeded")) == "run-1"

    by_path = {p.rsplit("/", 1)[-1]: b for _, p, b, _ in mlflow.calls if p.endswith(("create", "log-batch", "update"))}
    assert [c[1].rsplit("/", 1)[-1] for c in mlflow.calls] == [
        "get-by-name", "create", "create", "log-batch", "update"]
    batch = by_path["log-batch"]
    metrics = {m["key"]: m["value"] for m in batch["metrics"]}
    params = {p["key"]: p["value"] for p in batch["params"]}
    tags = {t["key"]: t["value"] for t in batch["tags"]}
    assert params["model"] == "gpt-oss-120b" and params["transcript_chars"] == "420"
    assert metrics["step.pass1_extraction.tasks_extracted"] == 3
    assert metrics["step.graph.cycles"] == 1
    assert metrics["llm.task-extraction.input_tokens"] == 800
    assert metrics["llm.total_input_tokens"] == 2800 and metrics["llm.total_retries"] == 1
    assert metrics["duration_s"] >= 0
    assert tags["steps"] == "pass1_extraction > graph"
    assert tags["step.pass1_extraction.note"] == "fine"
    assert tags["status"] == "succeeded"
    assert by_path["update"]["status"] == "FINISHED"
    step_of = {m["key"]: m["step"] for m in batch["metrics"]}
    assert step_of["step.graph.duration_s"] == 1                     # ordering of steps is preserved
    assert mlflow.calls[1][3].startswith("Basic ")                   # authenticated


def test_failed_run_is_marked_failed_and_step_errors_are_tagged(mlflow):
    trace = tracking.begin("infer_context")
    with pytest.raises(ValueError):
        with trace.step("pass2"):
            raise ValueError("boom")
    run(trace.finish("failed", "task_provider_error"))
    batch = [b for _, p, b, _ in mlflow.calls if p.endswith("log-batch")][0]
    tags = {t["key"]: t["value"] for t in batch["tags"]}
    assert tags["step.pass2.status"] == "error:ValueError"
    assert tags["error"] == "task_provider_error"
    assert [b for _, p, b, _ in mlflow.calls if p.endswith("update")][0]["status"] == "FAILED"


def test_experiment_is_created_once_and_reused(mlflow):
    for _ in range(2):
        run(tracking.send_run("r", {}, {"m": 1}, {}))
    assert sum(1 for _, p, _, _ in mlflow.calls if p.endswith("experiments/create")) == 1


def test_a_broken_server_never_raises(monkeypatch, mlflow):
    monkeypatch.setattr(tracking, "_transport", httpx.MockTransport(FakeMlflow(fail_on="runs/create")))
    trace = tracking.begin("process_meeting")
    assert run(trace.finish()) is None


def test_no_transcript_or_names_can_leak_through_the_helper():
    assert tracking.fingerprint("secret meeting text") == tracking.fingerprint("secret meeting text")
    assert "secret" not in tracking.fingerprint("secret meeting text")
    assert len(tracking.fingerprint("x")) == 16


def test_token_auth_and_large_batches(monkeypatch, mlflow):
    monkeypatch.delenv("MLFLOW_TRACKING_USERNAME")
    monkeypatch.delenv("MLFLOW_TRACKING_PASSWORD")
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "tok")
    metrics = {f"m{i}": i for i in range(2000)}
    run(tracking.send_run("big", {f"p{i}": i for i in range(150)}, metrics, {}))
    batches = [b for _, p, b, _ in mlflow.calls if p.endswith("log-batch")]
    assert len(batches) == 3
    assert all(len(b["metrics"]) <= 900 and len(b["params"]) <= 90 for b in batches)
    assert sum(len(b["metrics"]) for b in batches) == 2000
    assert mlflow.calls[1][3] == "Bearer tok"


def test_current_trace_is_visible_inside_and_cleared_after_a_pipeline(mlflow):
    async def pipeline():
        trace = tracking.begin("p")
        inside = tracking.CURRENT.get() is trace
        await trace.finish()
        return inside, tracking.CURRENT.get()
    inside, after = run(pipeline())
    assert inside is True and after is None


def test_process_pipeline_is_traced_end_to_end_without_leaking_content(mlflow, monkeypatch):
    import main
    from main import MeetingProcessRequest
    from models import ContextualInference, EnrichedTask, ExplicitTask, FactualExtraction

    SECRET = "Priya: I will build the secret UI within five days."

    async def factual(text, meeting_date=None):
        return FactualExtraction(meeting_title="t", date="d", tasks=[
            ExplicitTask(task_id="T1", description="Build UI", explicit_deadline="within five days")])

    async def inference(text, tasks, meeting_date=None):
        tracking.record_llm({"meeting_id": "task-inference", "calls": 1, "input_tokens": 500, "output_tokens": 200,
                             "status": "succeeded", "request_id": "r9"})
        return ContextualInference(enriched_tasks=[EnrichedTask(
            task_id="T1", description="Build UI", owner="Priya", missing_owner=False, deadline="within five days",
            missing_deadline=False, priority="High", explicit_prerequisites=[])])

    async def save(doc): return "m1"
    async def update(mid, updates): return True

    monkeypatch.setattr(main, "run_factual_extraction", factual)
    monkeypatch.setattr(main, "run_contextual_inference", inference)
    monkeypatch.setattr(main, "save_meeting_doc", save)
    monkeypatch.setattr(main, "update_meeting_doc", update)
    request = MeetingProcessRequest(transcript_text=SECRET, meeting={"title": "m", "startedAt": "2026-11-22T19:41:00+05:30", "participants": ["Priya"]})
    result = run(main.process_meeting_from_extension(request))
    assert result["status"] == "success"

    posted = json.dumps([b for _, _, b, _ in mlflow.calls])
    assert "secret UI" not in posted and "Priya" not in posted and "Build UI" not in posted     # no content leaves the server
    batch = [b for _, p, b, _ in mlflow.calls if p.endswith("log-batch")][0]
    metrics = {m["key"]: m["value"] for m in batch["metrics"]}
    tags = {t["key"]: t["value"] for t in batch["tags"]}
    assert tags["steps"] == ("pass1_factual_extraction > resolve_deadlines_pass1 > save_factual_tasks > pass2_contextual_inference > "
                             "resolve_deadlines_pass2 > dependency_audit > priority_scoring > pass3_graph_analysis > save_final_result")
    assert tags["meeting_id"] == "m1" and tags["status"] == "succeeded"
    assert metrics["step.pass1_factual_extraction.tasks_extracted"] == 1
    assert metrics["step.resolve_deadlines_pass2.resolved_to_date"] == 1
    assert metrics["step.priority_scoring.high"] + metrics["step.priority_scoring.medium"] + metrics["step.priority_scoring.low"] == 1
    assert metrics["step.pass3_graph_analysis.feasible"] == 1
    assert metrics["llm.task-inference.input_tokens"] == 500
    assert metrics["input.chars"] == len(SECRET)


def test_a_failed_pipeline_is_logged_as_failed(mlflow, monkeypatch):
    import main
    from fastapi import HTTPException
    from main import MeetingProcessRequest

    async def broken(text, meeting_date=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(main, "run_factual_extraction", broken)
    request = MeetingProcessRequest(transcript_text="Priya: hello there")
    with pytest.raises(HTTPException):
        run(main.process_meeting_from_extension(request))
    batch = [b for _, p, b, _ in mlflow.calls if p.endswith("log-batch")][0]
    tags = {t["key"]: t["value"] for t in batch["tags"]}
    assert tags["status"] == "failed" and tags["error"] == "RuntimeError"
    assert tags["step.pass1_factual_extraction.status"] == "error:RuntimeError"
    assert [b for _, p, b, _ in mlflow.calls if p.endswith("update")][0]["status"] == "FAILED"


def test_pipeline_metric_helpers():
    import pipeline_metrics as pm
    stats = pm.transcript_stats("[10:00] Priya: hi there\n[10:01] Rahul: ok\nplain line")
    assert stats["lines"] == 3 and stats["speakers"] == 2 and stats["timestamped_lines"] == 2
    tasks = [{"task_id": "T1", "explicit_prerequisites": [], "inferred_prerequisites": ["T2", "Client OK"],
              "dependency_evidence": [{"evidence": "after the dataset is ready"}, {"evidence": "made up quote"}]},
             {"task_id": "T2", "explicit_prerequisites": [], "inferred_prerequisites": []}]
    deps = pm.dependency_stats(tasks, "We start after the dataset is ready, then continue.")
    assert deps["inferred_edges"] == 2 and deps["external_blockers"] == 1
    assert deps["evidence_quotes"] == 2 and deps["evidence_grounded_rate"] == 0.5
    dl = pm.deadline_stats([{"deadline": "x", "deadline_date": "2026-01-01", "deadline_method": "calendar rule"}, {"deadline": "soon"}, {}])
    assert dl["resolved_to_date"] == 1 and dl["unresolved"] == 1 and dl["resolution_rate"] == 0.5
