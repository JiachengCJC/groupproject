import { useEffect, useState } from 'react';
import { api, errorMessage } from '../api';
import { useWorkspace } from '../context';
import type { Run } from '../types';
import { date } from '../utils';
import { TaskHistory } from '../components/Tasks';
import { Empty, Icon, Loading, Notice, PageHeading, Status } from '../components/ui';
export default function History() {
  const { navigate } = useWorkspace();
  const [items, setItems] = useState<Run[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  async function load() {
    setBusy(true);
    try {
      setItems(await api<Run[]>('/analyses'));
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  const filtered = items.filter(
    (r) =>
      (filter === 'all' || r.status === filter) &&
      (r.question || r.request?.question || '').toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="A TRACEABLE DECISION RECORD"
        title="A record of every decision."
        description="Track analyses, verify evidence, and review your team’s decisions and revisions."
        action={
          <button className="button secondary" onClick={() => void load()} disabled={busy}>
            <Icon name="refresh" size={16} />
            Refresh history
          </button>
        }
      />
      <div className="history-toolbar">
        <label className="search-field">
          <Icon name="search" size={18} />
          <input
            aria-label="Search analysis questions"
            placeholder="Search business questions…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <label className="sr-only" htmlFor="history-status">
          Filter by status
        </label>
        <select id="history-status" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All statuses</option>
          <option value="pending_review">Pending review</option>
          <option value="approved">Approved</option>
          <option value="rejected">Revision requested</option>
          <option value="running">Running</option>
          <option value="queued">Queued</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      {error && <Notice error>{error}</Notice>}
      <div className="panel history-panel">
        {busy ? (
          <Loading />
        ) : filtered.length ? (
          <>
            <div className="history-header">
              <span>Business question</span>
              <span>Created</span>
              <span>Status</span>
            </div>
            {filtered.map((run) => (
              <button
                className="history-row"
                key={run.id}
                onClick={() => navigate('analysis', run.id)}
              >
                <span>
                  <b>{run.question || run.request?.question}</b>
                  <small>{run.id}</small>
                </span>
                <time>{date(run.created_at)}</time>
                <span>
                  <Status status={run.status} />
                  <Icon name="arrow" size={16} />
                </span>
              </button>
            ))}
          </>
        ) : (
          <Empty
            title={
              items.length ? 'No records match these filters' : 'Start with your first analysis'
            }
            icon="clock"
          >
            {items.length
              ? 'Adjust your search or status filter and try again.'
              : 'After you create an analysis, its status, evidence, and review results will appear here.'}
          </Empty>
        )}
      </div>
      <TaskHistory />
    </>
  );
}
