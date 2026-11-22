"""Manual dependency edits: people can add or remove a dependency and the change survives re-analysis.

Edits are stored on the meeting as {"added": [{"prerequisite", "task_id"}], "removed": [...]} and re-applied to the
model's tasks every time they are produced, so a regenerated analysis never undoes a person's correction.
"""
from typing import Any, Dict, List, Optional

MANUAL_NOTE = "Added manually in the dashboard."


def empty_edits() -> Dict[str, List[Dict[str, str]]]:
    return {"added": [], "removed": []}


def clean_edits(edits: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    result = empty_edits()
    for key in result:
        seen = set()
        for row in (edits or {}).get(key, []) or []:
            if not isinstance(row, dict):
                continue
            pair = (str(row.get("prerequisite") or "").strip(), str(row.get("task_id") or "").strip())
            if all(pair) and pair not in seen:
                seen.add(pair)
                result[key].append({"prerequisite": pair[0], "task_id": pair[1]})
    return result


def merge_edit(edits: Optional[Dict[str, Any]], add: List[Dict[str, str]], remove: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    """Fold a new request into the stored edits; the latest decision for an edge wins."""
    current = clean_edits(edits)
    added = {(r["prerequisite"], r["task_id"]) for r in current["added"]}
    removed = {(r["prerequisite"], r["task_id"]) for r in current["removed"]}
    for row in remove:
        pair = (row["prerequisite"], row["task_id"])
        added.discard(pair)
        removed.add(pair)
    for row in add:
        pair = (row["prerequisite"], row["task_id"])
        removed.discard(pair)
        added.add(pair)
    return {"added": [{"prerequisite": p, "task_id": t} for p, t in sorted(added)],
            "removed": [{"prerequisite": p, "task_id": t} for p, t in sorted(removed)]}


def apply_edits(tasks: List[Dict[str, Any]], edits: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply stored edits to enriched tasks in place (safe to run repeatedly)."""
    edits = clean_edits(edits)
    by_id = {str(t.get("task_id")): t for t in tasks if t.get("task_id")}
    for row in edits["removed"]:
        task = by_id.get(row["task_id"])
        if not task:
            continue
        for key in ("explicit_prerequisites", "inferred_prerequisites", "manual_prerequisites"):
            task[key] = [p for p in (task.get(key) or []) if p != row["prerequisite"]]
        task["dependency_evidence"] = [e for e in (task.get("dependency_evidence") or [])
                                       if e.get("prerequisite_task_id") != row["prerequisite"]]
    for row in edits["added"]:
        task = by_id.get(row["task_id"])
        if not task or row["prerequisite"] == row["task_id"]:
            continue
        existing = (task.get("explicit_prerequisites") or []) + (task.get("inferred_prerequisites") or [])
        if row["prerequisite"] not in existing:
            task["explicit_prerequisites"] = (task.get("explicit_prerequisites") or []) + [row["prerequisite"]]
        manual = task.get("manual_prerequisites") or []
        if row["prerequisite"] not in manual:
            task["manual_prerequisites"] = manual + [row["prerequisite"]]
        evidence = task.get("dependency_evidence") or []
        if row["prerequisite"] in by_id and not any(e.get("prerequisite_task_id") == row["prerequisite"] for e in evidence):
            task["dependency_evidence"] = evidence + [{"prerequisite_task_id": row["prerequisite"],
                                                       "task_id": row["task_id"], "evidence": MANUAL_NOTE}]
    return tasks
