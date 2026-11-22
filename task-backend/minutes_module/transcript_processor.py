import re
from .schemas import Segment, GenerateRequest

def estimate(text: str) -> int:
    return max(1, len(text.encode("utf-8")) // 3 + 1)

def clean_transcript_text(text: str) -> str:
    """Turn escaped line breaks (a backslash and an n typed as two characters) into real ones.

    Pasted or generated transcripts sometimes arrive as one long line with "\\n" between speakers.
    """
    if not text:
        return text
    literal = text.count("\\n")
    if literal and literal > text.count("\n"):
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    return text

_WORD = r"[^\W\d_][\w.'\-]*"
_LINE = re.compile(r"^(?:\[([^\]]+)\]\s*)?(?:(" + _WORD + r"(?:\s+(?:[-\u2013]|\([^)]{1,40}\)|" + _WORD + r")){0,4}):\s+)?(.*)$")
# "Label: text" prefixes that are not people.
_NOT_SPEAKERS = {"time", "note", "notes", "action", "actions", "summary", "agenda", "topic", "decision", "decisions",
                 "todo", "deadline", "date", "update", "fyi", "reminder", "important", "question", "answer", "example"}

def parse_line(line: str) -> dict:
    """Split "[00:12] Speaker: text", "Speaker: text" or plain text into timestamp/speaker/text."""
    match = _LINE.match(line)
    if not match:
        return {"text": line}
    ts, spk, txt = match.groups()
    if spk and not spk[0].isupper():  # "meanwhile the team said: ..." is a sentence, not a speaker label
        spk, txt = None, (f"{spk}: {txt}" if txt else line)
    if spk and spk.strip().lower() in _NOT_SPEAKERS:
        spk, txt = None, f"{spk}: {txt}"  # keep the label as part of the sentence
    return {
        "text": (txt or "").strip() or line,
        "timestamp": ts.strip() if ts else None,
        "speaker": spk.strip() if spk else None,
    }

def normalize(request: GenerateRequest, max_chars: int) -> list[Segment]:
    raw = request.transcript_text or request.transcript
    if isinstance(raw, str):
        entries = []
        for line in clean_transcript_text(raw).splitlines():
            line = line.strip()
            if line:
                entries.append(parse_line(line))
    elif raw:
        entries = [e.model_dump() for e in raw]
    else:
        entries = []
    if sum(len(e["text"]) for e in entries) > max_chars:
        raise ValueError("Transcript exceeds configured character limit")
    result, offset = [], 0
    for entry in entries:
        text = entry["text"].strip()
        if not text:
            continue
        timestamp = entry.get("timestamp") or entry.get("at")
        if timestamp and not re.fullmatch(r"(?:\d+:)?[0-5]?\d:[0-5]\d(?:\.\d+)?(?:\s*[A-Za-z]{2,4})?", timestamp):
            timestamp = None
        for start in range(0, len(text), 700):
            result.append(Segment(segment_id=f"seg-{len(result)+1:06d}", text=text[start:start+700], timestamp=timestamp, speaker=entry.get("speaker"), source=entry.get("source"), offset=offset+start))
        offset += len(entry["text"]) + 1
    if not result:
        raise ValueError("Transcript is empty")
    return result

def chunks(segments: list[Segment], budget: int) -> list[list[Segment]]:
    batches, current, size = [], [], 0
    for segment in segments:
        cost = estimate(segment.model_dump_json())
        if cost > budget:
            raise ValueError("Context budget too small for a source segment")
        if current and size + cost > budget:
            batches.append(current)
            # Keep overlap small relative to the chunk budget; never duplicate a
            # large source segment merely for context. IDs/times/speakers stay intact.
            overlap = current[-1:] if estimate(current[-1].model_dump_json()) <= max(100, budget//8) else []
            current = overlap if overlap and estimate(overlap[0].model_dump_json()) + cost <= budget else []
            size = sum(estimate(s.model_dump_json()) for s in current)
        current.append(segment)
        size += cost
    if current:
        batches.append(current)
    return batches
