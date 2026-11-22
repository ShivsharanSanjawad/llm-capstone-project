# Evaluation report

Generated 2026-11-22 20:58:04 IST · dataset `meeting-intelligence-gold-v1` (6 meetings, sha `f3350cd31f1b`) · model `openai/gpt-oss-120b`

## Headline

| Metric | Result | Note |
|---|---|---|
| Automated checks passed | **108/108** | 7 suites, no LLM needed |
| Meetings processed | **5/6** | pipeline runs that completed without an error |
| Task extraction F1 | **100%** | precision 100%, recall 100% |
| Owner accuracy | **100%** | right person on matched tasks |
| Exact deadline dates | **100%** | right calendar date on matched tasks |
| Dependency F1 | **86%** | precision 100%, recall 75% |
| Circular dependency recall | **100%** | deadlocks caught |
| Hallucinated tasks | **0%** | predicted tasks with no real source |
| Dependency quotes grounded | **100%** | evidence found verbatim in the transcript |
| Median time per meeting | **21.44s** | 4156 tokens, ~$0.00142 at paid prices |

## Automated checks (no LLM)

| Suite | Passed | Accuracy | What it checks |
|---|---|---|---|
| Deadline resolution | 33/33 | 100% | Spoken deadlines turned into exact calendar dates (meeting on Wed 25 Nov 2026) |
| Deadline reconciliation | 12/12 | 100% | Model-proposed dates validated and repaired by the calendar rules |
| Participant filtering | 28/28 | 100% | Real names kept, Google Meet interface text rejected |
| Transcript parsing | 14/14 | 100% | Speaker, timestamp and text split correctly; labels like Note/Time are not speakers |
| Dependency graph engine | 7/7 | 100% | Cycle detection, feasibility and bottleneck ranking on known graphs |
| Priority scoring | 7/7 | 100% | Properties of the 0-100 priority score (monotonic in urgency, blocking and importance) |
| Minutes evidence verification | 7/7 | 100% | Fabricated or misplaced quotes are caught; decisions need a human |

## Live model evaluation

Real model `openai/gpt-oss-120b` (groq) on 5 hand-labelled meetings; 1 case(s) failed to run.

| Metric | Value |
|---|---|
| Pipeline runs completed | 5/6 |
| Task extraction precision / recall / F1 | 100% / 100% / 100% |
| Hallucination rate | 0% |
| Owner accuracy | 100% |
| Exact deadline-date accuracy | 100% (invented deadlines: 0) |
| Dependency precision / recall / F1 | 100% / 75% / 86% |
| Circular dependency precision / recall | 100% / 100% |
| External blocker recall | 100% |
| No-task meetings left empty | 100% |
| Dependency quotes found in transcript | 100% (5 quotes) |
| Median / max seconds per meeting | 21.44 / 30.41 |
| Tokens per meeting · retries | 4156 · 6 |
| Estimated cost per meeting (paid list price) | $0.00142 |

### Per meeting

| Meeting | Tasks (found/expected) | Owners | Dates | Edges | Cycle | Seconds | Problems |
|---|---|---|---|---|---|---|---|
| sprint_planning | 3/3 (+0 extra) | 3/3 | 3/3 | 0/0 | no (gold no) | 3.03 | none |
| circular_deadlock | 2/2 (+0 extra) | 2/2 | 2/2 | 1/2 | yes (gold yes) | 3.36 | missed dependency G1 -> G2 |
| small_talk | 0/0 (+0 extra) | 0/0 | 0/0 | 0/0 | no (gold no) | 21.44 | none |
| dependency_chain | 3/3 (+0 extra) | 3/3 | 1/1 | 2/2 | no (gold no) | 30.41 | none |
| external_blocker | 2/2 (+0 extra) | 2/2 | 1/1 | 0/0 | no (gold no) | 27.13 | none |
| decisions_are_not_tasks | 0/1 (+0 extra) | 0/0 | 0/0 | 0/0 | no (gold no) | 0.79 | HTTPException: {'code': 'provider_quota_exhausted', 'request_id': '9322d7da-f080-4154-b895-bcd464f361aa', 'upstream_status': 429} |

## Method and limits

- The gold set is small and hand-written, so treat live numbers as a demonstration, not a benchmark. Add meetings to `evals/dataset.json` to grow it.
- A predicted task counts as correct when its description contains the gold keywords; owner and date are then scored on matched tasks only.
- Dependency edges are scored on tasks that were matched; a gold edge with an unmatched task counts as missed.
- Exact dates are counted from the meeting day (Wed 25 Nov 2026 in every case).
- Cost uses paid-tier list prices; the free tier is not billed.
