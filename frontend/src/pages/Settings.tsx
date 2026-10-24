import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from '../api';
import { usePermissions, useWorkspace } from '../context';
import type { AgentMode, LocalProfile, Schedule, User } from '../types';
import {
  date,
  localProfileIssue,
  localProfileLabels,
  retrievalModelLabel,
  roleLabels,
} from '../utils';
import LocalProfileSettings from '../components/LocalProfileSettings';
import { Empty, Icon, Modal, Notice, PageHeading } from '../components/ui';
export default function Settings() {
  const { auth, config, models, refreshModels } = useWorkspace();
  const { isAdmin } = usePermissions();
  return (
    <>
      <PageHeading
        eyebrow="YOUR TEAM, YOUR WORKSPACE"
        title="Ready for your team."
        description="Check model and data services, manage members, and schedule analyses."
      />
      <div className="settings-grid">
        <section className="panel settings-panel">
          <div className="section-heading">
            <h2>Models and services</h2>
            <Icon name="settings" />
          </div>
          <div className="capability">
            <span className="source-icon">
              <Icon name="laptop" />
            </span>
            <div>
              <b>Ollama local model</b>
              <p>
                {models?.available
                  ? `Connected · ${models.models.length} model${models.models.length === 1 ? '' : 's'}`
                  : models?.error || 'Checking connection…'}
              </p>
            </div>
            <button className="button text small" onClick={() => void refreshModels()}>
              Refresh
            </button>
          </div>
          {models?.available && (
            <div className="model-chips">
              {models.models.map((model) => (
                <span key={model}>{model}</span>
              ))}
            </div>
          )}
          <div className="capability">
            <span className="source-icon">
              <Icon name="globe" />
            </span>
            <div>
              <b>Cloud analysis model</b>
              <p>
                {config.local_only
                  ? 'Cloud calls are disabled by the local-only policy'
                  : config.live_agents_available
                    ? config.openai_model || 'Configured'
                    : 'Not configured: ask the deployment administrator to set the API key and model'}
              </p>
            </div>
            <span
              className={
                'badge ' + (config.live_agents_available && !config.local_only ? 'success' : '')
              }
            >
              {config.live_agents_available && !config.local_only ? 'Available' : 'Disabled'}
            </span>
          </div>
          <div className="capability">
            <span className="source-icon">
              <Icon name="chart" />
            </span>
            <div>
              <b>OpenAI Decisions</b>
              <p>
                {config.local_only
                  ? 'OpenAI Decisions is disabled by the local-only policy'
                  : config.openai_decisions_available
                    ? config.openai_decision_model || 'Configured'
                    : 'Not configured: set the existing OPENAI_API_KEY and OPENAI_DECISION_MODEL'}
              </p>
            </div>
            <span
              className={
                'badge ' +
                (config.openai_decisions_available && !config.local_only ? 'success' : '')
              }
            >
              {config.openai_decisions_available && !config.local_only ? 'Available' : 'Disabled'}
            </span>
          </div>
          <dl className="settings-details">
            <dt>Data policy</dt>
            <dd>
              {config.local_only ? 'Local-only inference' : 'Cloud calls allowed when selected'}
            </dd>
            <dt>Database</dt>
            <dd>{config.database || 'Configured by the server'}</dd>
            <dt>Retrieval method</dt>
            <dd>{config.retrieval_mode || config.retrieval || 'Configured by the server'}</dd>
            <dt>Image extraction model</dt>
            <dd>{config.ollama_vision_model || 'Not configured'}</dd>
            <dt>Embedding model</dt>
            <dd>{retrievalModelLabel(config)}</dd>
            <dt>Background tasks</dt>
            <dd>{config.worker_mode || 'Configured by the server'}</dd>
          </dl>
          <Notice>
            {config.local_only
              ? 'External inference is restricted by the local-only policy. Online source sync follows a separate network policy.'
              : 'Local deployment does not guarantee that all data stays on the device. Selecting a cloud model or OpenAI Decisions sends relevant evidence to OpenAI or the configured cloud service.'}
          </Notice>
        </section>
        <section className="panel settings-panel">
          <p className="eyebrow">WORKSPACE ACCESS</p>
          <h2>Your access</h2>
          <div className="profile-card">
            <span className="user-avatar">{(auth.user?.name || 'Local').slice(0, 1)}</span>
            <div>
              <h3>{auth.user?.name || 'Local development workspace'}</h3>
              <p>{auth.user?.email || 'Authentication is disabled'}</p>
              <span className="badge">{roleLabels[auth.user?.role || 'admin']}</span>
            </div>
          </div>
          <div className="permission-guide">
            <div>
              <Icon name="users" size={17} />
              <p>
                <b>Analyst</b> Prepare sources and create analyses.
              </p>
            </div>
            <div>
              <Icon name="shield" size={17} />
              <p>
                <b>Reviewer</b> Verify evidence and approve reports.
              </p>
            </div>
            <div>
              <Icon name="book" size={17} />
              <p>
                <b>Viewer</b> View permitted sources and analyses.
              </p>
            </div>
            <div>
              <Icon name="settings" size={17} />
              <p>
                <b>Administrator</b> Manage members and the workspace.
              </p>
            </div>
          </div>
          {!auth.enabled && (
            <Notice>
              This is development mode without authentication. Enable account sign-in for team
              deployments.
            </Notice>
          )}
        </section>
      </div>
      {isAdmin && auth.enabled && <Users />}
      <Schedules />
    </>
  );
}
function Users() {
  const { notify, auth } = useWorkspace();
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resetUser, setResetUser] = useState<User | null>(null);
  async function load() {
    try {
      setUsers(await api<User[]>('/auth/users'));
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const element = e.currentTarget;
    const form = new FormData(element);
    setBusy(true);
    try {
      await api('/auth/users', {
        name: String(form.get('name')),
        email: String(form.get('email')),
        password: String(form.get('password')),
        role: String(form.get('role')),
      });
      element.reset();
      await load();
      notify('Member added. Share their login details through a secure channel.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  }
  async function update(user: User, changes: Record<string, unknown>) {
    setBusy(true);
    try {
      await api('/auth/users/' + encodeURIComponent(user.id), changes, { method: 'PATCH' });
      await load();
      setResetUser(null);
      notify(
        changes.password
          ? 'Password reset. Existing sessions have been invalidated.'
          : 'Member permissions updated. Existing sessions have been invalidated.',
      );
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="team-settings">
      <div className="section-heading">
        <h2>Team members</h2>
        <span className="badge">
          {users.length} {users.length === 1 ? 'member' : 'members'}
        </span>
      </div>
      {error && <Notice error>{error}</Notice>}
      <div className="settings-grid">
        <div className="panel table-scroll">
          <table>
            <thead>
              <tr>
                <th>Member</th>
                <th>Role</th>
                <th>Manage</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>
                    <b>{user.name}</b>
                    <div className="help">{user.email}</div>
                    {user.disabled && <span className="badge danger">Disabled</span>}
                  </td>
                  <td>
                    <select
                      aria-label={user.name + ' role'}
                      value={user.role}
                      disabled={busy || user.id === auth.user?.id}
                      onChange={(event) => void update(user, { role: event.target.value })}
                    >
                      {Object.entries(roleLabels).map(([role, label]) => (
                        <option key={role} value={role}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <div className="member-actions">
                      <button
                        className="button text small"
                        disabled={busy}
                        onClick={() => setResetUser(user)}
                      >
                        Reset password
                      </button>
                      {user.id !== auth.user?.id && (
                        <button
                          className="button text small"
                          disabled={busy}
                          onClick={() => void update(user, { disabled: !user.disabled })}
                        >
                          {user.disabled ? 'Enable account' : 'Disable account'}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form className="panel" onSubmit={create}>
          <h3>Add member</h3>
          <div className="form-grid">
            <label>
              Name
              <input name="name" required minLength={2} maxLength={100} />
            </label>
            <label>
              Role
              <select name="role" defaultValue="analyst">
                <option value="analyst">Analyst</option>
                <option value="reviewer">Reviewer</option>
                <option value="viewer">Viewer</option>
                <option value="admin">Administrator</option>
              </select>
            </label>
          </div>
          <label>
            Email
            <input name="email" type="email" required autoComplete="off" />
          </label>
          <label>
            Initial password
            <input
              name="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
            />
            <small>
              Use at least 12 characters and share it with this member through a secure channel.
            </small>
          </label>
          <button className="button secondary" disabled={busy}>
            <Icon name="plus" size={16} />
            Add member
          </button>
        </form>
      </div>
      {resetUser && (
        <Modal
          title={'Reset password for ' + resetUser.name + ''}
          onClose={() => setResetUser(null)}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void update(resetUser, { password: String(form.get('password')) });
            }}
          >
            <p className="help">
              Saving will invalidate all existing sessions for this member. Share the new password
              through a secure channel.
            </p>
            <label>
              New password
              <input
                name="password"
                type="password"
                autoComplete="new-password"
                minLength={12}
                maxLength={128}
                required
              />
            </label>
            <button className="button primary" disabled={busy}>
              Save new password
            </button>
          </form>
        </Modal>
      )}
    </section>
  );
}
function Schedules() {
  const { notify, sources, config, models } = useWorkspace();
  const { canWrite } = usePermissions();
  const [items, setItems] = useState<Schedule[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<AgentMode>('local');
  const [localProfile, setLocalProfile] = useState<LocalProfile>('custom');
  const namedProfile = mode === 'ollama' && localProfile !== 'custom';
  const profileIssue = mode === 'ollama' ? localProfileIssue(localProfile, config, models) : null;
  async function load() {
    try {
      setItems(await api<Schedule[]>('/schedules'));
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const element = e.currentTarget;
    const form = new FormData(element);
    if (profileIssue) {
      notify(profileIssue, true);
      return;
    }
    setBusy(true);
    try {
      await api('/schedules', {
        name: String(form.get('name')),
        interval_minutes: Number(form.get('interval')),
        enabled: true,
        request: {
          question: String(form.get('question')),
          dataset_id: String(form.get('dataset')) || null,
          document_ids: form.getAll('document').map(String),
          agent_mode: mode,
          decision_mode: mode === 'local' ? 'baseline' : 'llm',
          routing_mode: 'auto',
          ...(mode === 'ollama'
            ? namedProfile
              ? { local_profile: localProfile }
              : { model: String(form.get('model')), local_profile: 'custom' }
            : {}),
        },
      });
      element.reset();
      await load();
      notify('Scheduled analysis saved. Generated reports still require human review.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  }
  async function toggle(item: Schedule) {
    setBusy(true);
    try {
      await api(
        '/schedules/' + encodeURIComponent(item.id),
        { enabled: !item.enabled },
        { method: 'PATCH' },
      );
      await load();
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="schedules-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">KEEP YOUR ANALYSIS CURRENT</p>
          <h2>Scheduled analyses</h2>
        </div>
        <button className="button secondary small" onClick={() => void load()} disabled={busy}>
          <Icon name="refresh" size={15} />
          Refresh
        </button>
      </div>
      {error && <Notice error>{error}</Notice>}
      <div className="settings-grid">
        <div className="panel">
          {items.length ? (
            items.map((item) => (
              <article className="schedule-row" key={item.id}>
                <div>
                  <b>{item.name}</b>
                  <small>
                    Every {item.interval_minutes} minutes · {item.enabled ? 'Running' : 'Paused'}
                  </small>
                  <p>{item.request.question}</p>
                  {item.request.agent_mode === 'ollama' && (
                    <small>{localProfileLabels[item.request.local_profile || 'custom']}</small>
                  )}
                  <small>
                    Next run {date(item.next_run_at)} · Last run {date(item.last_run_at)}
                  </small>
                  {item.last_error && <p className="error-text">{item.last_error}</p>}
                </div>
                {canWrite && (
                  <button
                    className="button secondary small"
                    disabled={busy}
                    onClick={() => void toggle(item)}
                  >
                    {item.enabled ? 'Pause' : 'Enable'}
                  </button>
                )}
              </article>
            ))
          ) : (
            <Empty title="Track changes on your schedule" icon="clock">
              Schedule analyses to generate reports for review at regular intervals.
            </Empty>
          )}
        </div>
        {canWrite && (
          <form className="panel" onSubmit={create}>
            <h3>Create scheduled analysis</h3>
            <div className="form-grid">
              <label>
                Task name
                <input name="name" required maxLength={120} />
              </label>
              <label>
                Interval (minutes)
                <input
                  name="interval"
                  type="number"
                  required
                  min={15}
                  max={525600}
                  defaultValue={1440}
                />
              </label>
            </div>
            <label>
              Business question
              <textarea name="question" required minLength={8} maxLength={2000} rows={2} />
            </label>
            <label>
              Dataset
              <select name="dataset">
                <option value="">Documents only</option>
                {sources.datasets.map((d) => (
                  <option value={d.id} key={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
            <details>
              <summary>Select documents</summary>
              <div className="document-choices">
                {sources.documents.map((d) => (
                  <label className="check-row" key={d.id}>
                    <input name="document" type="checkbox" value={d.id} />
                    <span>{d.title}</span>
                  </label>
                ))}
              </div>
            </details>
            <label>
              Analysis model
              <select
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as AgentMode);
                  if (e.target.value !== 'ollama') setLocalProfile('custom');
                }}
              >
                <option value="local">Rule baseline</option>
                <option value="ollama">Ollama local model</option>
                <option value="live" disabled={config.local_only || !config.live_agents_available}>
                  Cloud model
                </option>
              </select>
            </label>
            {mode === 'ollama' && (
              <LocalProfileSettings
                config={config}
                models={models}
                value={localProfile}
                onChange={setLocalProfile}
                disabled={busy}
              />
            )}
            {mode === 'ollama' && !namedProfile && (
              <label>
                Local model
                <select
                  name="model"
                  required
                  defaultValue={models?.default_model || models?.models[0] || ''}
                >
                  <option value="">Select a model</option>
                  {models?.models.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              </label>
            )}
            <p className="help">
              Each run is recorded and awaits human review. Cloud tasks may incur recurring API
              charges.
            </p>
            <button className="button secondary" disabled={busy || !!profileIssue}>
              <Icon name="plus" size={16} />
              Create scheduled task
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
