"""Turn evaluation results into a Markdown report and a single-file HTML page for presenting."""
import html
from typing import Any, Dict, List, Tuple


def pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def headline(results: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """(label, big value, small caption) cards."""
    det = results["deterministic"]
    passed, total = sum(s["passed"] for s in det), sum(s["total"] for s in det)
    cards = [("Automated checks passed", f"{passed}/{total}", f"{len(det)} suites, no LLM needed")]
    live = results.get("live")
    if live:
        s = live["summary"]
        total = s["cases"] + s["failed_cases"]
        cards += [
            ("Meetings processed", f"{s['cases']}/{total}", "pipeline runs that completed without an error"),
            ("Task extraction F1", pct(s["task_extraction"]["f1"]), f"precision {pct(s['task_extraction']['precision'])}, recall {pct(s['task_extraction']['recall'])}"),
            ("Owner accuracy", pct(s["owner_accuracy"]), "right person on matched tasks"),
            ("Exact deadline dates", pct(s["deadline_exact_date_accuracy"]), "right calendar date on matched tasks"),
            ("Dependency F1", pct(s["dependency_edges"]["f1"]), f"precision {pct(s['dependency_edges']['precision'])}, recall {pct(s['dependency_edges']['recall'])}"),
            ("Circular dependency recall", pct(s["cycle_detection"]["recall"]), "deadlocks caught"),
            ("Hallucinated tasks", pct(s["hallucination_rate"]), "predicted tasks with no real source"),
            ("Dependency quotes grounded", pct(s.get("evidence_grounded_rate")), "evidence found verbatim in the transcript"),
            ("Median time per meeting", f"{s['latency_p50_s']}s" if s["latency_p50_s"] is not None else "n/a", f"{s['tokens_per_meeting']} tokens, ~${s['cost_per_meeting_usd']} at paid prices"),
        ]
    return cards


def markdown(results: Dict[str, Any]) -> str:
    out = [f"# Evaluation report", "",
           f"Generated {results['generated_at']} · dataset `{results['dataset']['name']}` ({results['dataset']['cases']} meetings, "
           f"sha `{results['dataset']['sha']}`)" + (f" · model `{results['live']['model']}`" if results.get("live") else ""), ""]
    out += ["## Headline", "", "| Metric | Result | Note |", "|---|---|---|"]
    out += [f"| {a} | **{b}** | {c} |" for a, b, c in headline(results)]
    out += ["", "## Automated checks (no LLM)", "", "| Suite | Passed | Accuracy | What it checks |", "|---|---|---|---|"]
    out += [f"| {s['name']} | {s['passed']}/{s['total']} | {pct(s['accuracy'])} | {s['what']} |" for s in results["deterministic"]]
    failures = [(s["name"], f) for s in results["deterministic"] for f in s["failures"]]
    if failures:
        out += ["", "### Failures", ""] + [f"- **{n}** · {f['case']}: expected `{f['expected']}`, got `{f['got']}`" for n, f in failures]
    live = results.get("live")
    if live:
        s = live["summary"]
        out += ["", "## Live model evaluation", "",
                f"Real model `{live['model']}` ({live['provider']}) on {s['cases']} hand-labelled meetings"
                + (f"; {s['failed_cases']} case(s) failed to run" if s["failed_cases"] else "") + ".", "",
                "| Metric | Value |", "|---|---|",
                f"| Pipeline runs completed | {s['cases']}/{s['cases'] + s['failed_cases']} |",
                f"| Task extraction precision / recall / F1 | {pct(s['task_extraction']['precision'])} / {pct(s['task_extraction']['recall'])} / {pct(s['task_extraction']['f1'])} |",
                f"| Hallucination rate | {pct(s['hallucination_rate'])} |",
                f"| Owner accuracy | {pct(s['owner_accuracy'])} |",
                f"| Exact deadline-date accuracy | {pct(s['deadline_exact_date_accuracy'])} (invented deadlines: {s['invented_deadlines']}) |",
                f"| Dependency precision / recall / F1 | {pct(s['dependency_edges']['precision'])} / {pct(s['dependency_edges']['recall'])} / {pct(s['dependency_edges']['f1'])} |",
                f"| Circular dependency precision / recall | {pct(s['cycle_detection']['precision'])} / {pct(s['cycle_detection']['recall'])} |",
                f"| External blocker recall | {pct(s['external_blocker_recall'])} |",
                f"| No-task meetings left empty | {pct(s['no_task_cases_clean'])} |",
                f"| Dependency quotes found in transcript | {pct(s.get('evidence_grounded_rate'))} ({s.get('evidence_quotes', 0)} quotes) |",
                f"| Median / max seconds per meeting | {s['latency_p50_s']} / {s['latency_max_s']} |",
                f"| Tokens per meeting · retries | {s['tokens_per_meeting']} · {s['retries']} |",
                f"| Estimated cost per meeting (paid list price) | ${s['cost_per_meeting_usd']} |", "",
                "### Per meeting", "", "| Meeting | Tasks (found/expected) | Owners | Dates | Edges | Cycle | Seconds | Problems |", "|---|---|---|---|---|---|---|---|"]
        for r in live["cases"]:
            problems = r["error"] or "; ".join(r["misses"]) or "none"
            out.append(f"| {r['case']} | {r['task_tp']}/{r['gold_tasks']} (+{r['task_fp']} extra) | {r['owner_ok']}/{r['owner_total']} | "
                       f"{r['deadline_ok']}/{r['deadline_total']} | {r['edge_tp']}/{r['edge_tp'] + r['edge_fn']} | "
                       f"{'yes' if r['cycle_predicted'] else 'no'} (gold {'yes' if r['cycle_gold'] else 'no'}) | {r['seconds']} | {problems} |")
    out += ["", "## Method and limits", "",
            "- The gold set is small and hand-written, so treat live numbers as a demonstration, not a benchmark. Add meetings to `evals/dataset.json` to grow it.",
            "- A predicted task counts as correct when its description contains the gold keywords; owner and date are then scored on matched tasks only.",
            "- Dependency edges are scored on tasks that were matched; a gold edge with an unmatched task counts as missed.",
            "- Exact dates are counted from the meeting day (Wed 25 Nov 2026 in every case).",
            "- Cost uses paid-tier list prices; the free tier is not billed.", ""]
    return "\n".join(out)


def _bar(value, label="") -> str:
    width = 0 if value is None else max(0, min(100, round(value * 100)))
    tone = "good" if width >= 90 else "mid" if width >= 70 else "low"
    return f'<div class="bar"><div class="fill {tone}" style="width:{width}%"></div></div>'


def html_page(results: Dict[str, Any]) -> str:
    e = html.escape
    cards = "".join(f'<div class="card"><small>{e(a)}</small><strong>{e(b)}</strong><span>{e(c)}</span></div>' for a, b, c in headline(results))
    suites = "".join(
        f'<tr><td>{e(s["name"])}<small>{e(s["what"])}</small></td><td>{s["passed"]}/{s["total"]}</td><td>{_bar(s["accuracy"])}</td><td>{pct(s["accuracy"])}</td></tr>'
        for s in results["deterministic"])
    live = results.get("live")
    live_html = ""
    if live:
        s = live["summary"]
        metric_rows = [("Task precision", s["task_extraction"]["precision"]), ("Task recall", s["task_extraction"]["recall"]),
                       ("Owner accuracy", s["owner_accuracy"]), ("Exact deadline dates", s["deadline_exact_date_accuracy"]),
                       ("Dependency precision", s["dependency_edges"]["precision"]), ("Dependency recall", s["dependency_edges"]["recall"]),
                       ("Circular dependency recall", s["cycle_detection"]["recall"]), ("External blocker recall", s["external_blocker_recall"]),
                       ("Evidence quotes grounded", s.get("evidence_grounded_rate")), ("No-task meetings left empty", s["no_task_cases_clean"])]
        bars = "".join(f'<tr><td>{e(n)}</td><td>{_bar(v)}</td><td>{pct(v)}</td></tr>' for n, v in metric_rows)
        cases = "".join(
            f'<tr><td>{e(r["case"])}</td><td>{r["task_tp"]}/{r["gold_tasks"]}</td><td>{r["owner_ok"]}/{r["owner_total"]}</td>'
            f'<td>{r["deadline_ok"]}/{r["deadline_total"]}</td><td>{r["edge_tp"]}/{r["edge_tp"] + r["edge_fn"]}</td><td>{r["seconds"]}s</td>'
            f'<td class="small">{e(r["error"] or "; ".join(r["misses"]) or "all correct")}</td></tr>' for r in live["cases"])
        live_html = (f'<h2>Live model: {e(live["model"])}</h2><table>{bars}</table>'
                     f'<h2>Per meeting</h2><table><tr><th>Meeting</th><th>Tasks</th><th>Owners</th><th>Dates</th><th>Edges</th><th>Time</th><th>Notes</th></tr>{cases}</table>')
    failures = "".join(f'<li><b>{e(s["name"])}</b>: {e(str(f["case"]))} (expected {e(str(f["expected"]))}, got {e(str(f["got"]))})</li>'
                       for s in results["deterministic"] for f in s["failures"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evaluation report</title><style>
:root{{--bg:#f7f4ec;--ink:#29271f;--muted:#706a5e;--card:#fcfaf5;--line:#d4ccbd;--good:#47765b;--mid:#b08a2e;--low:#b4544b}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1d1b17;--ink:#ece7da;--muted:#a39d8f;--card:#27241f;--line:#3d392f}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}}main{{max-width:1040px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-size:2rem;margin:0 0 4px}}h2{{margin:36px 0 12px}}.meta{{color:var(--muted);font-size:.85rem}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px}}.card small{{display:block;color:var(--muted)}}
.card strong{{display:block;font-size:2rem;line-height:1.2;margin:4px 0}}.card span{{font-size:.78rem;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px}}
td,th{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}td small{{display:block;color:var(--muted)}}.small{{font-size:.78rem;color:var(--muted)}}
.bar{{background:var(--line);border-radius:6px;height:10px;min-width:120px}}.fill{{height:10px;border-radius:6px}}.good{{background:var(--good)}}.mid{{background:var(--mid)}}.low{{background:var(--low)}}
@media(max-width:600px){{td,th{{padding:8px 6px;font-size:.82rem}}}}
</style></head><body><main>
<h1>Evaluation report</h1>
<p class="meta">Generated {e(results["generated_at"])} · dataset {e(results["dataset"]["name"])} ({results["dataset"]["cases"]} meetings)</p>
<div class="cards">{cards}</div>
<h2>Automated checks (no LLM)</h2><table>{suites}</table>
{"<ul>" + failures + "</ul>" if failures else ""}
{live_html}
<p class="meta">The gold set is small and hand-labelled: treat live numbers as a demonstration, not a benchmark. Cost uses paid-tier list prices.</p>
</main></body></html>"""
