from models import ContextualInference, EnrichedTask
from main import add_reported_cycle_edges


def task(tid, pre=()):
    return EnrichedTask(task_id=tid, description=tid, owner=None, missing_owner=True, deadline=None, missing_deadline=True,
                        priority="Medium", status="pending", explicit_prerequisites=list(pre))


def test_both_directions_of_a_reported_cycle_reach_the_graph():
    # Real failure: the model listed T1<->T2 as a cycle but wrote only "T2 needs T1" into the tasks.
    result = ContextualInference(
        enriched_tasks=[task("T1"), task("T2", ["T1"]), task("T3")],
        initial_circular_dependencies=[{"from_task_id": "T1", "to_task_id": "T2", "description": "a"},
                                       {"from_task_id": "T2", "to_task_id": "T1", "description": "I need your permission model"}])
    out = {t.task_id: t for t in add_reported_cycle_edges(result).enriched_tasks}
    assert out["T1"].explicit_prerequisites == ["T2"]
    assert out["T2"].explicit_prerequisites == ["T1"]                       # not duplicated
    assert out["T1"].dependency_evidence[0]["evidence"] == "I need your permission model"
    assert out["T3"].explicit_prerequisites == []


def test_a_cycle_the_meeting_resolved_is_not_put_back():
    result = ContextualInference(
        enriched_tasks=[task("T1"), task("T2", ["T1"])],
        initial_circular_dependencies=[{"from_task_id": "T2", "to_task_id": "T1", "description": "x"}],
        resolved_dependencies=[{"task_ids": "T1, T2", "resolution": "T1 goes first"}])
    out = {t.task_id: t for t in add_reported_cycle_edges(result).enriched_tasks}
    assert out["T1"].explicit_prerequisites == []


def test_unknown_or_self_links_are_ignored():
    result = ContextualInference(
        enriched_tasks=[task("T1")],
        initial_circular_dependencies=[{"from_task_id": "T9", "to_task_id": "T1"}, {"from_task_id": "T1", "to_task_id": "T1"}])
    assert add_reported_cycle_edges(result).enriched_tasks[0].explicit_prerequisites == []
