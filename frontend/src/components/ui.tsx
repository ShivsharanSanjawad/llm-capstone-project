import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertCircle,
  LoaderCircle,
  ArrowUpRight,
  FileText,
  Plus,
  Trash2,
} from 'lucide-react';
import { errorMessage } from '../services/api';
import { cleanParticipants, dateTimeLabel, tasksOf } from '../services/data';
import type { Claim, MeetingDoc, Revision } from '../types';
// Pages with a cacheKey keep their last result between visits: they show it immediately and refresh quietly in
// the background (stale-while-revalidate), instead of blanking the screen with "Loading workspace" every time.
const loadCache = new Map<string, unknown>();
export function useLoad<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  cacheKey?: string
) {
  const cached = cacheKey ? (loadCache.get(cacheKey) as T | undefined) : undefined;
  const [value, setValue] = useState<T | undefined>(cached);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(cached === undefined);
  const [refreshing, setRefreshing] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    const have = cacheKey ? (loadCache.get(cacheKey) as T | undefined) : undefined;
    setError('');
    if (have !== undefined) {
      setValue(have);
      setLoading(false);
      setRefreshing(true);
    } else {
      setLoading(true);
      setValue(undefined);
    }
    loader()
      .then((v) => {
        if (cacheKey) loadCache.set(cacheKey, v);
        if (active) setValue(v);
      })
      .catch((e) => {
        // A failed background refresh keeps showing the data we already have.
        if (active && have === undefined) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) {
          setLoading(false);
          setRefreshing(false);
        }
      });
    return () => {
      active = false;
    };
  }, [...deps, version]);
  return {
    value,
    setValue,
    error,
    loading,
    refreshing,
    reload: () => setVersion((n) => n + 1),
  };
}
export function ErrorNotice({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="notice danger" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      {retry && <button onClick={retry}>Try again</button>}
    </div>
  );
}
export function Loading({ text = 'Loading workspace?' }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={20} className="spin" />
      {text}
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <FileText size={30} />
      <h3>{title}</h3>
      <div>{children}</div>
    </div>
  );
}
export function Badge({ children }: { children: ReactNode }) {
  return (
    <span
      className={'badge ' + String(children).replaceAll(' ', '-').toLowerCase()}
    >
      {children}
    </span>
  );
}
export function PageTitle({
  eyebrow = 'YOUR WORKSPACE',
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: ReactNode;
  description: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <div>{typeof description === 'string' ? <p>{description}</p> : description}</div>
      </div>
      {action}
    </header>
  );
}
export function Claims({ items, title }: { items: Claim[]; title?: string }) {
  return (
    <section className="claim-section">
      {title && <h3>{title}</h3>}
      {items.length ? (
        items.map((c, i) => (
          <article className="claim" key={i}>
            <p>{c.text}</p>
            <div className="claim-meta">
              <Badge>{c.assessment.replaceAll('_', ' ')}</Badge>
              {c.ambiguity_reason && (
                <span
                  className="claim-ambiguity-reason"
                  style={{
                    fontSize: '0.78rem',
                    color: '#8b4b45',
                    marginLeft: '8px',
                    fontStyle: 'italic',
                  }}
                >
                  Ambiguity: {c.ambiguity_reason}
                </span>
              )}
              {c.evidence.length > 0 && (
                <details>
                  <summary>
                    {c.evidence.length} source{' '}
                    {c.evidence.length === 1 ? 'reference' : 'references'}
                  </summary>
                  {c.evidence.map((e, j) => (
                    <blockquote key={j}>
                      <span>
                        {e.timestamp || 'No timestamp'} ? {e.segment_id}
                      </span>
                      <p>{e.excerpt}</p>
                    </blockquote>
                  ))}
                </details>
              )}
            </div>
          </article>
        ))
      ) : (
        <p className="muted">None recorded.</p>
      )}
    </section>
  );
}
export function MeetingTable({
  meetings,
  minutes = [],
  onDelete,
  deletingId,
}: {
  meetings: MeetingDoc[];
  minutes?: Revision[];
  onDelete?: (meeting: MeetingDoc) => void;
  deletingId?: string;
}) {
  return meetings.length ? (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Meeting</th>
            <th>Date &amp; time</th>
            <th>Participants</th>
            <th>Action items</th>
            <th>Status</th>
            <th>Minutes</th>
            <th>
              <span className="sr-only">Open</span>
            </th>
            {onDelete && <th>Delete</th>}
          </tr>
        </thead>
        <tbody>
          {meetings.map((m) => (
            <tr key={m._id}>
              <td>
                <Link className="meeting-link" to={'/meetings/' + m._id}>
                  {m.meeting_title || 'Untitled meeting'}
                </Link>
              </td>
              <td>{dateTimeLabel(m.date, m.created_at)}</td>
              <td>{cleanParticipants(m.participants).join(', ') || 'Not recorded'}</td>
              <td>{tasksOf(m).length}</td>
              <td>
                <Badge>{m.status || 'unknown'}</Badge>
              </td>
              <td>
                {minutes.find((r) => r.meeting_id === m._id)?.status ||
                  'Not generated'}
              </td>
              <td>
                <Link
                  aria-label={'Open ' + m.meeting_title}
                  to={'/meetings/' + m._id}
                >
                  <ArrowUpRight size={18} />
                </Link>
              </td>
              {onDelete && (
                <td>
                  <button
                    className="delete-button"
                    aria-label={'Delete ' + (m.meeting_title || 'meeting')}
                    disabled={!!deletingId}
                    onClick={() => onDelete(m)}
                  >
                    <Trash2 size={15} />
                    {deletingId === m._id ? 'Deleting...' : 'Delete'}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="A clear record starts with a meeting">
      Create a meeting or capture one with the Chrome extension.
    </Empty>
  );
}

export function NewMeetingLink() {
  return (
    <Link className="button primary" to="/meetings?new=1">
      <Plus size={17} />
      New meeting
    </Link>
  );
}
