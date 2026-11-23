import asyncio

import pytest

import main
from graph_engine import analyze_task_graph
from models import CoreInference, DependencyInference, TaskCore, TaskLinks

FACTUAL = [
    {"task_id": "T1", "description": "Finish login page design", "explicit_owner": "Shivsharan", "explicit_deadline": "within 3 days"},
    {"task_id": "T2", "description": "Finalize authentication API", "explicit_owner": "Madhukar", "explicit_deadline": "within 5 days"},
    {"task_id": "T3", "description": "Write release notes"},
]


def core(tid, **kw):
    base = dict(task_id=tid, owner="X", missing_owner=False, deadline=None, deadline_date=None, missing_deadline=True, priority="Medium")
    base.update(kw)
    return TaskCore(**base)


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def fake(kind, system, instruction, payload, model):
        seen.append({"kind": kind, "system": system, "payload": payload, "model": model})
        if model is CoreInference:
            return CoreInference(tasks=[
                core("T1", owner="Shivsharan", deadline="within 3 days", deadline_date="2026-11-28", missing_deadline=False, priority="High", llm_importance=8, priority_reason="blocks login"),
                core("T2", owner="Madhukar", deadline="within 5 days", deadline_date="2026-11-30", missing_deadline=False, llm_importance=6)])
        return DependencyInference(
            tasks=[TaskLinks(task_id="T1", explicit_prerequisites=["T2"], dependency_evidence=[{"prerequisite_task_id": "T2", "task_id": "T1", "evidence": "I need the response format"}]),
                   TaskLinks(task_id="T2", explicit_prerequisites=["T1"], inferred_prerequisites=["Client sign-off"])],
            initial_circular_dependencies=[{"from_task_id": "T1", "to_task_id": "T2", "description": "once I receive your login page design"}],
            resolved_dependencies=[])

    monkeypatch.setenv("SPLIT_ANALYSIS", "on")
    monkeypatch.setattr(main, "structured_task_call", fake)
    return seen


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_two_focused_calls_are_made_and_merged_into_one_result(calls):
    result = run(main.run_contextual_inference("transcript", FACTUAL, "2026-11-25"))
    assert [c["kind"] for c in calls] == ["task-inference-core", "task-inference-links"]
    rows = {t.task_id: t for t in result.enriched_tasks}
    assert rows["T1"].deadline_date == "2026-11-28" and rows["T1"].priority == "High" and rows["T1"].llm_importance == 8
    assert rows["T1"].explicit_prerequisites == ["T2"] and rows["T1"].dependency_evidence[0]["evidence"] == "I need the response format"
    assert rows["T2"].inferred_prerequisites == ["Client sign-off"]
    assert result.initial_circular_dependencies[0]["to_task_id"] == "T2"


def test_the_merged_result_still_yields_the_cycle(calls):
    result = run(main.run_contextual_inference("transcript", FACTUAL, "2026-11-25"))
    graph = analyze_task_graph([t.model_dump() for t in result.enriched_tasks], result.model_dump())
    assert graph["is_feasible"] is False and graph["cycle_details"]


def test_a_task_missing_from_an_answer_is_kept_with_its_extracted_facts(calls):
    result = run(main.run_contextual_inference("transcript", FACTUAL, "2026-11-25"))
    third = [t for t in result.enriched_tasks if t.task_id == "T3"][0]            # neither answer mentioned T3
    assert third.description == "Write release notes" and third.owner is None and third.missing_owner is True
    assert third.explicit_prerequisites == [] and third.priority == "Medium"
    first = [t for t in result.enriched_tasks if t.task_id == "T1"][0]
    assert first.owner == "Shivsharan"                                             # extracted owner survives if the answer lacks one


def test_each_call_gets_only_what_it_needs(calls):
    run(main.run_contextual_inference("transcript", FACTUAL, "2026-11-25"))
    core_call, links_call = calls
    assert "calendar" in core_call["payload"] and "calendar" not in links_call["payload"]
    assert "Do NOT work out dependencies" in core_call["system"] and "Do NOT work out owners" in links_call["system"]
    assert "BOTH directions" in links_call["system"]
    assert all("deadline_date" not in t for t in core_call["payload"]["factual_tasks"])


def test_the_single_call_path_still_works_when_switched_off(monkeypatch):
    monkeypatch.setenv("SPLIT_ANALYSIS", "off")
    seen = []

    async def fake(kind, system, instruction, payload, model):
        seen.append(kind)
        from models import ContextualInference
        return ContextualInference(enriched_tasks=[])

    monkeypatch.setattr(main, "structured_task_call", fake)
    run(main.run_contextual_inference("t", FACTUAL, "2026-11-25"))
    assert seen == ["task-inference"]


def test_the_split_is_on_by_default(monkeypatch):
    monkeypatch.delenv("SPLIT_ANALYSIS", raising=False)
    assert main.split_analysis_enabled() is True
