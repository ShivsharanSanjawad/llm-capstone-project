import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import DependencyGraph from './DependencyGraph';
import { api } from '../services/api';
import type { Graph, Task } from '../types';

// The canvas needs real layout; the editing controls are what is under test.
vi.mock('@xyflow/react', () => ({
  ReactFlow: () => <div data-testid="flow" />,
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  MarkerType: { ArrowClosed: 'arrowclosed' },
  useNodesState: (initial: unknown[]) => [initial, vi.fn(), vi.fn()],
}));
vi.mock('@xyflow/react/dist/style.css', () => ({}));

const tasks: Task[] = [
  { task_id: 'T1', description: 'Design the API', explicit_prerequisites: [], inferred_prerequisites: [] },
  { task_id: 'T2', description: 'Build the UI', explicit_prerequisites: ['T1'], inferred_prerequisites: [], manual_prerequisites: ['T1'] },
  { task_id: 'T3', description: 'Write the docs', explicit_prerequisites: [], inferred_prerequisites: [] },
];
const graph = {
  is_feasible: true,
  circular_dependencies: [],
  bottlenecks: [],
  visualization_graph: {
    nodes: tasks.map((t) => ({ id: t.task_id, data: { description: t.description, status: 'pending' } })),
    edges: [{ source: 'T1', target: 'T2' }],
  },
} as unknown as Graph;

describe('dependency editing', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(cleanup);

  it('is read-only without a meeting id', () => {
    render(<DependencyGraph graph={graph} tasks={tasks} />);
    expect(screen.queryByRole('button', { name: /edit dependencies/i })).toBeNull();
  });

  it('adds a dependency from the form and reports the change', async () => {
    const save = vi.spyOn(api, 'editDependencies').mockResolvedValue({ status: 'success', meeting_id: 'm1' });
    const changed = vi.fn();
    render(<DependencyGraph graph={graph} tasks={tasks} meetingId="m1" onChanged={changed} />);
    fireEvent.click(screen.getByRole('button', { name: /edit dependencies/i }));
    const [pre, dep] = screen.getAllByRole('combobox');
    fireEvent.change(pre, { target: { value: 'T2' } });
    fireEvent.change(dep, { target: { value: 'T3' } });
    fireEvent.click(screen.getByRole('button', { name: /add dependency/i }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('m1', { add: [{ prerequisite: 'T2', task_id: 'T3' }] }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
  });

  it('accepts an outside blocker typed as text', async () => {
    const save = vi.spyOn(api, 'editDependencies').mockResolvedValue({ status: 'success', meeting_id: 'm1' });
    render(<DependencyGraph graph={graph} tasks={tasks} meetingId="m1" />);
    fireEvent.click(screen.getByRole('button', { name: /edit dependencies/i }));
    fireEvent.change(screen.getByPlaceholderText(/client approval/i), { target: { value: 'Client approval' } });
    fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'T3' } });
    fireEvent.click(screen.getByRole('button', { name: /add dependency/i }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('m1', { add: [{ prerequisite: 'Client approval', task_id: 'T3' }] }));
  });

  it('removes a dependency from the list', async () => {
    const save = vi.spyOn(api, 'editDependencies').mockResolvedValue({ status: 'success', meeting_id: 'm1' });
    render(<DependencyGraph graph={graph} tasks={tasks} meetingId="m1" />);
    fireEvent.click(screen.getByRole('button', { name: /edit dependencies/i }));
    fireEvent.click(screen.getByRole('button', { name: /remove dependency T1 to T2/i }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('m1', { remove: [{ prerequisite: 'T1', task_id: 'T2' }] }));
  });

  it('refuses a self-dependency and shows server errors', async () => {
    const save = vi.spyOn(api, 'editDependencies');
    render(<DependencyGraph graph={graph} tasks={tasks} meetingId="m1" />);
    fireEvent.click(screen.getByRole('button', { name: /edit dependencies/i }));
    const [pre, dep] = screen.getAllByRole('combobox');
    fireEvent.change(pre, { target: { value: 'T1' } });
    fireEvent.change(dep, { target: { value: 'T1' } });
    fireEvent.click(screen.getByRole('button', { name: /add dependency/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/cannot depend on itself/i);
    expect(save).not.toHaveBeenCalled();
  });
});

describe('countCycles', () => {
  it('counts cycles, not links', async () => {
    const { countCycles } = await import('./DependencyGraph');
    const pair = (a: string, b: string) => [{ from_task_id: a, to_task_id: b }, { from_task_id: b, to_task_id: a }];
    // the real case: six two-task cycles reported as twelve links
    const edges = ['1', '6', '17', '19', '22', '26'].flatMap((n) => pair('T' + n, 'T' + (Number(n) + 1)));
    expect(edges).toHaveLength(12);
    expect(countCycles(edges)).toBe(6);
    expect(countCycles([])).toBe(0);
  });
});
