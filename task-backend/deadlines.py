"""Turn spoken deadlines ("within five days", "by Friday") into exact calendar dates.

The model is bad at date arithmetic, so this is done in code, against the date the meeting took place
(the day the words were said). The original wording is always kept next to the computed date.
"""
import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "couple": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "thirty": 30,
}
_WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}
_WEEKDAYS.update({name.lower(): i for i, name in enumerate(calendar.day_abbr)})
_WEEKDAYS.update({"tues": 1, "thur": 3, "thurs": 3})
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9

_NUM = r"(\d{1,3}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")"
_WEEKDAY = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)"
_MONTH = r"(january|february|march|april|may|june|july|august|september|sept|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"


def _count(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBERS[token]


def _add_business_days(start: date, days: int) -> date:
    current = start
    while days > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _weekday_on_or_after(start: date, weekday: int, strictly_after: bool) -> date:
    delta = (weekday - start.weekday()) % 7
    if delta == 0 and strictly_after:
        delta = 7
    return start + timedelta(days=delta)


def _absolute(text: str, ref: date) -> Optional[date]:
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:
            return None
    forms = (
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"\b(?:,?\s+(\d{4}))?", 1, 2, 3),
        (r"\b" + _MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", 2, 1, 3),
    )
    for pattern, day_group, month_group, year_group in forms:
        match = re.search(pattern, text)
        if not match:
            continue
        day, month = int(match[day_group]), _MONTHS[match[month_group].lower()]
        year = int(match[year_group]) if match[year_group] else ref.year
        try:
            found = date(year, month, day)
        except ValueError:
            return None
        # "25 January" said in late December means next year.
        if not match[year_group] and (ref - found).days > 60:
            found = date(year + 1, month, day)
        return found
    return None


_ARITHMETIC = re.compile(
    r"\b(day after tomorrow|tomorrow|today|tonight|eod)\b|"
    r"\b(?:within|in|after|by|for|over|next)?\s*" + _NUM + r"\s+(?:business |working )?(?:days?|weeks?|months?|hours?)\b")


def analyze(text: Optional[str], reference: date) -> Tuple[List[date], bool]:
    """Candidate dates for a spoken deadline (nearest first) and whether it is plain arithmetic.

    More than one candidate means the wording is genuinely ambiguous ("next Friday" said midweek can mean
    this week's Friday or the following week's). Arithmetic phrases ("within five days") have exactly one answer.
    """
    if not text or not str(text).strip():
        return [], False
    t = str(text).casefold().strip()

    exact = _absolute(t, reference)
    if exact:
        return [exact], False

    arithmetic = bool(_ARITHMETIC.search(t))
    if re.search(r"\bday after tomorrow\b", t):
        return [reference + timedelta(days=2)], True
    if re.search(r"\btomorrow\b", t):
        return [reference + timedelta(days=1)], True
    if re.search(r"\b(today|tonight|eod|end of (the )?day|by the end of today)\b", t):
        return [reference], True

    relative = re.search(
        r"\b(?:within|in|after|by|for|over|next)?\s*" + _NUM + r"\s+(business |working )?(days?|weeks?|months?|hours?)\b", t)
    if relative:
        amount, business, unit = _count(relative[1]), relative[2], relative[3]
        if unit.startswith("hour"):
            return [reference + timedelta(days=1 if amount >= 24 else 0)], True
        if unit.startswith("day"):
            return [_add_business_days(reference, amount) if business else reference + timedelta(days=amount)], True
        if unit.startswith("week"):
            return [reference + timedelta(weeks=amount)], True
        return [_add_months(reference, amount)], True

    if re.search(r"\b(end of (the )?week|eow|this week)\b", t):
        return [reference + timedelta(days=(4 - reference.weekday()) % 7)], False
    if re.search(r"\bend of (the )?month\b", t):
        return [date(reference.year, reference.month, calendar.monthrange(reference.year, reference.month)[1])], False
    if re.search(r"\bnext month\b", t):
        return [_add_months(reference, 1)], False
    if re.search(r"\bnext week\b", t):
        return [reference + timedelta(weeks=1)], False

    weekday = re.search(r"\b(next|this|coming|upcoming)?\s*" + _WEEKDAY + r"\b", t)
    if weekday:
        modifier, target = weekday[1], _WEEKDAYS[weekday[2]]
        soonest = _weekday_on_or_after(reference, target, strictly_after=modifier != "this")
        if modifier == "next":
            following_week = reference + timedelta(days=7 - reference.weekday()) + timedelta(days=target)
            return sorted({soonest, following_week}), False
        return [soonest], False
    return [], arithmetic


def resolve_deadline(text: Optional[str], reference: date) -> Optional[date]:
    """The most likely single date for a spoken deadline (the nearest reading when it is ambiguous), or None."""
    candidates, _ = analyze(text, reference)
    return candidates[0] if candidates else None


def _named_weekday(text: str) -> Optional[int]:
    match = re.search(r"\b" + _WEEKDAY + r"\b", text.casefold())
    return _WEEKDAYS[match[1]] if match else None


def calendar_hint(reference: date, days: int = 45) -> str:
    """A compact weekday lookup for the model, so it never has to work out weekdays itself."""
    return "; ".join(f"{(reference + timedelta(days=i)).strftime('%a %Y-%m-%d')}" for i in range(days))


def format_label(value: date) -> str:
    return f"{value.strftime('%a')}, {value.day} {value.strftime('%b %Y')}"


def reference_date(value: Any, fallback: datetime) -> date:
    """The day the meeting happened, from an ISO string; falls back to `fallback` (now, in IST)."""
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is not None and fallback.tzinfo is not None:
                parsed = parsed.astimezone(fallback.tzinfo)
            return parsed.date()
        except ValueError:
            pass
    return fallback.date()


def _model_date(value: Any, source: str, reference: date) -> Optional[date]:
    """The model's answer, if it is a real date that is plausible for the spoken wording."""
    if not isinstance(value, str):
        return None
    try:
        found = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    if not reference <= found <= reference + timedelta(days=730):
        return None
    named = _named_weekday(source)
    if named is not None and found.weekday() != named:
        return None
    return found


def apply_deadlines(tasks: List[Dict[str, Any]], reference: date) -> List[Dict[str, Any]]:
    """Settle each task's exact deadline.

    The model proposes `deadline_date` (it sees the meeting date and a calendar). We accept it when it is a plausible date,
    and fall back to the calendar rules when it is missing or wrong. Plain arithmetic ("within five days") is always
    cross-checked in code. Ambiguous wording keeps the other possible dates in `deadline_note`.
    Adds deadline_date, deadline_label, deadline_source, deadline_method (and deadline_note when ambiguous).
    """
    extra = ("deadline_date", "deadline_label", "deadline_source", "deadline_method", "deadline_note")
    for task in tasks:
        source = task.get("deadline") or task.get("explicit_deadline")
        proposed = _model_date(task.get("deadline_date"), str(source or ""), reference)
        candidates, arithmetic = analyze(source, reference)
        chosen, method = None, ""
        if proposed and not candidates:
            chosen, method = proposed, "model"
        elif proposed and proposed in candidates:
            chosen, method = proposed, "model, confirmed by calendar rule"
        elif proposed and not arithmetic:
            chosen, method = proposed, "model"
        elif candidates:
            chosen, method = candidates[0], "calendar rule"
        for key in extra:
            task.pop(key, None)
        if not chosen:
            continue
        task["deadline_date"] = chosen.isoformat()
        task["deadline_label"] = format_label(chosen)
        task["deadline_source"] = str(source)
        task["deadline_method"] = method
        others = [c for c in candidates if c != chosen]
        if others:
            task["deadline_note"] = "Wording is ambiguous; it could also mean " + " or ".join(format_label(c) for c in others) + "."
    return tasks
