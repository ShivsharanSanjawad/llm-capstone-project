import { describe, it, expect } from 'vitest';
import {
  tasksOf,
  absoluteDeadline,
  parseTranscriptFile,
  sortMeetings,
  sortMeetingsBy,
  dateTimeLabel,
  cleanParticipants,
  cleanTranscriptText,
  parseTranscriptLines,
} from './data';
import type { MeetingDoc } from '../types';
describe('API data normalization', () => {
  it('preserves explicit metadata and applies persisted overrides', () => {
    const meeting: MeetingDoc = {
      _id: 'm',
      meeting_title: 'Sync',
      tasks: [
        {
          task_id: 'T1',
          description: 'Ship',
          explicit_owner: 'Priya',
          explicit_deadline: 'Friday',
        },
      ],
      task_status_overrides: [{ task_id: 'T1', status: 'blocked' }],
    };
    expect(tasksOf(meeting)[0]).toMatchObject({
      owner: 'Priya',
      deadline: 'Friday',
      status: 'blocked',
    });
  });
  it('does not invent dates from relative deadlines', () => {
    expect(absoluteDeadline('Friday')).toBeNull();
    expect(absoluteDeadline('2026-11-04')).not.toBeNull();
  });
  it('accepts timestamped JSON without losing speaker labels', () => {
    const input = parseTranscriptFile(
      '[{"text":"Release","timestamp":"00:12","speaker":"Priya"}]',
      'source.json'
    );
    expect(Array.isArray(input.transcript) && input.transcript[0].speaker).toBe(
      'Priya'
    );
  });
  it('rejects invalid files and empty transcripts', () => {
    expect(() => parseTranscriptFile('{}', 'empty.json')).toThrow();
    expect(() => parseTranscriptFile('audio', 'file.wav')).toThrow();
    expect(() => parseTranscriptFile('', 'file.txt')).toThrow();
  });
});

it('sorts by actual meeting time, puts invalid dates last and does not mutate input', () => {
  const meetings: MeetingDoc[] = [
    {
      _id: 'old',
      meeting_title: 'Old',
      date: '2026-11-01T15:00:00Z',
      created_at: '2026-11-20T10:00:00Z',
    },
    { _id: 'missing', meeting_title: 'Missing' },
    { _id: 'bad', meeting_title: 'Bad', date: '2026-02-30' },
    { _id: 'new', meeting_title: 'New', date: '2026-11-04T09:00:00+05:30' },
  ];
  expect(sortMeetings(meetings).map((m) => m._id)).toEqual([
    'new',
    'old',
    'missing',
    'bad',
  ]);
  expect(meetings.map((m) => m._id)).toEqual(['old', 'missing', 'bad', 'new']);
});

it('uses actual time rather than just the calendar day', () => {
  const meetings: MeetingDoc[] = [
    { _id: 'a', meeting_title: 'A', date: '2026-11-04T08:00:00Z' },
    { _id: 'b', meeting_title: 'B', date: '2026-11-04T14:00:00+05:30' },
  ];
  expect(sortMeetings(meetings).map((m) => m._id)).toEqual(['b', 'a']);
});

it('ranks a date-only meeting by its creation time on that day', () => {
  const meetings: MeetingDoc[] = [
    {
      _id: 'earlier',
      meeting_title: 'Earlier',
      date: '2026-11-22T16:50:06+05:30',
      created_at: '2026-11-22T16:50:06+05:30',
    },
    {
      _id: 'newest',
      meeting_title: 'Newest',
      date: '2026-11-22',
      created_at: '2026-11-22T17:02:07+05:30',
    },
  ];
  expect(sortMeetings(meetings).map((m) => m._id)).toEqual([
    'newest',
    'earlier',
  ]);
});

describe('date and time display', () => {
  it('shows the time in India time for full timestamps', () => {
    expect(dateTimeLabel('2026-11-22T17:02:07+05:30')).toBe(
      '22 Nov 2026, 5:02 pm IST'
    );
    expect(dateTimeLabel('2026-11-22T11:32:07Z')).toBe(
      '22 Nov 2026, 5:02 pm IST'
    );
  });
  it('uses the creation time for a date-only value created that day', () => {
    expect(dateTimeLabel('2026-11-22', '2026-11-22T17:02:07+05:30')).toBe(
      '22 Nov 2026, 5:02 pm IST'
    );
  });
  it('shows only the date when no time is known', () => {
    expect(dateTimeLabel('2026-11-22')).toBe('22 Nov 2026');
    expect(dateTimeLabel(undefined)).toBe('Not recorded');
  });
});

describe('sortMeetingsBy', () => {
  const meetings: MeetingDoc[] = [
    { _id: 'b', meeting_title: 'Beta', date: '2026-11-02T10:00:00+05:30', participants: ['x'] },
    { _id: 'a', meeting_title: 'alpha', date: '2026-11-03T10:00:00+05:30', participants: ['x', 'y', 'z'] },
    { _id: 'c', meeting_title: 'Gamma', date: '2026-11-01T10:00:00+05:30', tasks: [
      { task_id: 'T1', description: 'one' }, { task_id: 'T2', description: 'two' },
    ] },
    { _id: 'u', meeting_title: 'No date' },
  ];
  const ids = (sort: Parameters<typeof sortMeetingsBy>[2]) =>
    sortMeetingsBy(meetings, [], sort).map((m) => m._id);
  it('sorts by time in both directions and keeps undated meetings last', () => {
    expect(ids('newest')).toEqual(['a', 'b', 'c', 'u']);
    expect(ids('oldest')).toEqual(['c', 'b', 'a', 'u']);
  });
  it('sorts by title, action items and participants', () => {
    expect(ids('title')).toEqual(['a', 'b', 'u', 'c'].sort((x, y) => {
      const t: Record<string, string> = { a: 'alpha', b: 'beta', c: 'gamma', u: 'no date' };
      return t[x].localeCompare(t[y]);
    }));
    expect(ids('title_desc')).toEqual(['u', 'c', 'b', 'a']);
    expect(ids('tasks')[0]).toBe('c');
    expect(ids('participants')[0]).toBe('a');
  });
});

it('hides scraped Meet button text but keeps real names', () => {
  expect(
    cleanParticipants([
      'Pin Ayush Rane to your main screen',
      "You can't unmute someone else",
      'Reframe',
      '[00',
      'Shivsharan Sanjawad',
      'Ayush Rane',
      'Mute',
      "Priya O'Neil",
      'José Álvarez',
    ])
  ).toEqual(['Shivsharan Sanjawad', 'Ayush Rane', "Priya O'Neil", 'José Álvarez']);
  expect(cleanParticipants(undefined)).toEqual([]);
});

describe('transcript text handling', () => {
  const escaped =
    "[00:00] Rahul: Okay everyone, let's start.\\n\\nAmit: Sure. Deadline: Wednesday.\\n\\nKunal: That is my dependency.";
  it('turns escaped \\n into real line breaks only when needed', () => {
    expect(cleanTranscriptText(escaped).split('\n').filter(Boolean)).toHaveLength(3);
    const real = '[00:01] A: one\n[00:02] B: two';
    expect(cleanTranscriptText(real)).toBe(real);
  });
  it('splits timestamp, speaker and text', () => {
    const lines = parseTranscriptLines(escaped);
    expect(lines.map((l) => l.speaker)).toEqual(['Rahul', 'Amit', 'Kunal']);
    expect(lines[0].timestamp).toBe('00:00');
    expect(lines[1].text).toBe('Sure. Deadline: Wednesday.');
  });
  it('does not treat labels or sentences as speakers', () => {
    const [a, b, c] = parseTranscriptLines(
      "Time: 10 am sharp\nmeanwhile the team said: go\nDr. Smith: hello"
    );
    expect(a.speaker).toBeNull();
    expect(a.text).toBe('Time: 10 am sharp');
    expect(b.speaker).toBeNull();
    expect(c.speaker).toBe('Dr. Smith');
  });
});
