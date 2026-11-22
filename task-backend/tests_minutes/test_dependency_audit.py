import asyncio
from unittest.mock import AsyncMock

import main
from dependency_audit import merge_audit
from graph_engine import analyze_task_graph
from models import AuditedEdge, DependencyAudit

TRANSCRIPT = ("Madhukar: I'll finalize the authentication API within 5 days, once I receive your login page design.\n"
              "Shivsharan: I'll finish the login page design within 3 days, but I need the authentication API response format "
              "before I can finalize the login validation.")


def tasks():
    return [{"task_id": "T4", "description": "login page design", "owner": "Shivsharan", "explicit_prerequisites": [], "inferred_prerequisites": [], "dependency_evidence": []},
            {"task_id": "T5", "description": "authentication API", "owner": "Madhukar", "explicit_prerequisites": ["T4"], "inferred_prerequisites": [], "dependency_evidence": []}]


def edge(pre, dep, quote):
    return AuditedEdge(prerequisite_task_id=pre, task_id=dep, evidence=quote)


def test_the_missing_half_of_a_mutual_dependency_creates_a_cycle():
    rows = tasks()
    stats = merge_audit(rows, [
        edge("T4", "T5", "once I receive your login page design"),                      # already known
        edge("T5", "T4", "I need the authentication API response format before I can finalize the login validation"),
    ], TRANSCRIPT)
    assert stats == {"proposed": 2, "added": 1, "already_known": 1, "ungrounded": 0, "invalid": 0}
    assert rows[0]["inferred_prerequisites"] == ["T5"]
    assert rows[0]["dependency_evidence"][0]["evidence"].startswith("I need the authentication API")
    graph = analyze_task_graph(rows, {})
    assert graph["is_feasible"] is False and graph["cycle_details"]


def test_invented_quotes_unknown_tasks_and_self_edges_are_dropped():
    rows = tasks()
    stats = merge_audit(rows, [edge("T5", "T4", "a sentence nobody said"), edge("T9", "T4", "once I receive your login page design"),
                               edge("T4", "T4", "once I receive your login page design")], TRANSCRIPT)
    assert stats["added"] == 0 and stats["ungrounded"] == 1 and stats["invalid"] == 2
    assert rows[0]["inferred_prerequisites"] == []


def test_quote_matching_ignores_case_and_punctuation():
    rows = tasks()
    stats = merge_audit(rows, [edge("T5", "T4", "I NEED the authentication api response format -- before I can finalize the login validation!")], TRANSCRIPT)
    assert stats["added"] == 1


def test_a_failing_audit_never_fails_the_analysis(monkeypatch):
    monkeypatch.setattr(main, "run_dependency_audit", AsyncMock(side_effect=RuntimeError("provider down")))
    stats = asyncio.new_event_loop().run_until_complete(main.audit_dependencies(TRANSCRIPT, tasks()))
    assert stats["failed"] == 1 and stats["added"] == 0


def test_the_audit_runs_through_the_pipeline_helper(monkeypatch):
    monkeypatch.setattr(main, "run_dependency_audit", AsyncMock(return_value=DependencyAudit(edges=[
        edge("T5", "T4", "I need the authentication API response format before I can finalize the login validation")])))
    rows = tasks()
    stats = asyncio.new_event_loop().run_until_complete(main.audit_dependencies(TRANSCRIPT, rows))
    assert stats["added"] == 1 and rows[0]["inferred_prerequisites"] == ["T5"]
    one = asyncio.new_event_loop().run_until_complete(main.audit_dependencies(TRANSCRIPT, rows[:1]))
    assert one["proposed"] == 0            # nothing to relate with a single task
