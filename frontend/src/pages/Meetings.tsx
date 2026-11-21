import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Search, X, Upload, Check, LoaderCircle } from 'lucide-react';
import { api, ApiError, errorMessage } from '../services/api';
import { parseTranscriptFile, sortMeetings } from '../services/data';
import type { ProcessInput, MeetingDoc, Entry } from '../types';
import {
  PageTitle,
  useLoad,
  Loading,
  ErrorNotice,
  MeetingTable,
} from '../components/ui';
import { NewMeetingLink } from '../components/ui';
export default function Meetings() {
  const data = useLoad(async () => ({
    meetings: await api.meetings(),
    minutes: await api.minutesList(),
  }));
  const [deletingId, setDeletingId] = useState('');
  const [deleteError, setDeleteError] = useState('');
  async function deleteMeeting(meeting: MeetingDoc) {
    if (
      !window.confirm(
        `Permanently delete "${meeting.meeting_title || 'this meeting'}"? This removes its transcript, tasks, graph, all minutes revisions (including approved versions), and generation monitoring records. Make sure processing has finished. This cannot be undone.`
      )
    )
      return;
    setDeletingId(meeting._id);
    setDeleteError('');
    try {
      await api.deleteMeeting(meeting._id);
      data.reload();
    } catch (error) {
      setDeleteError(errorMessage(error));
    } finally {
      setDeletingId('');
    }
  }
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('');
  const [params, setParams] = useSearchParams();
  const list = sortMeetings(
    data.value?.meetings || [],
    data.value?.minutes
  ).filter(
    (m) =>
      (m.meeting_title + ' ' + m.participants?.join(' '))
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (!filter || m.status === filter)
  );
  return (
    <>
      <PageTitle
        title="The meeting library"
        description="A shared record of conversations, decisions and next steps."
        action={<NewMeetingLink />}
      />
      {deleteError && <ErrorNotice message={deleteError} />}
      <section className="panel">
        <div className="filters">
          <label className="search">
            <Search size={17} />
            <input
              aria-label="Search meetings"
              placeholder="Search title or participant?"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <select
            aria-label="Filter processing status"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="">All processing states</option>
            <option value="completed">Completed</option>
            <option value="factual_extracted">Factual tasks saved</option>
          </select>
          <button onClick={data.reload}>Refresh</button>
        </div>
        {data.loading ? (
          <Loading />
        ) : data.error ? (
          <ErrorNotice message={data.error} retry={data.reload} />
        ) : (
          <MeetingTable
            meetings={list}
            minutes={data.value?.minutes}
            onDelete={(meeting) => void deleteMeeting(meeting)}
            deletingId={deletingId}
          />
        )}
      </section>
      {params.get('new') === '1' && (
        <NewMeeting onClose={() => setParams({})} />
      )}
    </>
  );
}
function NewMeeting({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const dialog = useRef<HTMLDialogElement>(null);
  const [title, setTitle] = useState('');
  const [date, setDate] = useState('');
  const [people, setPeople] = useState('');
  const [text, setText] = useState('');
  const [uploaded, setUploaded] = useState<ProcessInput>();
  const [fileName, setFileName] = useState('');
  const [audioEntries, setAudioEntries] = useState<Entry[]>([]);
  const [speakerNames, setSpeakerNames] = useState(['Speaker 1', 'Speaker 2']);
  const [audioUrl, setAudioUrl] = useState('');
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState('');
  const [stage, setStage] = useState(0);
  const [meetingId, setMeetingId] = useState('');
  const [taskComplete, setTaskComplete] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(
    () => () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    },
    [audioUrl]
  );
  const busy = transcribing || (stage > 0 && stage < 3);
  async function upload(file?: File) {
    setError('');
    setUploaded(undefined);
    setFileName('');
    setAudioEntries([]);
    setAudioUrl('');
    setText('');
    if (!file) return;
    try {
      if (/\.(mp3|wav|m4a)$/i.test(file.name)) {
        if (file.size > 50 * 1024 * 1024)
          throw new Error('Choose audio smaller than 50 MB.');
        setTranscribing(true);
        const result = await api.transcribe(file);
        setAudioEntries(result.transcript);
        setSpeakerNames(['Speaker 1', 'Speaker 2']);
        setAudioUrl(URL.createObjectURL(file));
        setFileName(file.name);
        return;
      }
      if (file.size > 4_000_000)
        throw new Error('Choose a transcript smaller than 4 MB.');
      const input = parseTranscriptFile(await file.text(), file.name);
      setUploaded(input);
      setFileName(file.name);
      setText('');
      if (input.meeting?.title) setTitle(input.meeting.title);
      if (input.meeting?.participants)
        setPeople(input.meeting.participants.join(', '));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setTranscribing(false);
    }
  }
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (busy) return;
    if (
      audioEntries.length &&
      audioEntries.some((entry) => !entry.text.trim())
    ) {
      setError('Remove empty segments or enter their transcript text.');
      return;
    }
    if (!text.trim() && !uploaded && !audioEntries.length) {
      setError('Paste a transcript or upload a text, JSON or audio file.');
      return;
    }
    setStage(1);
    setTaskComplete(false);
    let saved = '';
    try {
      const input: ProcessInput = {
        ...(uploaded || {}),
        ...(text.trim()
          ? { transcript_text: text, transcript: undefined }
          : {}),
        ...(audioEntries.length
          ? {
              transcript: audioEntries.map((entry) => ({
                ...entry,
                text: entry.text.trim(),
                speaker: entry.speaker
                  ? speakerNames[
                      Number(entry.speaker.replace('speaker-', ''))
                    ]?.trim() ||
                    `Speaker ${Number(entry.speaker.replace('speaker-', '')) + 1}`
                  : null,
              })),
              transcript_text: undefined,
              processing: { source: 'audio' },
            }
          : {}),
        meeting: {
          ...uploaded?.meeting,
          title: title.trim(),
          participants: people
            .split(',')
            .map((p) => p.trim())
            .filter(Boolean),
          ...(date ? { startedAt: new Date(date).toISOString() } : {}),
        },
      };
      const result = await api.process(input);
      saved = result.meeting_id;
      setMeetingId(saved);
      setTaskComplete(true);
      setStage(2);
      await api.generate(saved);
      setStage(3);
      navigate('/meetings/' + saved);
    } catch (e) {
      const partial =
        e instanceof ApiError && typeof e.detail.meeting_id === 'string'
          ? e.detail.meeting_id
          : saved;
      if (partial) setMeetingId(partial);
      setError(errorMessage(e));
      setStage(3);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="meeting-dialog"
      onCancel={(e) => {
        if (busy) e.preventDefault();
        else onClose();
      }}
    >
      <div className="dialog-title">
        <div>
          <p className="eyebrow">START A CLEAR RECORD</p>
          <h2>New meeting</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close dialog"
          disabled={busy}
          onClick={onClose}
        >
          <X />
        </button>
      </div>
      <form onSubmit={submit}>
        <label>
          Meeting title
          <input
            required
            maxLength={200}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={busy}
          />
        </label>
        <div className="two-column">
          <label>
            Date and time <span className="muted">(optional)</span>
            <input
              type="datetime-local"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              disabled={busy}
            />
          </label>
          <label>
            Participants <span className="muted">(comma separated)</span>
            <input
              value={people}
              onChange={(e) => setPeople(e.target.value)}
              disabled={busy}
              placeholder="Priya, Rahul"
            />
          </label>
        </div>
        <label className="upload-box">
          <Upload size={23} />
          <strong>{fileName || 'Upload a transcript or meeting audio'}</strong>
          <span>
            TXT/JSON up to 4 MB; MP3/WAV/M4A up to 50 MB and 30 minutes
          </span>
          <input
            aria-label="Upload transcript"
            type="file"
            accept=".txt,.json,.mp3,.wav,.m4a"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) void upload(file);
            }}
            disabled={busy}
          />
        </label>
        {transcribing && (
          <Loading text="Transcribing audio locally. The first run downloads the model; please keep this window open." />
        )}
        {!!audioEntries.length && (
          <section aria-label="Review audio transcript">
            <h3>Review transcript and speakers</h3>
            <p className="muted small">
              Automatic speaker detection is unavailable. Listen and assign
              labels only when known; Unknown is allowed. Speaker names are
              manual labels, not verified identities. Correct recognition errors
              before generating. If speakers change inside one block, add a
              segment and move the relevant text; its timestamp stays unknown.
            </p>
            <audio
              controls
              src={audioUrl}
              aria-label="Uploaded meeting audio"
            />
            <div className="two-column">
              {speakerNames.map((name, index) => (
                <label key={index}>
                  Name for Speaker {index + 1}
                  <input
                    value={name}
                    maxLength={100}
                    disabled={busy}
                    onChange={(e) => {
                      const names = [...speakerNames];
                      names[index] = e.target.value;
                      setSpeakerNames(names);
                    }}
                  />
                </label>
              ))}
            </div>
            <button
              type="button"
              disabled={busy || speakerNames.length >= 20}
              onClick={() =>
                setSpeakerNames([
                  ...speakerNames,
                  `Speaker ${speakerNames.length + 1}`,
                ])
              }
            >
              Add speaker
            </button>
            {audioEntries.map((entry, index) => (
              <div className="review-box" key={index}>
                <label>
                  Speaker for segment {index + 1} (
                  {entry.timestamp || 'time not recorded'})
                  <select
                    disabled={busy}
                    value={entry.speaker || ''}
                    onChange={(e) =>
                      setAudioEntries((entries) =>
                        entries.map((item, i) =>
                          i === index
                            ? { ...item, speaker: e.target.value || null }
                            : item
                        )
                      )
                    }
                  >
                    <option value="">Unknown / unassigned</option>
                    {speakerNames.map((name, i) => (
                      <option key={i} value={`speaker-${i}`}>
                        {name.trim() || `Speaker ${i + 1}`}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Transcript segment {index + 1}
                  <textarea
                    rows={3}
                    required
                    maxLength={10000}
                    disabled={busy}
                    value={entry.text}
                    onChange={(e) =>
                      setAudioEntries((entries) =>
                        entries.map((item, i) =>
                          i === index ? { ...item, text: e.target.value } : item
                        )
                      )
                    }
                  />
                </label>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    setAudioEntries((entries) => [
                      ...entries.slice(0, index + 1),
                      {
                        text: '',
                        timestamp: null,
                        speaker: null,
                        source: 'audio',
                      },
                      ...entries.slice(index + 1),
                    ])
                  }
                >
                  Add segment after {index + 1}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    setAudioEntries((entries) =>
                      entries.filter((_, i) => i !== index)
                    )
                  }
                >
                  Remove segment {index + 1}
                </button>
              </div>
            ))}
          </section>
        )}
        <label>
          Or paste transcript
          <textarea
            rows={7}
            maxLength={1000000}
            placeholder="Paste the meeting conversation here?"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              if (e.target.value) {
                setUploaded(undefined);
                setFileName('');
                setAudioEntries([]);
                setAudioUrl('');
              }
            }}
            disabled={busy}
          />
        </label>
        {stage > 0 && (
          <ol className="progress" aria-live="polite">
            <li>
              {taskComplete ? (
                <Check size={16} />
              ) : stage === 1 ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <X size={16} />
              )}
              Extract tasks & analyze dependencies
            </li>
            <li>
              {stage === 2 ? <LoaderCircle className="spin" size={16} /> : null}
              Generate grounded minutes
            </li>
          </ol>
        )}
        {error && <ErrorNotice message={error} />}
        <div className="dialog-actions">
          {meetingId ? (
            <button
              type="button"
              className="primary"
              onClick={() => navigate('/meetings/' + meetingId)}
            >
              Open saved meeting & continue
            </button>
          ) : (
            <button className="primary" type="submit" disabled={busy}>
              {busy ? 'Processing?' : 'Process meeting'}
            </button>
          )}
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
        {busy && (
          <p className="small muted">
            This may take a few minutes. Keep this window open. Each stage runs
            once.
          </p>
        )}
      </form>
    </dialog>
  );
}
