import { useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { Graph, Task } from '../types';
import { Empty } from './ui';
import { api, errorMessage } from '../services/api';
export default function DependencyGraph({
  graph,
  tasks,
  meetingId,
  onChanged,
}: {
  graph?: Graph;
  tasks: Task[];
  meetingId?: string;
  onChanged?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState('');
  const [prerequisite, setPrerequisite] = useState('');
  const [blocker, setBlocker] = useState('');
  const [dependent, setDependent] = useState('');
  const [selected, setSelected] = useState<{ source: string; target: string }>();
  const taskIds = tasks.map((t) => t.task_id);
  async function save(changes: Parameters<typeof api.editDependencies>[1]) {
    if (!meetingId || saving) return;
    setSaving(true);
    setProblem('');
    try {
      await api.editDependencies(meetingId, changes);
      setSelected(undefined);
      setBlocker('');
      onChanged?.();
    } catch (e) {
      setProblem(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }
  const addDependency = (from: string, to: string) => {
    const pre = from.trim();
    if (!pre || !to) return setProblem('Choose a prerequisite and the task it blocks.');
    if (pre === to) return setProblem('A task cannot depend on itself.');
    void save({ add: [{ prerequisite: pre, task_id: to }] });
  };
  const onConnect = (c: Connection) => {
    if (c.source && c.target && taskIds.includes(c.target)) addDependency(c.source, c.target);
  };
  const model = useMemo(() => {
    const source = graph?.visualization_graph;
    const nodes: Node[] = (source?.nodes || []).map((n, i) => {
      const task = tasks.find((t) => t.task_id === n.id);
      const status = task?.status || n.data.status || 'pending';
      const critical = graph?.bottlenecks.some(
        (b) => b.entity_id === n.id && b.blocks_count > 1
      );
      const color =
        status === 'completed'
          ? '#47765b'
          : status === 'blocked'
            ? '#b4544b'
            : critical
              ? '#8b713a'
              : '#d4ccbd';
      return {
        id: n.id,
        position: { x: (i % 3) * 280, y: Math.floor(i / 3) * 170 },
        data: {
          label: (
            <div>
              <small>
                {n.data.is_external_blocker ? 'EXTERNAL BLOCKER' : n.id} ?{' '}
                {status}
              </small>
              <p>{task?.description || n.data.description || n.id}</p>
              {critical && <small>Critical blocker</small>}
            </div>
          ),
        },
        style: {
          borderColor: color,
          borderWidth: 2,
          background: '#fcfaf5',
          width: 240,
          padding: 12,
        },
      };
    });
    const edges: Edge[] = (source?.edges || []).map((e, i) => {
      const task = tasks.find((t) => t.task_id === e.target);
      const manual = !!task?.manual_prerequisites?.includes(e.source);
      const inferred =
        !manual &&
        !!task?.inferred_prerequisites?.includes(e.source) &&
        !task?.explicit_prerequisites?.includes(e.source);
      const isCycleEdge = !!graph?.circular_dependencies?.some(
        (c) => c.from === e.source && c.to === e.target
      );
      const strokeColor = isCycleEdge ? '#b4544b' : '#8b713a';
      return {
        id: 'edge-' + i,
        source: e.source,
        target: e.target,
        label: isCycleEdge ? 'cycle' : manual ? 'manual' : inferred ? 'inferred' : undefined,
        style: {
          stroke: strokeColor,
          strokeWidth: isCycleEdge || (selected && selected.source === e.source && selected.target === e.target) ? 3 : 1.5,
          strokeDasharray: !isCycleEdge && inferred ? '5 4' : undefined,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: strokeColor },
      };
    });
    return { nodes, edges };
  }, [graph, tasks, selected]);
  const [nodes, setNodes, onNodesChange] = useNodesState(model.nodes);
  useEffect(() => setNodes(model.nodes), [model, setNodes]);
  if (!model.nodes.length)
    return (
      <Empty title="No dependency graph available">
        Process action items to see their relationships.
      </Empty>
    );

  const hasUnresolvedCycles =
    graph?.is_feasible === false ||
    (graph?.circular_dependencies && graph.circular_dependencies.length > 0);
  const hasResolvedCycles =
    !!graph?.has_resolved_circular_dependencies ||
    (!!graph?.initial_circular_dependencies?.length &&
      !!graph?.resolved_dependencies?.length);

  return (
    <>
      {!model.edges.length && (
        <p className="notice">
          No dependencies were detected. Individual tasks are shown below.
        </p>
      )}
      <p className="muted">
        Drag nodes, pan and zoom. Dashed edges indicate inferred dependencies.
        Red edges indicate circular deadlocks.
        {meetingId
          ? ' Use Edit dependencies to add or remove relationships; your changes are kept if the meeting is re-analysed.'
          : ' Relationships are read-only.'}
      </p>
      {meetingId && (
        <div className="dep-editor">
          <button
            type="button"
            className={editing ? 'primary' : ''}
            onClick={() => {
              setEditing(!editing);
              setProblem('');
              setSelected(undefined);
            }}
          >
            {editing ? 'Done editing' : 'Edit dependencies'}
          </button>
          {editing && (
            <div className="dep-editor-panel">
              <p className="small muted">
                Drag from the bottom handle of a prerequisite to the top handle of the task it blocks, click an arrow
                to select it, or use the form.
              </p>
              <form
                className="dep-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  addDependency(blocker || prerequisite, dependent);
                }}
              >
                <label>
                  Prerequisite (must finish first)
                  <select
                    value={prerequisite}
                    disabled={!!blocker}
                    onChange={(e) => setPrerequisite(e.target.value)}
                  >
                    <option value="">Choose a task…</option>
                    {taskIds.map((id) => (
                      <option key={id} value={id}>
                        {id}: {tasks.find((t) => t.task_id === id)?.description.slice(0, 50)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  …or an outside blocker
                  <input
                    value={blocker}
                    maxLength={120}
                    placeholder="e.g. Client approval"
                    onChange={(e) => setBlocker(e.target.value)}
                  />
                </label>
                <label>
                  Blocks this task
                  <select value={dependent} onChange={(e) => setDependent(e.target.value)}>
                    <option value="">Choose a task…</option>
                    {taskIds.map((id) => (
                      <option key={id} value={id}>
                        {id}: {tasks.find((t) => t.task_id === id)?.description.slice(0, 50)}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="submit" className="primary" disabled={saving}>
                  {saving ? 'Saving…' : 'Add dependency'}
                </button>
              </form>
              {selected && (
                <div className="notice warning">
                  Selected: <strong>{selected.source} ➔ {selected.target}</strong>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() =>
                      save({ remove: [{ prerequisite: selected.source, task_id: selected.target }] })
                    }
                  >
                    Remove this dependency
                  </button>
                </div>
              )}
              <ul className="dep-list">
                {model.edges.length === 0 && <li className="muted small">No dependencies yet.</li>}
                {model.edges.map((e) => (
                  <li key={e.id}>
                    <span>
                      <strong>{e.source}</strong> ➔ <strong>{e.target}</strong>
                      {e.label ? <small> {String(e.label)}</small> : null}
                    </span>
                    <button
                      type="button"
                      aria-label={`Remove dependency ${e.source} to ${e.target}`}
                      disabled={saving}
                      onClick={() => save({ remove: [{ prerequisite: e.source, task_id: e.target }] })}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
              {problem && (
                <div className="notice danger" role="alert">
                  {problem}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Feasibility Status Banner */}
      {hasUnresolvedCycles ? (
        <div className="notice danger" role="alert">
          <strong>⚠ Schedule Infeasible – Circular Dependency Detected!</strong>
        </div>
      ) : hasResolvedCycles ? (
        <div className="notice warning">
          <strong>
            ✓ Schedule Feasible – Initial circular dependencies were resolved during discussion
          </strong>
        </div>
      ) : (
        <div
          className="notice"
          style={{
            background: '#edf5ee',
            borderColor: '#c4ddc6',
            color: '#27522d',
          }}
        >
          <strong>✓ Schedule Feasible – No Circular Deadlocks</strong>
        </div>
      )}

      {/* Initial Cycles discussed */}
      {!!graph?.initial_circular_dependencies?.length && (
        <div className="notice warning" style={{ display: 'block' }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 6,
            }}
          >
            <strong>Initial circular dependencies detected</strong>
            <span
              style={{
                background: '#806432',
                color: '#fff',
                padding: '1px 8px',
                borderRadius: 10,
                fontSize: '0.7rem',
                fontWeight: 600,
              }}
            >
              {graph.initial_circular_dependencies.length}
            </span>
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: '0.8rem' }}>
            {graph.initial_circular_dependencies.map((cycle, idx) => (
              <li key={idx} style={{ marginBottom: 4 }}>
                <strong>
                  {cycle.from_task_id || 'Task'} ➔ {cycle.to_task_id || 'Task'}
                </strong>
                {cycle.description && (
                  <span
                    style={{
                      display: 'block',
                      color: 'var(--muted)',
                      fontStyle: 'italic',
                    }}
                  >
                    Transcript evidence: {cycle.description}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Meeting Resolutions */}
      {!!graph?.resolved_dependencies?.length && (
        <div
          className="notice"
          style={{
            display: 'block',
            background: '#edf5ee',
            borderColor: '#c4ddc6',
            color: '#27522d',
          }}
        >
          <strong style={{ display: 'block', marginBottom: 6 }}>
            Meeting resolution:
          </strong>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: '0.8rem' }}>
            {graph.resolved_dependencies.map((res, idx) => (
              <li key={idx} style={{ marginBottom: 4 }}>
                {typeof res === 'object' && res && 'description' in res
                  ? String(res.description)
                  : JSON.stringify(res)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Active Unresolved Cycle Details with evidence */}
      {!!graph?.cycle_details?.length ? (
        <div className="notice danger" style={{ display: 'block' }}>
          <strong style={{ display: 'block', marginBottom: 6 }}>
            Detected cycles and supporting transcript statements:
          </strong>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: '0.8rem' }}>
            {graph.cycle_details.map((cycle, idx) => (
              <li key={idx} style={{ marginBottom: 6 }}>
                <strong>
                  Cycle {idx + 1}: {cycle.task_ids.join(' ➔ ')}
                </strong>
                <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
                  {cycle.dependencies.map((dep, depIdx) => (
                    <li key={depIdx}>
                      {dep.prerequisite_task_id} ➔ {dep.task_id}:{' '}
                      {dep.evidence
                        ? `“${dep.evidence}”`
                        : 'No transcript evidence returned.'}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      ) : hasUnresolvedCycles && !!graph?.circular_dependencies?.length ? (
        <div className="notice danger">
          <strong>Circular dependencies detected:</strong>{' '}
          {graph.circular_dependencies
            .map((c) => `${c.from} ➔ ${c.to}`)
            .join(', ')}
        </div>
      ) : null}
      <div className="graph">
        <ReactFlow
          nodes={nodes}
          edges={model.edges}
          onNodesChange={onNodesChange}
          nodesConnectable={editing}
          edgesReconnectable={false}
          onConnect={onConnect}
          onEdgeClick={(_, edge) => editing && setSelected({ source: edge.source, target: edge.target })}
          fitView
        >
          <Background color="#d4ccbd" />
          <MiniMap />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      {graph && (
        <div className="bottlenecks">
          {graph.bottlenecks.map((b) => (
            <span key={b.entity_id}>
              {b.entity_id}: blocks {b.blocks_count}{' '}
              {b.blocks_count === 1 ? 'task' : 'tasks'}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
