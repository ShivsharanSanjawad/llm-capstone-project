import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import get_meeting_doc, save_meeting_doc
from dependency_edits import MANUAL_NOTE, apply_edits, clean_edits, merge_edit
from minutes_module.config import Settings
from minutes_module.router import Runtime
from workspace_api import create_workspace_router


def task(tid, pre=(), inferred=()):
    return {"task_id": tid, "description": tid, "explicit_prerequisites": list(pre), "inferred_prerequisites": list(inferred),
            "priority": "Medium", "deadline_date": None, "llm_importance": 5, "dependency_evidence": []}


def test_apply_adds_manual_dependencies_with_a_note_and_is_idempotent():
    tasks = [task("T1"), task("T2")]
    edits = {"added": [{"prerequisite": "T1", "task_id": "T2"}], "removed": []}
    apply_edits(tasks, edits)
    apply_edits(tasks, edits)
    assert tasks[1]["explicit_prerequisites"] == ["T1"]
    assert tasks[1]["manual_prerequisites"] == ["T1"]
    assert tasks[1]["dependency_evidence"] == [{"prerequisite_task_id": "T1", "task_id": "T2", "evidence": MANUAL_NOTE}]


def test_removed_dependencies_stay_removed_after_reanalysis():
    edits = {"added": [], "removed": [{"prerequisite": "T1", "task_id": "T2"}]}
    fresh = [task("T1"), task("T2", pre=["T1"], inferred=["T1", "Client approval"])]   # the model finds it again
    apply_edits(fresh, edits)
    assert fresh[1]["explicit_prerequisites"] == []
    assert fresh[1]["inferred_prerequisites"] == ["Client approval"]


def test_latest_decision_for_an_edge_wins():
    edits = merge_edit(None, [{"prerequisite": "T1", "task_id": "T2"}], [])
    edits = merge_edit(edits, [], [{"prerequisite": "T1", "task_id": "T2"}])
    assert edits == {"added": [], "removed": [{"prerequisite": "T1", "task_id": "T2"}]}
    edits = merge_edit(edits, [{"prerequisite": "T1", "task_id": "T2"}], [])
    assert edits == {"added": [{"prerequisite": "T1", "task_id": "T2"}], "removed": []}
    assert clean_edits({"added": [{"prerequisite": "", "task_id": "T2"}, "junk"]}) == {"added": [], "removed": []}


@pytest.fixture
def client():
    runtime = Runtime(Settings(), NS(provider="mock", model="mock", configured=True))
    app = FastAPI()
    app.include_router(create_workspace_router(runtime))
    doc = {"meeting_title": "M", "date": "2026-11-22T19:41:00+05:30", "raw_transcript": "x",
           "tasks": [{"task_id": "T1", "description": "a"}],
           "enriched_tasks": [task("T1"), task("T2"), task("T3")],
           "graph_analysis": {"initial_circular_dependencies": [{"from_task_id": "T9", "to_task_id": "T8", "description": "d"}],
                              "resolved_dependencies": []}}
    meeting_id = asyncio.run(save_meeting_doc(doc))
    return TestClient(app), meeting_id


def edges(result):
    return {(e["source"], e["target"]) for e in result["graph_analysis"]["visualization_graph"]["edges"]}


def test_endpoint_adds_and_removes_and_recomputes_graph_and_priority(client):
    http, mid = client
    url = f"/api/meetings/{mid}/dependencies"
    result = http.patch(url, json={"add": [{"prerequisite": "T1", "task_id": "T2"}, {"prerequisite": "T2", "task_id": "T3"}]})
    assert result.status_code == 200, result.text
    body = result.json()
    assert edges(body) == {("T1", "T2"), ("T2", "T3")}
    scores = {t["task_id"]: t["priority_score"] for t in body["enriched_tasks"]}
    assert scores["T1"] > scores["T3"]                                   # T1 now blocks two tasks
    assert body["graph_analysis"]["initial_circular_dependencies"][0]["from_task_id"] == "T9"   # kept
    saved = asyncio.run(get_meeting_doc(mid))
    assert saved["dependency_edits"]["added"] and edges({"graph_analysis": saved["graph_analysis"]}) == edges(body)

    removed = http.patch(url, json={"remove": [{"prerequisite": "T1", "task_id": "T2"}]}).json()
    assert edges(removed) == {("T2", "T3")}


def test_endpoint_creates_a_cycle_and_reports_it(client):
    http, mid = client
    body = http.patch(f"/api/meetings/{mid}/dependencies", json={"add": [
        {"prerequisite": "T1", "task_id": "T2"}, {"prerequisite": "T2", "task_id": "T1"}]}).json()
    assert body["graph_analysis"]["is_feasible"] is False
    assert body["graph_analysis"]["circular_dependencies"]


def test_endpoint_accepts_an_external_blocker_and_rejects_bad_requests(client):
    http, mid = client
    url = f"/api/meetings/{mid}/dependencies"
    ok = http.patch(url, json={"add": [{"prerequisite": "Waiting on AWS", "task_id": "T1"}]})
    assert ok.status_code == 200
    assert any(n["id"] == "Waiting on AWS" for n in ok.json()["graph_analysis"]["visualization_graph"]["nodes"])
    assert http.patch(url, json={"add": [{"prerequisite": "T1", "task_id": "T1"}]}).status_code == 422
    assert http.patch(url, json={"add": [{"prerequisite": "T1", "task_id": "T99"}]}).status_code == 404
    assert http.patch(f"/api/meetings/nope/dependencies", json={"add": []}).status_code == 404
