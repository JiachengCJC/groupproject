import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../api';
import { usePermissions, useWorkspace } from '../context';
import { date, isRunning, pretty } from '../utils';
import { Empty, Icon, Notice, Status } from './ui';
export interface Task {
  id: string;
  kind: string;
  status: string;
  created_at: string;
  error?: string;
  attempts?: number;
  result?: unknown;
  payload?: Record<string, unknown>;
}
const labels: Record<string, string> = {
  analysis: 'Business analysis',
  upload: 'Source processing',
  connector: 'Source sync',
  sync: 'Source sync',
  evaluation: 'Quality evaluation',
  index: 'Semantic index',
  schedule: 'Scheduled analysis',
};
export function TaskProgress({
  taskId,
  onComplete,
}: {
  taskId: string;
  onComplete?: (task: Task) => void;
}) {
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState('');
  const callback = useRef(onComplete);
  callback.current = onComplete;
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const result = await api<Task>('/tasks/' + encodeURIComponent(taskId), undefined, {
          signal: controller.signal,
        });
        if (disposed) return;
        setTask(result);
        setError('');
        if (isRunning(result.status)) timer = setTimeout(poll, 1600);
        else callback.current?.(result);
      } catch (err) {
        if (!disposed) {
          setError(errorMessage(err));
          timer = setTimeout(poll, 5000);
        }
      }
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [taskId]);
  return (
    <div className="task-progress" aria-live="polite">
      {task ? (
        <>
          <Status status={task.status} />
          <span>
            {task.status === 'completed'
              ? 'Background processing completed'
              : task.status === 'failed'
                ? task.error || 'Processing failed. Retry from task history.'
                : task.status === 'cancelled'
                  ? 'Task cancelled'
                  : 'Task saved. You can view it later in Analysis and review.'}
          </span>
        </>
      ) : (
        <span className="help">Connecting to background task…</span>
      )}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
export function TaskHistory() {
  const { notify, navigate, refreshSources } = useWorkspace();
  const { canWrite } = usePermissions();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  async function load() {
    try {
      setTasks(await api<Task[]>('/tasks'));
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<Task[]>('/tasks', undefined, { signal: controller.signal });
        if (disposed) return;
        setTasks(result);
        setError('');
        timer = setTimeout(poll, result.some((t) => isRunning(t.status)) ? 2000 : 10000);
      } catch (err) {
        if (!disposed) {
          setError(errorMessage(err));
          timer = setTimeout(poll, 5000);
        }
      }
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, []);
  async function act(id: string, action: string) {
    setBusy(id);
    try {
      await api('/tasks/' + encodeURIComponent(id) + '/' + action, {});
      await load();
      await refreshSources();
      notify(
        action === 'retry' ? 'Task added back to the queue.' : 'Cancellation request recorded.',
      );
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy('');
    }
  }
  return (
    <section className="task-history">
      <div className="section-heading">
        <div>
          <p className="eyebrow">BACKGROUND TASKS</p>
          <h2>Background tasks</h2>
        </div>
        <button className="button secondary small" onClick={() => void load()}>
          <Icon name="refresh" size={14} />
          Refresh
        </button>
      </div>
      {error && <Notice error>{error}</Notice>}
      <div className="panel">
        {tasks.length ? (
          tasks.map((task) => (
            <details key={task.id} className="task-row">
              <summary>
                <div>
                  <b>{labels[task.kind] || task.kind}</b>
                  <small>
                    {date(task.created_at)} · {task.id}
                  </small>
                </div>
                <Status status={task.status} />
              </summary>
              {task.error && <Notice error>{task.error}</Notice>}
              {task.result != null && <pre>{pretty(task.result)}</pre>}
              <div className="inline-actions">
                {task.kind === 'analysis' && typeof task.payload?.run_id === 'string' && (
                  <button
                    className="button secondary small"
                    onClick={() => navigate('analysis', String(task.payload?.run_id))}
                  >
                    View analysis
                  </button>
                )}
                {canWrite && isRunning(task.status) && (
                  <button
                    className="button secondary small"
                    disabled={!!busy}
                    onClick={() => void act(task.id, 'cancel')}
                  >
                    Cancel task
                  </button>
                )}
                {canWrite && ['failed', 'cancelled'].includes(task.status) && (
                  <button
                    className="button secondary small"
                    disabled={!!busy}
                    onClick={() => void act(task.id, 'retry')}
                  >
                    Retry task
                  </button>
                )}
              </div>
            </details>
          ))
        ) : (
          <Empty title="No background tasks yet" icon="clock">
            Source processing, sync, indexing, and evaluation history will appear here.
          </Empty>
        )}
      </div>
    </section>
  );
}
