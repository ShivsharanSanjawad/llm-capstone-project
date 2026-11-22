"""Numbers describing one pipeline run, for tracing (MLflow) and evaluation. Counts and scores only: never text."""
import re
from typing import Any, Dict, List


def _words(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").casefold())).strip()


def transcript_stats(text: str) -> Dict[str, Any]:
    lines = [l for l in (text or "").splitlines() if l.strip()]
    speakers = {m.group(1).strip().casefold() for l in lines if (m := re.match(r"^(?:\[[^\]]+\]\s*)?([A-Z][\w.'\-]*(?:\s[\w.'\-]+){0,3}):\s", l))}
    return {"chars": len(text or ""), "lines": len(lines), "words": len((text or "").split()), "speakers": len(speakers),
            "timestamped_lines": sum(1 for l in lines if re.match(r"^\s*\[\d", l))}


def deadline_stats(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    said = [t for t in tasks if t.get("deadline") or t.get("explicit_deadline")]
    resolved = [t for t in said if t.get("deadline_date")]
    methods = [str(t.get("deadline_method") or "") for t in resolved]
    return {
        "tasks": len(tasks), "with_deadline_wording": len(said), "resolved_to_date": len(resolved),
        "unresolved": len(said) - len(resolved),
        "by_model_confirmed": sum(m == "model, confirmed by calendar rule" for m in methods),
        "by_model_only": sum(m == "model" for m in methods),
        "by_calendar_rule": sum(m == "calendar rule" for m in methods),
        "ambiguous": sum(1 for t in resolved if t.get("deadline_note")),
        "resolution_rate": round(len(resolved) / len(said), 3) if said else 1.0,
    }


def priority_stats(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    scores = [t["priority_score"] for t in tasks if isinstance(t.get("priority_score"), (int, float))]
    labels = [t.get("priority") for t in tasks]
    return {
        "high": labels.count("High"), "medium": labels.count("Medium"), "low": labels.count("Low"),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0, "max_score": max(scores, default=0),
        "label_changed_by_score": sum(1 for t in tasks if t.get("priority_model") and t.get("priority") != t.get("priority_model")),
        "model_gave_importance": sum(1 for t in tasks if isinstance(t.get("llm_importance"), (int, float))),
    }


def dependency_stats(tasks: List[Dict[str, Any]], transcript: str = "") -> Dict[str, Any]:
    ids = {str(t.get("task_id")) for t in tasks}
    explicit = inferred = manual = external = 0
    quotes = grounded = 0
    haystack = _words(transcript)
    for t in tasks:
        explicit += len(t.get("explicit_prerequisites") or [])
        inferred += len(t.get("inferred_prerequisites") or [])
        manual += len(t.get("manual_prerequisites") or [])
        external += sum(1 for p in (t.get("explicit_prerequisites") or []) + (t.get("inferred_prerequisites") or []) if p not in ids)
        for ev in t.get("dependency_evidence") or []:
            quote = ev.get("evidence")
            if quote and quote != "Added manually in the dashboard.":
                quotes += 1
                grounded += 1 if _words(quote) and _words(quote) in haystack else 0
    result = {"explicit_edges": explicit, "inferred_edges": inferred, "manual_edges": manual, "external_blockers": external,
              "evidence_quotes": quotes}
    if transcript and quotes:
        result["evidence_grounded_rate"] = round(grounded / quotes, 3)
    return result


def graph_stats(graph: Dict[str, Any]) -> Dict[str, Any]:
    viz = graph.get("visualization_graph") or {}
    bottlenecks = graph.get("bottlenecks") or []
    return {
        "nodes": len(viz.get("nodes") or []), "edges": len(viz.get("edges") or []),
        "cycles": len(graph.get("cycle_details") or []),
        "initial_cycles_discussed": len(graph.get("initial_circular_dependencies") or []),
        "resolved_in_meeting": len(graph.get("resolved_dependencies") or []),
        "bottlenecks": len(bottlenecks), "max_blocks": max((b.get("blocks_count", 0) for b in bottlenecks), default=0),
        "feasible": 1 if graph.get("is_feasible", True) else 0,
    }


def minutes_stats(minutes, verification: Dict[str, Any]) -> Dict[str, Any]:
    """How trustworthy a generated set of minutes looks: how many claims, how many are fully supported, how many were flagged."""
    from minutes_module.verifier import claims
    rows = [claim for _, claim in claims(minutes)]
    counts = {"supported": 0, "ambiguous": 0, "potentially_unsupported": 0, "conflicting": 0}
    for claim in rows:
        counts[claim.assessment] = counts.get(claim.assessment, 0) + 1
    evidence = sum(len(c.evidence) for c in rows)
    return {
        "claims": len(rows), "evidence_refs": evidence,
        "claims_supported": counts["supported"], "claims_ambiguous": counts["ambiguous"],
        "claims_unsupported": counts["potentially_unsupported"], "claims_conflicting": counts["conflicting"],
        "supported_rate": round(counts["supported"] / len(rows), 3) if rows else 1.0,
        "decisions": len(minutes.decisions), "follow_ups": len(minutes.follow_up_items),
        "unresolved_issues": len(minutes.unresolved_issues), "topics": len(minutes.topics),
        "flags": len((verification or {}).get("flags", [])),
        "needs_human_review": 1 if (verification or {}).get("requires_human_review") else 0,
    }
