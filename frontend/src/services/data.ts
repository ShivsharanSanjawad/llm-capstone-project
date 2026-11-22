import type {
  MeetingDoc,
  Task,
  TaskStatus,
  ProcessInput,
  Revision,
} from '../types';
export function tasksOf(meeting: MeetingDoc): Task[] {
  const source = meeting.enriched_tasks?.length
    ? meeting.enriched_tasks
    : meeting.tasks || [];
  const overrides = new Map(
    meeting.task_status_overrides?.map((t) => [t.task_id, t.status])
  );
  return source.map((t) => ({
    ...t,
    owner: t.owner ?? t.explicit_owner,
    deadline: t.deadline ?? t.explicit_deadline,
    status: overrides.get(t.task_id) || t.status,
  }));
}
export const taskStatus = (task: Task): TaskStatus | 'unknown' =>
  task.status || 'unknown';
export function dateLabel(value?: string | null) {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('en-IN', {
        timeZone: 'Asia/Kolkata',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
}
// Google Meet button text that older saved meetings may carry as "participants" (mirrors the backend filter).
const UI_TEXT =
  /\b(pin|unpin|mute|unmute|reframe|remove|spotlight|present|presenting|turn (on|off)|more options|raise hand|captions?|camera|microphone|main screen|someone else|can't|cannot|you can|options|menu|settings|meeting|joined|left|waiting|admit|deny|transcript|speaker|note|notes|action|summary|agenda|topic|decision|todo)\b/i;
export function isPersonName(name: string): boolean {
  const n = name.trim();
  if (n.length < 2 || n.length > 40 || n.split(/\s+/).length > 5) return false;
  if (/^(you|me|unknown|unassigned|not recorded|not specified)$/i.test(n)) return false;
  if (UI_TEXT.test(n) || /\d{2,}|[@/\\<>{}[\]=]/.test(n)) return false;
  return /^[\p{L}][\p{L}\s.'-]*$/u.test(n);
}
/** Participants with scraped UI text removed. A list that would end up empty is returned unchanged. */
export function cleanParticipants(list?: string[] | null): string[] {
  const all = list || [];
  const kept = all.filter(isPersonName);
  return kept.length ? kept : [];
}
/** Turn escaped line breaks (a backslash and an n typed as text) into real ones. */
export function cleanTranscriptText(text: string): string {
  const literal = (text.match(/\\n/g) || []).length;
  const real = (text.match(/\n/g) || []).length;
  return literal > real ? text.replace(/\\r\\n|\\n/g, '\n') : text;
}
const NOT_SPEAKER = new Set([
  'time', 'note', 'notes', 'action', 'actions', 'summary', 'agenda', 'topic',
  'decision', 'decisions', 'todo', 'deadline', 'date', 'update', 'fyi',
  'reminder', 'important', 'question', 'answer', 'example',
]);
const WORD = "[\\p{L}][\\p{L}\\d_.'-]*";
const LINE = new RegExp(
  `^(?:\\[([^\\]]+)\\]\\s*)?(?:(${WORD}(?:\\s+(?:[-\u2013]|\\([^)]{1,40}\\)|${WORD})){0,4}):\\s+)?(.*)$`,
  'u'
);
/** Split "[00:12] Speaker: text", "Speaker: text" or plain lines into timestamp, speaker and text. */
export function parseTranscriptLines(
  text: string
): { text: string; speaker: string | null; timestamp: string | null }[] {
  return cleanTranscriptText(text)
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const m = LINE.exec(line);
      if (!m) return { text: line, speaker: null, timestamp: null };
      let [, ts, speaker, body] = m;
      if (
        speaker &&
        (!/^\p{Lu}/u.test(speaker) || NOT_SPEAKER.has(speaker.toLowerCase()))
      ) {
        body = `${speaker}: ${body}`;
        speaker = undefined as unknown as string;
      }
      return {
        text: (body || '').trim() || line,
        speaker: speaker?.trim() || null,
        timestamp: ts?.trim() || null,
      };
    });
}
const IST_ZONE = 'Asia/Kolkata';
/** "22 Nov 2026, 5:02 pm IST". A date-only value shows just the date unless the record was created that day. */
export function dateTimeLabel(value?: string | null, createdRaw?: string | null) {
  if (!value) return 'Not recorded';
  const stamp = dateWithTimeOfDay(value, createdRaw);
  if (stamp === null) return value;
  const dateOnly =
    /^\d{4}-\d{2}-\d{2}$/.test(value.trim()) && stamp === validDate(value);
  const date = new Date(stamp);
  if (dateOnly) return dateLabel(value);
  return (
    date.toLocaleString('en-IN', {
      timeZone: IST_ZONE,
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }) + ' IST'
  );
}
export function absoluteDeadline(value?: string | null): number | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(value)) return null;
  const time = Date.parse(value);
  return Number.isNaN(time) ? null : time;
}
export function parseTranscriptFile(text: string, name: string): ProcessInput {
  if (!text.trim()) throw new Error('The transcript file is empty.');
  if (name.toLowerCase().endsWith('.txt'))
    return { transcript_text: cleanTranscriptText(text) };
  if (!name.toLowerCase().endsWith('.json'))
    throw new Error(
      'Choose a .txt or .json transcript, or upload MP3, WAV or M4A meeting audio.'
    );
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error('The file is not valid JSON.');
  }
  const input = Array.isArray(data) ? { transcript: data } : data;
  if (!input || typeof input !== 'object')
    throw new Error('JSON must contain transcript_text or a transcript array.');
  const value = input as ProcessInput;
  if (typeof value.transcript_text === 'string')
    value.transcript_text = cleanTranscriptText(value.transcript_text);
  if (
    value.transcript_text &&
    typeof value.transcript_text === 'string' &&
    value.transcript_text.trim()
  )
    return value;
  if (typeof value.transcript === 'string' && value.transcript.trim())
    return value;
  if (
    Array.isArray(value.transcript) &&
    value.transcript.length &&
    value.transcript.every((e) => typeof e?.text === 'string' && e.text.trim())
  )
    return value;
  throw new Error('No valid transcript found in the JSON file.');
}

function validDate(value?: string | null): number | null {
  if (!value?.trim()) return null;
  const text = value.trim();
  const iso = /^(\d{4})-(\d{2})-(\d{2})(?:$|T| )/.exec(text);
  if (iso) {
    const calendar = new Date(
      Date.UTC(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]))
    );
    if (
      calendar.getUTCFullYear() !== Number(iso[1]) ||
      calendar.getUTCMonth() !== Number(iso[2]) - 1 ||
      calendar.getUTCDate() !== Number(iso[3])
    )
      return null;
  }
  const timestamp = Date.parse(text);
  return Number.isFinite(timestamp) ? timestamp : null;
}
// A date-only value ("2026-11-22") means midnight, which would rank a meeting below every other meeting from
// the same day that has a real time. When the record was created on that same day, use its creation time instead.
function dateWithTimeOfDay(
  raw?: string | null,
  createdRaw?: string | null
): number | null {
  const base = validDate(raw);
  if (base === null || !raw) return base;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw.trim()) && createdRaw?.startsWith(raw.trim())) {
    const created = validDate(createdRaw);
    if (created !== null) return created;
  }
  return base;
}
export function sortMeetings(
  meetings: MeetingDoc[],
  minutes: Revision[] = []
): MeetingDoc[] {
  const dates = new Map(
    minutes.map((revision) => [revision.meeting_id, revision.meeting.startedAt])
  );
  return meetings
    .map((meeting, index) => ({
      meeting,
      index,
      date:
        dateWithTimeOfDay(meeting.date, meeting.created_at) ??
        dateWithTimeOfDay(dates.get(meeting._id), meeting.created_at),
      created: validDate(meeting.created_at),
    }))
    .sort((a, b) => {
      if (a.date !== null && b.date !== null)
        return b.date - a.date || a.index - b.index;
      if (a.date !== null) return -1;
      if (b.date !== null) return 1;
      return (
        (b.created ?? -Infinity) - (a.created ?? -Infinity) || a.index - b.index
      );
    })
    .map((item) => item.meeting);
}

/** Best available point in time for a meeting, or null when it has no usable date. */
export function meetingTimestamp(
  meeting: MeetingDoc,
  minutes: Revision[] = []
): number | null {
  const started = minutes.find((r) => r.meeting_id === meeting._id)?.meeting
    .startedAt;
  return (
    dateWithTimeOfDay(meeting.date, meeting.created_at) ??
    dateWithTimeOfDay(started, meeting.created_at)
  );
}

export type MeetingSort =
  | 'newest'
  | 'oldest'
  | 'added'
  | 'title'
  | 'title_desc'
  | 'tasks'
  | 'participants';
export const meetingSortOptions: { value: MeetingSort; label: string }[] = [
  { value: 'newest', label: 'Newest meeting first' },
  { value: 'oldest', label: 'Oldest meeting first' },
  { value: 'added', label: 'Recently added' },
  { value: 'title', label: 'Title A to Z' },
  { value: 'title_desc', label: 'Title Z to A' },
  { value: 'tasks', label: 'Most action items' },
  { value: 'participants', label: 'Most participants' },
];
export function sortMeetingsBy(
  meetings: MeetingDoc[],
  minutes: Revision[] = [],
  sort: MeetingSort = 'newest'
): MeetingDoc[] {
  const newest = sortMeetings(meetings, minutes);
  const title = (m: MeetingDoc) => (m.meeting_title || 'Untitled meeting').toLowerCase();
  const created = (m: MeetingDoc) => validDate(m.created_at) ?? -Infinity;
  switch (sort) {
    case 'oldest': {
      const dated = newest.filter((m) => meetingTimestamp(m, minutes) !== null);
      const undated = newest.filter((m) => meetingTimestamp(m, minutes) === null);
      return [...dated.reverse(), ...undated];
    }
    case 'added':
      return [...newest].sort((a, b) => created(b) - created(a));
    case 'title':
      return [...newest].sort((a, b) => title(a).localeCompare(title(b)));
    case 'title_desc':
      return [...newest].sort((a, b) => title(b).localeCompare(title(a)));
    case 'tasks':
      return [...newest].sort((a, b) => tasksOf(b).length - tasksOf(a).length);
    case 'participants':
      return [...newest].sort(
        (a, b) => (b.participants?.length || 0) - (a.participants?.length || 0)
      );
    default:
      return newest;
  }
}
