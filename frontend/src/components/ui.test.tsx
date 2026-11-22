import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { Claims, MeetingTable, ErrorNotice, Loading, useLoad } from './ui';
import Meetings from '../pages/Meetings';
import Assistant from '../pages/Assistant';
import { api } from '../services/api';
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe('accessible meeting states', () => {
  it('renders source excerpts and exact timestamp', () => {
    render(
      <Claims
        items={[
          {
            text: 'Ship Friday',
            assessment: 'ambiguous',
            evidence: [
              {
                segment_id: 'seg-1',
                timestamp: '00:12',
                excerpt: 'We might ship Friday.',
              },
            ],
          },
        ]}
      />
    );
    expect(screen.getByText('We might ship Friday.')).toBeInTheDocument();
    expect(screen.getByText(/00:12/)).toBeInTheDocument();
  });
  it('shows an empty library rather than invented meetings', () => {
    render(
      <MemoryRouter>
        <MeetingTable meetings={[]} />
      </MemoryRouter>
    );
    expect(
      screen.getByText('A clear record starts with a meeting')
    ).toBeInTheDocument();
  });
  it('uses accessible loading and failure messages', () => {
    render(
      <>
        <Loading />
        <ErrorNotice message="Atlas unavailable" />
      </>
    );
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Atlas unavailable');
  });
});

it('offers the optional meeting delete action', () => {
  render(
    <MemoryRouter>
      <MeetingTable
        meetings={[{ _id: 'm', meeting_title: 'A meeting' }]}
        onDelete={() => {}}
      />
    </MemoryRouter>
  );
  expect(
    screen.getByRole('button', { name: 'Delete A meeting' })
  ).toBeInTheDocument();
});

it('shows newest meetings first while preserving search, status filtering and links', async () => {
  vi.spyOn(api, 'meetings').mockResolvedValue([
    {
      _id: 'older',
      meeting_title: 'Older meeting',
      date: '2026-10-01',
      status: 'completed',
    },
    {
      _id: 'newer',
      meeting_title: 'Newest meeting',
      date: '2026-11-04',
      status: 'factual_extracted',
    },
  ]);
  vi.spyOn(api, 'minutesList').mockResolvedValue([]);
  render(
    <MemoryRouter>
      <Meetings />
    </MemoryRouter>
  );
  await screen.findByText('Newest meeting');
  expect(
    within(screen.getAllByRole('row')[1]).getByText('Newest meeting')
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Newest meeting' })).toHaveAttribute(
    'href',
    '/meetings/newer'
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Search meetings' }), {
    target: { value: 'Older' },
  });
  expect(screen.queryByText('Newest meeting')).not.toBeInTheDocument();
  expect(screen.getByText('Older meeting')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Search meetings' }), {
    target: { value: '' },
  });
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Filter processing status' }),
    { target: { value: 'completed' } }
  );
  expect(screen.queryByText('Newest meeting')).not.toBeInTheDocument();
  expect(screen.getByText('Older meeting')).toBeInTheDocument();
});

it('assistant sends the selected meeting ID and clears answers when selection changes', async () => {
  vi.spyOn(api, 'meetings').mockResolvedValue([
    { _id: 'a', meeting_title: 'Meeting A' },
    { _id: 'b', meeting_title: 'Meeting B' },
  ]);
  const query = vi
    .spyOn(api, 'query')
    .mockImplementation(async (id) => ({
      meeting_id: id,
      claims: [
        { text: 'Answer for ' + id, evidence: [], assessment: 'supported' },
      ],
      insufficient_evidence: false,
      retrieval: 'saved meeting data',
      cross_meeting: false,
    }));
  render(
    <MemoryRouter>
      <Assistant />
    </MemoryRouter>
  );
  await screen.findByRole('option', { name: 'Meeting A' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Meeting context' }), {
    target: { value: 'a' },
  });
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Ask about the selected meeting' }),
    { target: { value: 'What decisions were made?' } }
  );
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Answer for a');
  expect(query).toHaveBeenCalledWith('a', 'What decisions were made?');
  await waitFor(() =>
    expect(
      screen.getByRole('combobox', { name: 'Meeting context' })
    ).not.toBeDisabled()
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Meeting context' }), {
    target: { value: 'b' },
  });
  expect(screen.queryByText('Answer for a')).not.toBeInTheDocument();
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Ask about the selected meeting' }),
    { target: { value: 'What decisions were made?' } }
  );
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Answer for b');
  expect(query).toHaveBeenLastCalledWith('b', 'What decisions were made?');
});

function setupAudioDialog() {
  vi.spyOn(api, 'meetings').mockResolvedValue([]);
  vi.spyOn(api, 'minutesList').mockResolvedValue([]);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    },
  });
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn(() => 'blob:meeting');
      static revokeObjectURL = vi.fn();
    }
  );
  return render(
    <MemoryRouter initialEntries={['/meetings?new=1']}>
      <Routes>
        <Route path="/meetings" element={<Meetings />} />
        <Route
          path="/meetings/:id"
          element={<p>Saved audio meeting details</p>}
        />
      </Routes>
    </MemoryRouter>
  );
}

it('reviews audio, manually renames speakers, preserves unknowns and runs existing generation once', async () => {
  const transcribe = vi.spyOn(api, 'transcribe').mockResolvedValue({
    transcript: [
      {
        text: 'Release Friday.',
        timestamp: '00:00:01',
        speaker: null,
        source: 'audio',
      },
      {
        text: 'I will test it.',
        timestamp: '00:00:04',
        speaker: null,
        source: 'audio',
      },
    ],
    language: 'en',
    duration_seconds: 8,
    speaker_mode: 'manual',
  });
  const process = vi
    .spyOn(api, 'process')
    .mockResolvedValue({ meeting_id: 'audio-id', status: 'success' });
  const generate = vi
    .spyOn(api, 'generate')
    .mockResolvedValue({} as Awaited<ReturnType<typeof api.generate>>);
  setupAudioDialog();
  fireEvent.change(screen.getByLabelText('Upload transcript'), {
    target: { files: [new File(['audio'], 'meeting.m4a')] },
  });
  await screen.findByText('Review transcript and speakers');
  expect(process).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Speaker for segment 1 (00:00:01)')).toHaveValue(
    ''
  );
  fireEvent.change(screen.getByLabelText('Name for Speaker 1'), {
    target: { value: 'Priya' },
  });
  fireEvent.change(screen.getByLabelText('Speaker for segment 1 (00:00:01)'), {
    target: { value: 'speaker-0' },
  });
  fireEvent.change(screen.getByLabelText('Transcript segment 1'), {
    target: { value: 'We agreed to release Friday.' },
  });
  fireEvent.change(screen.getByLabelText('Meeting title'), {
    target: { value: 'Audio demo' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Process meeting' }));
  await screen.findByText('Saved audio meeting details');
  expect(transcribe).toHaveBeenCalledTimes(1);
  expect(process).toHaveBeenCalledTimes(1);
  expect(process.mock.calls[0][0]).toMatchObject({
    processing: { source: 'audio' },
    transcript: [
      {
        text: 'We agreed to release Friday.',
        speaker: 'Priya',
        timestamp: '00:00:01',
      },
      { speaker: null, timestamp: '00:00:04' },
    ],
  });
  expect(generate).toHaveBeenCalledExactlyOnceWith('audio-id');
});

it('keeps upload errors recoverable and prevents processing during transcription', async () => {
  let reject!: (reason: Error) => void;
  vi.spyOn(api, 'transcribe').mockImplementation(
    () =>
      new Promise((_, no) => {
        reject = no;
      })
  );
  const process = vi.spyOn(api, 'process');
  setupAudioDialog();
  fireEvent.change(screen.getByLabelText('Upload transcript'), {
    target: { files: [new File(['bad'], 'corrupt.wav')] },
  });
  expect(screen.getByRole('button', { name: 'Processing?' })).toBeDisabled();
  expect(screen.getByLabelText('Upload transcript')).toBeDisabled();
  reject(new Error('Cannot decode this audio.'));
  await screen.findByText('Cannot decode this audio.');
  expect(process).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Process meeting' })
  ).not.toBeDisabled();
});

it('allows manual speaker turns within a block without inventing timestamps', async () => {
  vi.spyOn(api, 'transcribe').mockResolvedValue({
    transcript: [
      {
        text: 'First speaker. Second speaker.',
        timestamp: '00:00:02',
        speaker: null,
        source: 'audio',
      },
    ],
    language: 'en',
    duration_seconds: 5,
    speaker_mode: 'manual',
  });
  const process = vi
    .spyOn(api, 'process')
    .mockResolvedValue({ meeting_id: 'split-id', status: 'success' });
  vi.spyOn(api, 'generate').mockResolvedValue(
    {} as Awaited<ReturnType<typeof api.generate>>
  );
  setupAudioDialog();
  fireEvent.change(screen.getByLabelText('Upload transcript'), {
    target: { files: [new File(['audio'], 'turns.wav')] },
  });
  await screen.findByText('Review transcript and speakers');
  fireEvent.click(screen.getByRole('button', { name: 'Add segment after 1' }));
  fireEvent.change(screen.getByLabelText('Transcript segment 1'), {
    target: { value: 'First speaker.' },
  });
  fireEvent.change(screen.getByLabelText('Transcript segment 2'), {
    target: { value: 'Second speaker.' },
  });
  fireEvent.change(
    screen.getByLabelText('Speaker for segment 2 (time not recorded)'),
    { target: { value: 'speaker-1' } }
  );
  fireEvent.change(screen.getByLabelText('Meeting title'), {
    target: { value: 'Two speakers' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Process meeting' }));
  await screen.findByText('Saved audio meeting details');
  expect(process.mock.calls[0][0]).toMatchObject({
    transcript: [
      { text: 'First speaker.', timestamp: '00:00:02', speaker: null },
      { text: 'Second speaker.', timestamp: null, speaker: 'Speaker 2' },
    ],
  });
});

describe('useLoad cache', () => {
  function Probe({ loader }: { loader: () => Promise<string> }) {
    const data = useLoad(loader, [], 'probe');
    return <p>{data.loading ? 'spinner' : data.value}</p>;
  }
  it('shows the previous result instantly on a later visit instead of a spinner', async () => {
    const first = vi.fn().mockResolvedValue('v1');
    const view = render(<Probe loader={first} />);
    expect(screen.getByText('spinner')).toBeTruthy();
    await screen.findByText('v1');
    view.unmount();
    const second = vi.fn().mockResolvedValue('v2');
    render(<Probe loader={second} />);
    expect(screen.getByText('v1')).toBeTruthy(); // cached value, no spinner
    expect(screen.queryByText('spinner')).toBeNull();
    await screen.findByText('v2'); // refreshed in the background
  });
});
