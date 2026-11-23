from datetime import date, datetime, timezone, timedelta

from deadlines import analyze, apply_deadlines, calendar_hint, format_label, reference_date, resolve_deadline

SAT = date(2026, 11, 22)   # a Sunday
IST = timezone(timedelta(hours=5, minutes=30))


def d(text, ref=SAT):
    return resolve_deadline(text, ref)


def test_reference_weekday_is_known():
    assert SAT.strftime("%A") == "Sunday"


def test_within_n_days_words_and_digits():
    assert d("within five days") == date(2026, 11, 27)
    assert d("within seven days") == date(2026, 11, 29)
    assert d("within two days") == date(2026, 11, 24)
    assert d("in 3 days") == date(2026, 11, 25)
    assert d("within three days") == date(2026, 11, 25)


def test_weeks_months_and_business_days():
    assert d("in two weeks") == date(2026, 12, 6)
    assert d("in a week") == date(2026, 11, 29)
    assert d("within a month") == date(2026, 12, 22)
    assert d("in 3 business days") == date(2026, 11, 25)   # Mon, Tue, Wed after a Sunday


def test_tomorrow_today_and_end_of_periods():
    assert d("tomorrow") == date(2026, 11, 23)
    assert d("by end of day") == SAT
    assert d("end of the week") == date(2026, 11, 27)
    assert d("end of the month") == date(2026, 11, 30)
    assert d("next week") == date(2026, 11, 29)


def test_weekdays():
    assert d("by Friday") == date(2026, 11, 27)
    assert d("this Friday") == date(2026, 11, 27)
    # "next Friday" said midweek is genuinely ambiguous: both readings are offered, nearest first
    wed = date(2026, 11, 25)
    assert analyze("next Friday", wed)[0] == [date(2026, 11, 27), date(2026, 12, 4)]
    assert d("next Friday", wed) == date(2026, 11, 27)
    assert analyze("next Friday", SAT)[0] == [date(2026, 11, 27)]   # said on a Sunday there is only one reading
    assert d("Monday") == date(2026, 11, 23)
    # said on a Friday: "by Friday" means the following Friday, "this Friday" means today
    friday = date(2026, 11, 27)
    assert d("by Friday", friday) == date(2026, 12, 4)
    assert d("this Friday", friday) == friday


def test_absolute_dates():
    assert d("25 November") == date(2026, 11, 25)
    assert d("Nov 25th") == date(2026, 11, 25)
    assert d("2026-12-01") == date(2026, 12, 1)
    assert d("5 January", date(2026, 12, 20)) == date(2027, 1, 5)   # rolls into next year


def test_unresolvable_text_returns_none():
    assert d(None) is None
    assert d("") is None
    assert d("when the client approves") is None
    assert d("soon") is None


def test_label_and_reference_date():
    assert format_label(date(2026, 11, 27)) == "Fri, 27 Nov 2026"
    now = datetime(2026, 11, 22, 21, 0, tzinfo=IST)
    # 20:00 UTC on the 22nd is already the 23rd in India: the meeting day is judged in IST
    assert reference_date("2026-11-22T20:00:00+00:00", now) == date(2026, 11, 23)
    assert reference_date("2026-11-22T19:41:00+05:30", now) == date(2026, 11, 22)
    assert reference_date("not a date", now) == date(2026, 11, 22)
    assert reference_date(None, now) == date(2026, 11, 22)


def test_apply_deadlines_adds_fields_and_keeps_original_wording():
    tasks = [
        {"task_id": "T1", "deadline": "within five days"},
        {"task_id": "T2", "explicit_deadline": "by Friday"},
        {"task_id": "T3", "deadline": "after approval", "deadline_date": "stale"},
        {"task_id": "T4"},
    ]
    apply_deadlines(tasks, SAT)
    assert tasks[0]["deadline_date"] == "2026-11-27"
    assert tasks[0]["deadline_label"] == "Fri, 27 Nov 2026"
    assert tasks[0]["deadline_source"] == "within five days"
    assert tasks[0]["deadline"] == "within five days"
    assert tasks[1]["deadline_date"] == "2026-11-27"
    assert "deadline_date" not in tasks[2]      # unresolvable: stale values are removed
    assert "deadline_date" not in tasks[3]
    assert tasks[0]["deadline_method"] == "calendar rule"


def task(deadline, model_date=None):
    row = {"task_id": "T1", "deadline": deadline}
    if model_date:
        row["deadline_date"] = model_date
    return apply_deadlines([row], SAT)[0]


def test_model_date_is_used_and_confirmed_by_the_rules():
    row = task("within five days", "2026-11-27")
    assert row["deadline_date"] == "2026-11-27"
    assert row["deadline_method"] == "model, confirmed by calendar rule"


def test_wrong_model_arithmetic_is_overruled_by_the_rules():
    row = task("within five days", "2026-11-30")        # off by three days
    assert row["deadline_date"] == "2026-11-27"
    assert row["deadline_method"] == "calendar rule"


def test_model_date_for_wording_the_rules_cannot_read_is_kept():
    row = task("by the first sprint demo", "2026-12-03")
    assert row["deadline_date"] == "2026-12-03"
    assert row["deadline_method"] == "model"


def test_implausible_model_dates_are_ignored():
    assert "deadline_date" not in task("after approval", "2026-01-01")      # in the past
    assert "deadline_date" not in task("after approval", "2031-01-01")      # years away
    assert "deadline_date" not in task("after approval", "banana")
    wrong_day = task("by Friday", "2026-11-26")                             # a Thursday
    assert wrong_day["deadline_date"] == "2026-11-27"                        # weekday check fails, rules take over
    assert wrong_day["deadline_method"] == "calendar rule"


def test_ambiguous_wording_keeps_the_other_reading():
    wed = date(2026, 11, 25)
    row = apply_deadlines([{"task_id": "T1", "deadline": "next Friday", "deadline_date": "2026-12-04"}], wed)[0]
    assert row["deadline_date"] == "2026-12-04"            # the model's reading, which is one of the two
    assert row["deadline_method"] == "model, confirmed by calendar rule"
    assert "Fri, 27 Nov 2026" in row["deadline_note"]
    row = apply_deadlines([{"task_id": "T1", "deadline": "next Friday"}], wed)[0]
    assert row["deadline_date"] == "2026-11-27"            # no model answer: nearest reading
    assert "Fri, 4 Dec 2026" in row["deadline_note"]


def test_calendar_hint_lists_weekdays_from_the_meeting_day():
    hint = calendar_hint(SAT, 3)
    assert hint == "Sun 2026-11-22; Mon 2026-11-23; Tue 2026-11-24"


def test_a_stated_date_beats_a_model_that_moved_it_to_another_month():
    # Real failure: meeting on 23 Nov 2026, "January 3" came back from the model as 2026-12-03.
    from datetime import date
    from deadlines import apply_deadlines
    day = date(2026, 11, 23)
    rows = apply_deadlines([{"task_id": "T19", "explicit_deadline": "January 3", "deadline_date": "2026-12-03"},
                            {"task_id": "T2", "explicit_deadline": "December 2", "deadline_date": "2026-12-02"},
                            {"task_id": "T3", "explicit_deadline": "February 15, 2027", "deadline_date": "2027-02-14"}], day)
    assert rows[0]["deadline_date"] == "2027-01-03" and rows[0]["deadline_method"] == "calendar rule"
    assert "deadline_note" not in rows[0]                       # a stated date is not ambiguous
    assert rows[1]["deadline_date"] == "2026-12-02" and rows[1]["deadline_method"].startswith("model, confirmed")
    assert rows[2]["deadline_date"] == "2027-02-15"
