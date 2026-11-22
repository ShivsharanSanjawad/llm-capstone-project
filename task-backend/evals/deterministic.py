"""Evaluations that need no LLM and no network: they run in seconds and can run in CI.

Each suite returns {"name", "what", "total", "passed", "accuracy", "failures": [...]}.
"""
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

os.environ.setdefault("MONGODB_URL", "")           # evaluations must never touch a real database
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REF = date(2026, 11, 25)                           # a Wednesday; the meeting day in every case below


def _suite(name: str, what: str, checks: List[Tuple[str, Callable[[], Any], Any]]) -> Dict[str, Any]:
    failures = []
    for label, fn, expected in checks:
        try:
            got = fn()
        except Exception as exc:                   # a crash is a failure, not a reason to stop
            got = f"error: {type(exc).__name__}"
        if got != expected:
            failures.append({"case": label, "expected": expected, "got": got})
    total = len(checks)
    return {"name": name, "what": what, "total": total, "passed": total - len(failures),
            "accuracy": round((total - len(failures)) / total, 3) if total else 1.0, "failures": failures}


def deadline_resolution() -> Dict[str, Any]:
    from deadlines import resolve_deadline
    iso = lambda text: (lambda d: d.isoformat() if d else None)(resolve_deadline(text, REF))
    table = [
        ("within five days", "2026-11-30"), ("within seven days", "2026-12-02"), ("within two days", "2026-11-27"),
        ("in 3 days", "2026-11-28"), ("in two weeks", "2026-12-09"), ("in a week", "2026-12-02"),
        ("within a month", "2026-12-25"), ("in 3 business days", "2026-11-30"), ("tomorrow", "2026-11-26"),
        ("day after tomorrow", "2026-11-27"), ("today", "2026-11-25"), ("by end of day", "2026-11-25"),
        ("end of the week", "2026-11-27"), ("end of the month", "2026-11-30"), ("next month", "2026-12-25"),
        ("by Friday", "2026-11-27"), ("this Friday", "2026-11-27"), ("on Monday", "2026-11-30"), ("by Thursday", "2026-11-26"),
        ("by Wednesday", "2026-12-02"), ("this Wednesday", "2026-11-25"), ("25 November", "2026-11-25"),
        ("Nov 30th", "2026-11-30"), ("2026-12-01", "2026-12-01"), ("5 January", "2027-01-05"), ("15th of December", "2026-12-15"),
        ("within 10 days", "2026-12-05"), ("in a couple of days", "2026-11-27"), ("after approval", None),
        ("when the client replies", None), ("soon", None), ("", None), ("someday maybe", None),
    ]
    return _suite("Deadline resolution", "Spoken deadlines turned into exact calendar dates (meeting on Wed 25 Nov 2026)",
                  [(t, (lambda t=t: iso(t)), e) for t, e in table])


def deadline_reconciliation() -> Dict[str, Any]:
    """The model proposes a date; code must accept good proposals and repair bad ones."""
    from deadlines import apply_deadlines

    def settle(text, proposed):
        row = {"task_id": "T1", "deadline": text}
        if proposed:
            row["deadline_date"] = proposed
        out = apply_deadlines([row], REF)[0]
        return out.get("deadline_date")

    table = [
        ("good model date for arithmetic", "within five days", "2026-11-30", "2026-11-30"),
        ("model off by three days is repaired", "within five days", "2026-12-03", "2026-11-30"),
        ("model gives a past date, rules repair", "by Friday", "2026-11-20", "2026-11-27"),
        ("wrong weekday is repaired", "by Friday", "2026-11-26", "2026-11-27"),
        ("model silent, rules answer", "tomorrow", None, "2026-11-26"),
        ("model-only wording is kept", "by the first sprint demo", "2026-12-03", "2026-12-03"),
        ("model junk for unreadable wording is dropped", "after approval", "banana", None),
        ("far future is dropped", "after approval", "2031-01-01", None),
        ("event-based wording stays undated", "once the client replies", None, None),
        ("model's reading of 'next Friday' is accepted", "next Friday", "2026-12-04", "2026-12-04"),
        ("the nearer reading of 'next Friday' is accepted", "next Friday", "2026-11-27", "2026-11-27"),
        ("exact date said aloud", "on 28 November", "2026-11-28", "2026-11-28"),
    ]
    return _suite("Deadline reconciliation", "Model-proposed dates validated and repaired by the calendar rules",
                  [(label, (lambda t=t, m=m: settle(t, m)), e) for label, t, m, e in table])


def participant_filter() -> Dict[str, Any]:
    from main import is_valid_participant_name as ok
    people = ["Priya Sharma", "Madhukar Pai", "Shivsharan Sanjawad", "Dr. Smith", "Priya O'Neil", "Rahul", "Anna-Maria Lopez", "Wei Chen"]
    junk = ["Everyone can see your annotations", "Mute", "Turn on captions", "Pin", "Present now", "More options",
            "Raise hand", "Your main screen", "Camera", "Microphone", "Meeting details", "joined the meeting",
            "Breakout rooms", "Recording", "You", "Unknown", "Settings", "12345678", "someone@example.com", "Reframe"]
    checks = [(f"accept {n}", (lambda n=n: ok(n)), True) for n in people]
    checks += [(f"reject {n}", (lambda n=n: ok(n)), False) for n in junk]
    return _suite("Participant filtering", "Real names kept, Google Meet interface text rejected", checks)


def transcript_parsing() -> Dict[str, Any]:
    from minutes_module.transcript_processor import parse_line
    table = [
        ("[00:12] Priya: I will do it", ("Priya", "00:12", "I will do it")),
        ("Priya: I will do it", ("Priya", None, "I will do it")),
        ("[10:01:05] Rahul Sharma: Done", ("Rahul Sharma", "10:01:05", "Done")),
        ("Dr. Smith: Please review", ("Dr. Smith", None, "Please review")),
        ("Rahul (Project Manager): Ship it", ("Rahul (Project Manager)", None, "Ship it")),
        ("Priya O'Neil: Fine", ("Priya O'Neil", None, "Fine")),
        ("Note: remember the demo", (None, None, "Note: remember the demo")),
        ("Time: 5 pm sharp", (None, None, "Time: 5 pm sharp")),
        ("Action: send the report", (None, None, "Action: send the report")),
        ("just a plain line", (None, None, "just a plain line")),
        ("meanwhile the team said: relax", (None, None, "meanwhile the team said: relax")),
        ("[09:00] plain with time", (None, "09:00", "plain with time")),
        ("Agenda: budget review", (None, None, "Agenda: budget review")),
        ("Kunal - Backend: ready", ("Kunal - Backend", None, "ready")),
    ]

    def parsed(line):
        p = parse_line(line)
        return (p.get("speaker"), p.get("timestamp"), p.get("text"))

    return _suite("Transcript parsing", "Speaker, timestamp and text split correctly; labels like Note/Time are not speakers",
                  [(l, (lambda l=l: parsed(l)), e) for l, e in table])


def graph_engine() -> Dict[str, Any]:
    from graph_engine import analyze_task_graph

    def run(edges, n=4):
        tasks = [{"task_id": f"T{i}", "description": f"t{i}", "explicit_prerequisites": [], "inferred_prerequisites": []} for i in range(1, n + 1)]
        for a, b in edges:
            tasks[b - 1]["explicit_prerequisites"].append(f"T{a}")
        g = analyze_task_graph(tasks, {})
        top = g["bottlenecks"][0] if g["bottlenecks"] else {}
        return (g["is_feasible"], len(g["cycle_details"]), top.get("entity_id"), top.get("blocks_count"))

    table = [
        ("no dependencies", [], (True, 0, None, None)),
        ("simple chain", [(1, 2), (2, 3)], (True, 0, "T1", 1)),
        ("fan-out bottleneck", [(1, 2), (1, 3), (1, 4)], (True, 0, "T1", 3)),
        ("two-task cycle", [(1, 2), (2, 1)], (False, 1, "T1", 1)),
        ("three-task cycle", [(1, 2), (2, 3), (3, 1)], (False, 1, "T1", 1)),
        ("diamond is not a cycle", [(1, 2), (1, 3), (2, 4), (3, 4)], (True, 0, "T1", 2)),
        ("cycle plus a free task", [(1, 2), (2, 1), (3, 4)], (False, 1, "T1", 1)),
    ]
    return _suite("Dependency graph engine", "Cycle detection, feasibility and bottleneck ranking on known graphs",
                  [(label, (lambda e=e: run(e)), exp) for label, e, exp in table])


def priority_properties() -> Dict[str, Any]:
    """Properties the score must always satisfy, checked over a grid of situations."""
    from priority import apply_priority

    def score(importance=5, deadline=None, blocks=0, label="Medium"):
        tasks = [{"task_id": "T0", "priority": label, "llm_importance": importance, "deadline_date": deadline,
                  "explicit_prerequisites": [], "inferred_prerequisites": []}]
        for i in range(blocks):
            tasks.append({"task_id": f"B{i}", "priority": "Low", "llm_importance": 0, "deadline_date": None,
                          "explicit_prerequisites": ["T0"], "inferred_prerequisites": []})
        return apply_priority(tasks, REF)[0]["priority_score"]

    days = [date.fromordinal(REF.toordinal() + n).isoformat() for n in (0, 1, 3, 5, 10, 20, 40)]
    checks = [
        ("score stays within 0-100", lambda: all(0 <= score(i, d, b) <= 100 for i in (0, 5, 10) for d in [None] + days for b in (0, 1, 3, 6)), True),
        ("a nearer deadline never scores lower", lambda: all(score(5, days[k]) >= score(5, days[k + 1]) for k in range(len(days) - 1)), True),
        ("blocking more tasks never scores lower", lambda: all(score(5, None, b) <= score(5, None, b + 1) for b in range(0, 5)), True),
        ("higher model importance never scores lower", lambda: all(score(i) <= score(i + 1) for i in range(0, 10)), True),
        ("an undated unimportant task is Low", lambda: score(1) < 30, True),
        ("urgent, important and blocking is High", lambda: score(9, days[1], 3) >= 55, True),
        ("a missing model number falls back to the model's label", lambda: score(None, None, 0, "High") > score(None, None, 0, "Low"), True),
    ]
    return _suite("Priority scoring", "Properties of the 0-100 priority score (monotonic in urgency, blocking and importance)", checks)


def verifier_grounding() -> Dict[str, Any]:
    """The minutes verifier must catch fabricated evidence and never let a quote that is not in the transcript pass."""
    from minutes_module.schemas import Claim, Evidence, Minutes, Segment
    from minutes_module.verifier import verify
    seg = [Segment(segment_id="s1", text="We will ship the beta on Friday.", timestamp="00:10", source="t"),
           Segment(segment_id="s2", text="I think we should maybe use Postgres.", timestamp="00:20", source="t")]

    def run(text, assessment, segment, excerpt, timestamp, field="executive_summary"):
        claim = Claim(text=text, assessment=assessment, evidence=[Evidence(segment_id=segment, timestamp=timestamp, excerpt=excerpt)])
        minutes = Minutes(executive_summary=claim) if field == "executive_summary" else Minutes(
            executive_summary=Claim(text="s", assessment="supported", evidence=[Evidence(segment_id="s1", timestamp="00:10", excerpt="ship the beta")]),
            decisions=[claim])
        verify(minutes, seg)
        c = minutes.executive_summary if field == "executive_summary" else minutes.decisions[0]
        return c.assessment

    checks = [
        ("real quote stays supported", lambda: run("Beta ships Friday", "supported", "s1", "ship the beta", "00:10"), "supported"),
        ("fabricated quote is flagged", lambda: run("Beta ships Monday", "supported", "s1", "ship the beta on Monday", "00:10"), "potentially_unsupported"),
        ("unknown segment is flagged", lambda: run("x", "supported", "s9", "anything", "00:10"), "potentially_unsupported"),
        ("quote from the wrong segment is flagged", lambda: run("x", "supported", "s2", "ship the beta", "00:10"), "potentially_unsupported"),
        ("wrong timestamp is flagged", lambda: run("Beta ships Friday", "supported", "s1", "ship the beta", "09:99"), "potentially_unsupported"),
        ("a decision is never auto-confirmed", lambda: run("Use Postgres", "supported", "s2", "use Postgres", "00:20", "decisions"), "ambiguous"),
        ("a fabricated decision is flagged", lambda: run("Use Mongo", "supported", "s2", "use Mongo", "00:20", "decisions"), "potentially_unsupported"),
    ]
    return _suite("Minutes evidence verification", "Fabricated or misplaced quotes are caught; decisions need a human",
                  [(l, f, e) for l, f, e in checks])


SUITES = [deadline_resolution, deadline_reconciliation, participant_filter, transcript_parsing, graph_engine,
          priority_properties, verifier_grounding]


def run_all() -> List[Dict[str, Any]]:
    return [suite() for suite in SUITES]
