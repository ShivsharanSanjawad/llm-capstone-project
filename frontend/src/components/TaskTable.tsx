import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '../services/api';
import { absoluteDeadline, dateLabel, taskStatus, tasksOf } from '../services/data';
import type { MeetingDoc, Task, TaskStatus } from '../types';
import { Badge, Empty, ErrorNotice } from './ui';
export type TaskRow = Task & { meeting: MeetingDoc };
export function allTasks(meetings: MeetingDoc[]): TaskRow[] {
  return meetings.flatMap((m) => tasksOf(m).map((t) => ({ ...t, meeting: m })));
}
export default function TaskTable({
  tasks,
  onChanged,
}: {
  tasks: TaskRow[];
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  async function update(task: TaskRow, status: TaskStatus) {
    setBusy(task.meeting._id + task.task_id);
    setError('');
    try {
      await api.status(
        task.meeting._id,
        task.task_id,
        status,
        task.meeting.task_revision || 0
      );
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy('');
    }
  }
  return (
    <>
      {error && <ErrorNotice message={error} retry={onChanged} />}{' '}
      {tasks.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Action item / source</th>
                <th>Owner</th>
                <th>Deadline</th>
                <th>Priority</th>
                <th>Dependencies</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.meeting._id + t.task_id}>
                  <td className="task-description">
                    <strong>{t.description}</strong>
                    <Link to={'/meetings/' + t.meeting._id + '?tab=transcript'}>
                      {t.task_id} ? {t.meeting.meeting_title}
                    </Link>
                    <small>{t.meeting.enriched_tasks?.length ? '' : ''}</small>
                  </td>
                  <td>
                    {t.owner || (
                      <span className="warning-text">Unassigned</span>
                    )}
                    {t.missing_owner && <small>Owner uncertain</small>}
                  </td>
                  <td>
                    {t.deadline ? (
                      absoluteDeadline(t.deadline) !== null ? (
                        <>
                          <strong>{dateLabel(t.deadline)}</strong>
                          {t.deadline_source && <small>said: “{t.deadline_source}”</small>}
                          {t.deadline_note && <small className="warning-text">{t.deadline_note}</small>}
                        </>
                      ) : (
                        t.deadline
                      )
                    ) : (
                      <span className="warning-text">Not stated</span>
                    )}
                    {t.missing_deadline && <small>Deadline uncertain</small>}
                  </td>
                  <td>
                    {t.priority ? <Badge>{t.priority}</Badge> : 'Not inferred'}
                    {typeof t.priority_score === 'number' && (
                      <>
                        <small title="Priority score out of 100">
                          <strong>{t.priority_score}</strong>/100
                        </small>
                        {t.priority_breakdown && (
                          <small
                            title={(
                              Object.entries(t.priority_breakdown) as [string, { points: number; max: number; why: string }][]
                            )
                              .map(([k, v]) => `${k}: ${v.points}/${v.max} (${v.why})`)
                              .join('\n')}
                          >
                            deadline {t.priority_breakdown.deadline.points} · blocks{' '}
                            {t.priority_breakdown.dependencies.points} · AI{' '}
                            {t.priority_breakdown.model.points}
                          </small>
                        )}
                      </>
                    )}
                  </td>
                  <td>
                    <span>
                      {t.explicit_prerequisites?.join(', ') || 'None explicit'}
                    </span>
                    {!!t.inferred_prerequisites?.length && (
                      <small>
                        Inferred: {t.inferred_prerequisites.join(', ')}
                      </small>
                    )}
                  </td>
                  <td>
                    <select
                      aria-label={'Status of ' + t.description}
                      value={taskStatus(t)}
                      disabled={!!busy}
                      onChange={(e) =>
                        void update(t, e.target.value as TaskStatus)
                      }
                    >
                      <option value="unknown" disabled>
                        Not recorded
                      </option>
                      {(
                        [
                          'pending',
                          'in progress',
                          'completed',
                          'blocked',
                        ] as const
                      ).map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="No action items found">
          Tasks appear here after meeting processing.
        </Empty>
      )}
    </>
  );
}
