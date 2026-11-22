"""Task priority as a transparent 0-100 score instead of a bare label from the model.

score = deadline urgency (0-35) + how much the task blocks others (0-30) + the model's judgment (0-35)

* deadline urgency and blocking are computed in code, so they are repeatable and explainable;
* the model contributes what code cannot see: urgency words, stakes and project context (`llm_importance` 0-10);
* the label follows the score: High from 55, Medium from 30, otherwise Low.
"""
from datetime import date
from typing import Any, Dict, List, Optional

import networkx as nx

DEADLINE_MAX, BLOCKING_MAX, MODEL_MAX = 35, 30, 35
HIGH_FROM, MEDIUM_FROM = 55, 30
_LABEL_TO_IMPORTANCE = {"High": 8, "Medium": 5, "Low": 2}   # used only when the model gave no number


def deadline_points(deadline_date: Optional[str], reference: date) -> tuple[int, str]:
    try:
        due = date.fromisoformat(str(deadline_date)[:10])
    except (TypeError, ValueError):
        return 0, "no exact deadline"
    days = (due - reference).days
    if days <= 0:
        return 35, "due on the meeting day"
    for limit, points in ((2, 32), (4, 27), (7, 22), (14, 14), (30, 8)):
        if days <= limit:
            return points, f"due {days} day{'s' if days != 1 else ''} after the meeting"
    return 3, f"due {days} days after the meeting"


def blocking_points(blocked: int, in_cycle: bool) -> tuple[int, str]:
    base = {0: 0, 1: 12, 2: 20}.get(blocked, 26)
    points = min(BLOCKING_MAX, base + (4 if in_cycle else 0))
    parts = []
    if blocked:
        parts.append(f"blocks {blocked} task{'s' if blocked != 1 else ''}")
    if in_cycle:
        parts.append("part of a circular dependency")
    return points, ", ".join(parts) or "blocks nothing"


def model_points(task: Dict[str, Any]) -> tuple[int, str]:
    importance = task.get("llm_importance")
    if isinstance(importance, bool) or not isinstance(importance, (int, float)):
        label = task.get("priority_model") or task.get("priority")
        importance = _LABEL_TO_IMPORTANCE.get(label)
        if importance is None:
            return 0, "no judgment from the model"
        why = f"model label {label}"
    else:
        importance = max(0, min(10, round(importance)))
        why = f"model importance {importance}/10"
    reason = task.get("priority_reason")
    return round(importance / 10 * MODEL_MAX), why + (f": {reason}" if reason else "")


def label_for(score: int) -> str:
    return "High" if score >= HIGH_FROM else "Medium" if score >= MEDIUM_FROM else "Low"


def apply_priority(tasks: List[Dict[str, Any]], reference: date) -> List[Dict[str, Any]]:
    """Set priority (label), priority_score and priority_breakdown on every task. The model's own label is kept as priority_model."""
    ids = {str(t.get("task_id")) for t in tasks if t.get("task_id")}
    graph = nx.DiGraph()
    graph.add_nodes_from(ids)
    for task in tasks:
        for pre in (task.get("explicit_prerequisites") or []) + (task.get("inferred_prerequisites") or []):
            if pre in ids and str(task.get("task_id")) != pre:
                graph.add_edge(pre, str(task["task_id"]))        # prerequisite -> the task it blocks
    in_cycle = {node for cycle in nx.simple_cycles(graph) if len(cycle) > 1 for node in cycle}
    for task in tasks:
        tid = str(task.get("task_id"))
        if "priority_model" not in task and task.get("priority"):
            task["priority_model"] = task["priority"]
        blocked = len(nx.descendants(graph, tid) - {tid}) if tid in graph else 0
        d_points, d_why = deadline_points(task.get("deadline_date"), reference)
        b_points, b_why = blocking_points(blocked, tid in in_cycle)
        m_points, m_why = model_points(task)
        score = max(0, min(100, d_points + b_points + m_points))
        task["priority_score"] = score
        task["priority"] = label_for(score)
        task["priority_breakdown"] = {
            "deadline": {"points": d_points, "max": DEADLINE_MAX, "why": d_why},
            "dependencies": {"points": b_points, "max": BLOCKING_MAX, "why": b_why},
            "model": {"points": m_points, "max": MODEL_MAX, "why": m_why},
        }
    return tasks
