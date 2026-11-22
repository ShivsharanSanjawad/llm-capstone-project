"""Scoring for the task-intelligence pipeline against a hand-labelled gold set. Pure functions, no network."""
import re
from typing import Any, Dict, List, Optional, Tuple


def prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").casefold()))


def _owner(task: Dict[str, Any]) -> str:
    return str(task.get("owner") or task.get("explicit_owner") or "").strip().casefold()


def match_tasks(predicted: List[Dict[str, Any]], gold: List[Dict[str, Any]]) -> Dict[str, str]:
    """Pair predicted tasks with gold tasks (one to one). A gold task is found when its keywords appear in the description.
    Returns {predicted task_id: gold id}."""
    scored: List[Tuple[float, int, int]] = []
    for pi, pred in enumerate(predicted):
        words = _tokens(pred.get("description", ""))
        for gi, g in enumerate(gold):
            keywords = [k.casefold() for k in g["keywords"]]
            hit = sum(1 for k in keywords if k in words or any(w.startswith(k) for w in words))
            if hit and hit / len(keywords) >= 0.5:
                score = hit / len(keywords) + (0.25 if _owner(pred) == g.get("owner", "").casefold() else 0)
                scored.append((score, pi, gi))
    pairs, used_p, used_g = {}, set(), set()
    for score, pi, gi in sorted(scored, reverse=True):
        if pi in used_p or gi in used_g:
            continue
        used_p.add(pi)
        used_g.add(gi)
        pairs[str(predicted[pi]["task_id"])] = gold[gi]["id"]
    return pairs


def predicted_edges(tasks: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    ids = {str(t["task_id"]) for t in tasks}
    edges = []
    for t in tasks:
        for pre in (t.get("explicit_prerequisites") or []) + (t.get("inferred_prerequisites") or []):
            if pre in ids and pre != str(t["task_id"]):
                edges.append((pre, str(t["task_id"])))
    return sorted(set(edges))


def score_case(case: Dict[str, Any], tasks: List[Dict[str, Any]], graph: Dict[str, Any]) -> Dict[str, Any]:
    """Raw counts for one case; sum them across cases (micro-average) and turn them into rates with `aggregate`."""
    gold = case["gold"]
    gold_tasks = gold["tasks"]
    pairs = match_tasks(tasks, gold_tasks)
    by_pred = {str(t["task_id"]): t for t in tasks}
    gold_by_id = {g["id"]: g for g in gold_tasks}
    gold_to_pred = {g: p for p, g in pairs.items()}

    out: Dict[str, Any] = {"case": case["id"], "predicted_tasks": len(tasks), "gold_tasks": len(gold_tasks),
                           "task_tp": len(pairs), "task_fp": len(tasks) - len(pairs), "task_fn": len(gold_tasks) - len(pairs)}
    owner_total = owner_ok = deadline_total = deadline_ok = 0
    spurious_deadlines = 0
    misses: List[str] = []
    for pid, gid in pairs.items():
        g, p = gold_by_id[gid], by_pred[pid]
        if g.get("owner"):
            owner_total += 1
            if _owner(p) == g["owner"].casefold():
                owner_ok += 1
            else:
                misses.append(f"owner of {gid}: got {_owner(p) or 'none'}, wanted {g['owner']}")
        if g.get("deadline_date"):
            deadline_total += 1
            if p.get("deadline_date") == g["deadline_date"]:
                deadline_ok += 1
            else:
                misses.append(f"deadline of {gid}: got {p.get('deadline_date')}, wanted {g['deadline_date']}")
        elif p.get("deadline_date"):
            spurious_deadlines += 1
            misses.append(f"deadline of {gid}: invented {p.get('deadline_date')}")
    out.update(owner_total=owner_total, owner_ok=owner_ok, deadline_total=deadline_total, deadline_ok=deadline_ok,
               deadline_invented=spurious_deadlines)

    gold_edges = {(a, b) for a, b in gold.get("dependencies", [])}
    pred_edges_all = predicted_edges(tasks)
    mapped = {(pairs[a], pairs[b]) for a, b in pred_edges_all if a in pairs and b in pairs}
    tp = len(mapped & gold_edges)
    out.update(edge_tp=tp, edge_fp=len(mapped - gold_edges), edge_fn=len(gold_edges - mapped))
    for a, b in sorted(gold_edges - mapped):
        misses.append(f"missed dependency {a} -> {b}")
    for a, b in sorted(mapped - gold_edges):
        misses.append(f"extra dependency {a} -> {b}")

    predicted_cycle = (graph.get("is_feasible") is False) or bool(graph.get("initial_circular_dependencies"))
    out["cycle_predicted"], out["cycle_gold"] = int(predicted_cycle), int(bool(gold.get("has_cycle")))
    if predicted_cycle != bool(gold.get("has_cycle")):
        misses.append("cycle " + ("invented" if predicted_cycle else "missed"))

    blockers = gold.get("external_blockers", {})
    found = 0
    for gid, words in blockers.items():
        pred = by_pred.get(gold_to_pred.get(gid, ""), {})
        texts = [p.casefold() for p in (pred.get("explicit_prerequisites") or []) + (pred.get("inferred_prerequisites") or []) if p not in by_pred]
        if any(w in t for w in words for t in texts):
            found += 1
        else:
            misses.append(f"external blocker for {gid} not found")
    out.update(blocker_total=len(blockers), blocker_found=found)
    out["misses"] = misses
    return out


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def total(key):
        return sum(r.get(key, 0) for r in rows)

    def rate(ok, n):
        return round(ok / n, 3) if n else None

    cycle_tp = sum(1 for r in rows if r["cycle_predicted"] and r["cycle_gold"])
    cycle_fp = sum(1 for r in rows if r["cycle_predicted"] and not r["cycle_gold"])
    cycle_fn = sum(1 for r in rows if not r["cycle_predicted"] and r["cycle_gold"])
    quiet = [r for r in rows if r["gold_tasks"] == 0]
    return {
        "task_extraction": prf(total("task_tp"), total("task_fp"), total("task_fn")),
        "hallucination_rate": rate(total("task_fp"), total("predicted_tasks")) or 0.0,
        "owner_accuracy": rate(total("owner_ok"), total("owner_total")),
        "deadline_exact_date_accuracy": rate(total("deadline_ok"), total("deadline_total")),
        "invented_deadlines": total("deadline_invented"),
        "dependency_edges": prf(total("edge_tp"), total("edge_fp"), total("edge_fn")),
        "cycle_detection": prf(cycle_tp, cycle_fp, cycle_fn),
        "external_blocker_recall": rate(total("blocker_found"), total("blocker_total")),
        "no_task_cases_clean": rate(sum(1 for r in quiet if r["predicted_tasks"] == 0), len(quiet)),
        "cases": len(rows),
    }
