import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from '../api';
import { usePermissions, useWorkspace } from '../context';
import type {
  AgentMode,
  AnalysisRequest,
  DecisionMode,
  Evidence,
  LocalProfile,
  Run,
} from '../types';
import {
  agentLabels,
  compatibleDecision,
  date,
  decisionProviderLabel,
  eligibleDocuments,
  fmt,
  isRunning,
  isProbabilisticDecision,
  localProfileIssue,
  localProfileLabels,
  parseCandidates,
  percent,
  pretty,
} from '../utils';
import QueryChart from '../components/QueryChart';
import LocalProfileSettings, { LocalRoutingSummary } from '../components/LocalProfileSettings';
import {
  DataTable,
  Empty,
  Icon,
  Loading,
  Metric,
  Modal,
  Notice,
  PageHeading,
  Status,
} from '../components/ui';

const team = [
  ['competitive', 'Pricing · Margin · Competition'],
  ['customer', 'Retention · Behavior · Feedback'],
  ['product', 'Quality · Inventory · Supply'],
  ['market', 'Demand · Growth · Opportunity'],
];
export default function Analysis({ runId }: { runId?: string }) {
  const { config, sources, models, refreshModels, auth, notify, navigate } = useWorkspace();
  const { canWrite, canReview } = usePermissions();
  const [question, setQuestion] = useState('');
  const [dataset, setDataset] = useState(sources.datasets[0]?.id || '');
  const [documents, setDocuments] = useState<string[]>([]);
  const [mode, setMode] = useState<AgentMode>('local');
  const [decisionMode, setDecisionMode] = useState<DecisionMode>('baseline');
  const [model, setModel] = useState(config.ollama_model || '');
  const [localProfile, setLocalProfile] = useState<LocalProfile>('custom');
  const [candidates, setCandidates] = useState('');
  const [parent, setParent] = useState<string | undefined>();
  const [run, setRun] = useState<Run | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pollError, setPollError] = useState('');
  const [pollVersion, setPollVersion] = useState(0);
  const [tab, setTab] = useState('overview');
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [notes, setNotes] = useState('');
  const [reviewer, setReviewer] = useState(auth.user?.name || '');
  const [reviewBusy, setReviewBusy] = useState(false);
  const availableDocuments = eligibleDocuments(sources.documents, dataset);
  const busy = submitting || isRunning(run?.status);
  const namedProfile = mode === 'ollama' && localProfile !== 'custom';
  const profileIssue = mode === 'ollama' ? localProfileIssue(localProfile, config, models) : null;
  useEffect(() => {
    setDocuments((prev) => prev.filter((id) => availableDocuments.some((d) => d.id === id)));
  }, [dataset, sources.documents]); // Scope changes cannot retain out-of-scope documents.
  useEffect(() => {
    if (models?.available && !models.models.includes(model))
      setModel(
        models.models.includes(models.default_model || '')
          ? models.default_model!
          : models.models[0] || '',
      );
  }, [models, model]);
  useEffect(() => {
    if (!runId) {
      setRun(null);
      setPollError('');
      return;
    }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    let first = true;
    async function poll() {
      try {
        const next = await api<Run>('/analyses/' + encodeURIComponent(runId!), undefined, {
          signal: controller.signal,
        });
        if (disposed) return;
        setRun(next);
        setPollError('');
        if (first && next.request) {
          setQuestion(next.request.question);
          setDataset(next.request.dataset_id || '');
          setDocuments(next.request.document_ids || []);
          setMode(next.request.agent_mode);
          setLocalProfile(next.request.local_profile || 'custom');
          setDecisionMode(
            compatibleDecision(
              next.request.agent_mode,
              next.request.decision_mode,
              !!config.local_only,
              config.openai_decisions_available,
            ),
          );
          setCandidates(
            (next.request.candidate_actions || [])
              .map((action) => `${action.title} | ${action.description}`)
              .join('\n'),
          );
          if (next.request.model) setModel(next.request.model);
          setParent(undefined);
          first = false;
        }
        if (isRunning(next.status)) timer = setTimeout(poll, 1200);
      } catch (err) {
        if (!disposed) setPollError(errorMessage(err));
      }
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [runId, pollVersion]);
  function updateMode(value: AgentMode) {
    setMode(value);
    if (value !== 'ollama') setLocalProfile('custom');
    setDecisionMode(
      compatibleDecision(
        value,
        decisionMode === 'openai_decisions' || value === 'local' ? decisionMode : 'llm',
        !!config.local_only,
        config.openai_decisions_available,
      ),
    );
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canWrite || busy) return;
    if (!dataset && !documents.length) {
      notify('Select a dataset or at least one document.', true);
      return;
    }
    if (profileIssue) {
      notify(profileIssue, true);
      return;
    }
    if (mode === 'ollama' && !namedProfile && !model) {
      notify('Select an installed local model first.', true);
      return;
    }
    setSubmitting(true);
    setPollError('');
    try {
      const request: AnalysisRequest = {
        question,
        dataset_id: dataset || null,
        document_ids: documents,
        agent_mode: mode,
        decision_mode: decisionMode,
        routing_mode: 'auto',
        ...(mode === 'ollama'
          ? namedProfile
            ? { local_profile: localProfile }
            : { model, local_profile: 'custom' as const }
          : {}),
        ...(parent ? { parent_run_id: parent } : {}),
        ...(decisionMode === 'openai_decisions'
          ? { candidate_actions: parseCandidates(candidates) }
          : {}),
      };
      const created = await api<{ id: string }>('/analyses', request);
      setTab('overview');
      setNotes('');
      setParent(undefined);
      navigate('analysis', created.id);
      notify('Analysis submitted. You can track it in the review history.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setSubmitting(false);
    }
  }
  async function review(decision: string) {
    if (!run || !canReview) return;
    if (decision === 'rejected' && !notes.trim()) {
      notify('Please describe the changes needed.', true);
      return;
    }
    if (!auth.enabled && reviewer.trim().length < 2) {
      notify('Enter a reviewer name with at least 2 characters.', true);
      return;
    }
    setReviewBusy(true);
    try {
      await api('/analyses/' + encodeURIComponent(run.id) + '/review', {
        decision,
        notes,
        reviewer: auth.user?.name || reviewer,
      });
      setRun(await api<Run>('/analyses/' + encodeURIComponent(run.id)));
      notify(
        decision === 'approved'
          ? 'This version is approved. Reports are ready to export.'
          : 'Revision notes recorded.',
      );
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setReviewBusy(false);
    }
  }
  async function changeRun(action: 'cancel' | 'retry') {
    if (!run) return;
    try {
      const result = await api<{ id?: string }>(
        '/analyses/' + encodeURIComponent(run.id) + '/' + action,
        {},
      );
      if (result?.id && result.id !== run.id) navigate('analysis', result.id);
      else setPollVersion((v) => v + 1);
    } catch (err) {
      notify(errorMessage(err), true);
    }
  }
  function revise() {
    if (!run) return;
    setParent(run.id);
    setQuestion(run.request.question);
    navigate('analysis');
    setNotes('');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    notify('Original analysis linked. Review notes will be included in the new version.');
  }
  function citationButtons(ids?: string[]) {
    return (
      <div className="citations">
        {(ids || []).map((id) => {
          const evidence = run?.result?.evidence.find((e) => e.id === id);
          return evidence ? (
            <button key={id} className="citation" onClick={() => setSelectedEvidence(evidence)}>
              <Icon name={evidence.kind === 'sql' ? 'database' : 'file'} size={12} />
              {evidence.kind === 'sql' ? 'Data source' : evidence.title.split(' · ')[0]}
            </button>
          ) : (
            <span className="citation missing" key={id}>
              Citation unavailable
            </span>
          );
        })}
      </div>
    );
  }
  const result = run?.result;
  const d = result?.decision;
  const probabilistic = isProbabilisticDecision(d?.provider);
  const ranking = Object.entries(probabilistic ? d?.probabilities || {} : d?.scores || {}).sort(
    (a, b) => b[1] - a[1],
  );
  return (
    <>
      <PageHeading
        eyebrow="FROM EVIDENCE TO ACTION"
        title="Make your next move clearer."
        description="Ask a business question, connect your sources, and get an analysis your team can review."
        action={
          <span className="page-marker">
            01 <i>/ ANALYZE</i>
          </span>
        }
      />
      <div className="studio-grid">
        <form className="panel question-panel" onSubmit={submit}>
          <div className="panel-kicker">
            <span>
              <Icon name="spark" size={16} />
              Your business question
            </span>
            <span className="badge">
              {sources.datasets.find((s) => s.id === dataset)?.synthetic
                ? 'Synthetic demo data'
                : dataset
                  ? 'Business data'
                  : 'Document analysis'}
            </span>
          </div>
          {parent && (
            <Notice>
              Creating a revised version. The original analysis and review notes will be preserved.
            </Notice>
          )}
          <label className="sr-only" htmlFor="question">
            Business question
          </label>
          <textarea
            id="question"
            className="question-input"
            placeholder="For example: Sales fell this quarter. Should we adjust prices or focus on customer retention?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            required
            minLength={8}
            maxLength={2000}
            disabled={busy || !canWrite}
          />
          <div className="query-toolbar">
            <label>
              Analysis scope
              <select
                aria-label="Analysis scope"
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
                disabled={busy || !canWrite}
              >
                <option value="">Selected documents only</option>
                {sources.datasets.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.synthetic ? ' · Demo' : ''}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="button primary"
              disabled={busy || !canWrite || !!profileIssue}
            >
              {busy ? <span className="spinner" /> : <Icon name="arrow" size={18} />}{' '}
              {busy ? 'Analysis in progress' : 'Start analysis'}
            </button>
          </div>
          <details className="form-details">
            <summary>
              Select reference documents{' '}
              <span>
                {documents.length
                  ? `${documents.length} selected`
                  : dataset
                    ? 'Automatically retrieve relevant and shared sources'
                    : 'Select at least one'}
              </span>
            </summary>
            <div className="document-choices">
              {availableDocuments.length ? (
                availableDocuments.map((doc) => (
                  <label key={doc.id} className="check-row">
                    <input
                      type="checkbox"
                      checked={documents.includes(doc.id)}
                      disabled={busy || !canWrite}
                      onChange={(e) =>
                        setDocuments((prev) =>
                          e.target.checked ? [...prev, doc.id] : prev.filter((id) => id !== doc.id),
                        )
                      }
                    />
                    <span>
                      {doc.title}
                      <small>{doc.category || 'General sources'}</small>
                    </span>
                  </label>
                ))
              ) : (
                <p className="help">
                  No documents are available yet. Upload sources to select them here.
                </p>
              )}
            </div>
          </details>
          <details className="form-details">
            <summary>
              Model and analysis settings{' '}
              <span>
                {mode === 'local'
                  ? 'Rule baseline'
                  : mode === 'ollama'
                    ? localProfileLabels[localProfile]
                    : 'Cloud model'}
              </span>
            </summary>
            <div className="form-grid">
              <label>
                Analysis model
                <select
                  value={mode}
                  onChange={(e) => updateMode(e.target.value as AgentMode)}
                  disabled={busy || !canWrite}
                >
                  <option value="local">Rule baseline · No model</option>
                  <option value="ollama">Ollama · Local model</option>
                  <option
                    value="live"
                    disabled={config.local_only || !config.live_agents_available}
                  >
                    Cloud model
                    {config.local_only
                      ? ' · Disabled by local-only policy'
                      : !config.live_agents_available
                        ? ' · Not configured'
                        : ''}
                  </option>
                </select>
              </label>
              <label>
                Decision method
                <select
                  value={decisionMode}
                  onChange={(e) => setDecisionMode(e.target.value as DecisionMode)}
                  disabled={busy || !canWrite || namedProfile}
                >
                  <option value="baseline">Transparent rule scoring</option>
                  <option value="llm" disabled={mode === 'local'}>
                    Model recommendation
                  </option>
                  <option
                    value="openai_decisions"
                    disabled={config.local_only || !config.openai_decisions_available}
                  >
                    OpenAI Decisions{!config.openai_decisions_available ? ' · Not configured' : ''}
                  </option>
                </select>
              </label>
              {mode === 'ollama' && !namedProfile && (
                <label>
                  Local model
                  <select value={model} onChange={(e) => setModel(e.target.value)} disabled={busy}>
                    <option value="">Select an installed model</option>
                    {models?.models.map((m) => (
                      <option key={m}>{m}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            {mode === 'ollama' && (
              <LocalProfileSettings
                config={config}
                models={models}
                value={localProfile}
                onChange={(value) => {
                  setLocalProfile(value);
                  if (value !== 'custom') setDecisionMode('llm');
                }}
                disabled={busy || !canWrite}
              />
            )}
            <div className="inline-actions">
              <button
                type="button"
                className="button text small"
                onClick={() => {
                  void refreshModels();
                }}
                disabled={busy}
              >
                <Icon name="refresh" size={14} />
                Refresh local models
              </button>
              <span className="help">
                {models?.available
                  ? `Connected · ${models.models.length} model${models.models.length === 1 ? '' : 's'}`
                  : models?.error || 'Checking connection'}
              </span>
            </div>
            <p className="help">
              {mode === 'local'
                ? 'Fixed queries and scoring rules cover the demo metrics. Local or cloud models can generate queries and combine evidence from your sources.'
                : mode === 'ollama'
                  ? 'Analysis evidence is sent to the configured Ollama service. Complex tasks may take a few minutes.'
                  : 'Selected schemas, query results, and relevant document excerpts are sent to the configured cloud model.'}
              {decisionMode === 'openai_decisions'
                ? ' OpenAI Decisions uses your OpenAI API configuration to compare candidate actions.'
                : ''}
            </p>
            {decisionMode === 'openai_decisions' && (
              <label>
                Custom candidate actions (optional)
                <textarea
                  aria-label="Custom candidate actions (optional)"
                  aria-describedby="candidate-action-help"
                  value={candidates}
                  onChange={(e) => setCandidates(e.target.value)}
                  rows={3}
                  placeholder={
                    'One action per line, with an optional | description\nPilot a price reduction | Test in one region first\nImprove after-sales support | Prioritize customer feedback'
                  }
                />
                <small id="candidate-action-help">
                  Leave blank to use built-in actions. The system retains a “Gather more evidence”
                  option.
                </small>
              </label>
            )}
          </details>
          {!canWrite && (
            <p className="help">
              Your role can view records. Creating analyses requires an analyst or administrator
              role.
            </p>
          )}
        </form>
        <aside className="team-panel">
          <div className="team-heading">
            <span className="eyebrow">Your analysis team</span>
            <span className="badge">4 specialist perspectives</span>
          </div>
          {team.map(([id, focus]) => (
            <div key={id} className="team-member">
              <span className={'agent-symbol agent-' + id}>
                <Icon
                  name={
                    id === 'competitive'
                      ? 'chart'
                      : id === 'customer'
                        ? 'users'
                        : id === 'product'
                          ? 'grid'
                          : 'globe'
                  }
                  size={20}
                />
              </span>
              <div>
                <b>{agentLabels[id]}</b>
                <small>{focus}</small>
              </div>
              <span className="team-dot" />
            </div>
          ))}
          <p className="team-note">
            <i />
            Relevant specialists and tools are selected for each question
          </p>
        </aside>
      </div>
      {!run && config.scenarios?.length > 0 && (
        <>
          <div className="section-label">
            <span>Start with an example</span>
            <small>Synthetic cases for exploring the workflow</small>
          </div>
          <div className="scenario-grid">
            {config.scenarios.map((s, i) => (
              <button
                key={s.id}
                className={'scenario' + (dataset === s.dataset_id ? ' active' : '')}
                disabled={busy || !canWrite}
                onClick={() => {
                  setQuestion(s.question);
                  setDataset(s.dataset_id);
                  setDocuments([]);
                  setParent(undefined);
                }}
              >
                <span>0{i + 1}</span>
                <b>{s.title}</b>
                <small>{s.tag}</small>
                <Icon name="arrow" size={16} />
              </button>
            ))}
          </div>
        </>
      )}
      {pollError && (
        <Notice error>
          {pollError}
          <button className="button text small" onClick={() => setPollVersion((v) => v + 1)}>
            Reconnect to task
          </button>
        </Notice>
      )}
      {runId && !run && !pollError && <Loading label="Loading analysis record…" />}
      {isRunning(run?.status) && (
        <div className="panel progress-panel" role="status">
          <div>
            <span className="spinner" />
            <div>
              <b>
                {run?.status === 'queued' ? 'Analysis queued' : 'Gathering evidence and analyzing'}
              </b>
              <p>
                {run?.events?.at(-1)?.message ||
                  'The background task is saved. You can leave this page and return later.'}
              </p>
            </div>
            {canWrite && (
              <button className="button secondary small" onClick={() => void changeRun('cancel')}>
                Cancel task
              </button>
            )}
          </div>
          <div className="progress-steps">
            {[
              'Planning and routing',
              'Query and retrieval',
              'Decision and explanation',
              'Awaiting human review',
            ].map((label, i) => (
              <span key={label}>
                <i>{i + 1}</i>
                {label}
              </span>
            ))}
          </div>
        </div>
      )}
      {run && ['failed', 'cancelled'].includes(run.status) && (
        <div className="panel failure-panel">
          <Status status={run.status} />
          <h2>
            {run.status === 'failed' ? 'This analysis could not be completed' : 'Task cancelled'}
          </h2>
          <p>
            {run.error ||
              'Your sources and history are preserved. Adjust the settings and try again.'}
          </p>
          {canWrite && (
            <button className="button secondary" onClick={() => void changeRun('retry')}>
              <Icon name="refresh" size={16} />
              Retry task
            </button>
          )}
        </div>
      )}
      {!runId && !run && (
        <Empty title="Turn your sources into informed decisions." icon="spark">
          Complete an analysis to see recommendations, supporting evidence, and a traceable
          execution history here.
        </Empty>
      )}
      {run && result && d && (
        <section className="results-section">
          <div className="results-heading">
            <div>
              <p className="eyebrow">ANALYSIS BRIEF</p>
              <h2>{result.dataset.name}</h2>
            </div>
            <div>
              <Status status={run.status} />
              <small>{date(run.created_at)}</small>
            </div>
          </div>
          {Object.keys(result.metrics || {}).length > 0 && (
            <div className="metric-grid">
              <Metric
                label="Revenue change"
                value={percent(result.metrics.revenue_growth)}
                detail="Current vs previous period"
              />
              <Metric
                label="Gross margin"
                value={percent(result.metrics.gross_margin)}
                detail="(Revenue - cost) / revenue"
              />
              <Metric
                label="Customer churn"
                value={percent(result.metrics.churn_rate)}
                detail="Lost customers / customers at period start"
              />
              <Metric
                label="Return rate"
                value={percent(result.metrics.return_rate)}
                detail="Returned units / sold units"
              />
            </div>
          )}
          {result.local_routing && <LocalRoutingSummary routing={result.local_routing} />}
          <div className="tabs" role="tablist" aria-label="Analysis results">
            {[
              ['overview', 'Decision overview'],
              ['agents', 'Specialist findings'],
              ['evidence', `Source evidence · ${result.evidence.length}`],
              ['trace', 'Execution history'],
            ].map(([key, label]) => (
              <button
                key={key}
                role="tab"
                aria-selected={tab === key}
                aria-controls="result-panel"
                className={tab === key ? 'active' : ''}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <div id="result-panel" role="tabpanel">
            {tab === 'overview' && (
              <div className="decision-grid">
                <article className="recommendation">
                  <span className="eyebrow">Recommended next step</span>
                  <span className="recommendation-icon">
                    <Icon name="arrow" size={27} />
                  </span>
                  <h2>{d.actions[d.choice]?.title || d.choice}</h2>
                  <p>{result.rationale}</p>
                  {citationButtons(result.rationale_evidence_ids)}
                  <footer>
                    <span>
                      {decisionProviderLabel(d.provider)} · {d.model}
                    </span>
                    {d.confidence != null && <span>Model confidence {percent(d.confidence)}</span>}
                  </footer>
                </article>
                <section className="panel ranking">
                  <div className="section-heading">
                    <h3>Alternative actions</h3>
                    <span className="eyebrow">
                      {probabilistic
                        ? 'Model choice probability'
                        : ranking.length
                          ? 'Rule score / 100'
                          : 'No numeric ranking'}
                    </span>
                  </div>
                  {ranking.length
                    ? ranking.map(([key, value]) => (
                        <div className="rank-row" key={key}>
                          <div>
                            <span>{d.actions[key]?.title || key}</span>
                            <b>{probabilistic ? percent(value) : fmt(value)}</b>
                          </div>
                          <div className="bar">
                            <i
                              style={{
                                width: `${Math.min(100, Math.max(0, probabilistic ? value * 100 : value))}%`,
                              }}
                            />
                          </div>
                        </div>
                      ))
                    : Object.entries(d.actions)
                        .filter(([key]) => key !== d.choice)
                        .map(([key, value]) => (
                          <p className="alternative" key={key}>
                            {value.title}
                          </p>
                        ))}
                  {!ranking.length && Object.keys(d.actions).length < 2 && (
                    <p className="help">No additional actions were proposed.</p>
                  )}
                  <p className="help">
                    {probabilistic
                      ? 'Probabilities describe the model’s choice among these actions, not the chance of business success.'
                      : ranking.length
                        ? 'Scores use explicit rules. They are not probabilities and do not need to sum to 100.'
                        : 'The model recommends an action using the evidence, without assigning a numeric probability of success.'}
                  </p>
                </section>
              </div>
            )}
            {tab === 'agents' && (
              <div className="findings-grid">
                {result.agents.map((agent) => (
                  <article key={agent.id} className="panel finding">
                    <header>
                      <span className={'agent-symbol agent-' + agent.id}>
                        <Icon name="spark" size={19} />
                      </span>
                      <div>
                        <h3>{agentLabels[agent.id] || agent.name}</h3>
                        <small>
                          {agent.tool_calls} tool calls · {fmt(agent.elapsed_ms)} ms
                        </small>
                      </div>
                    </header>
                    {agent.claims.map((claim, i) => (
                      <div className="claim" key={i}>
                        <p>{claim.text}</p>
                        {citationButtons(claim.evidence_ids)}
                      </div>
                    ))}
                    <p className="help">{agent.limitations?.join(' ')}</p>
                  </article>
                ))}
              </div>
            )}
            {tab === 'evidence' && (
              <div className="evidence-list">
                {result.evidence.map((e) => (
                  <details key={e.id} className="panel evidence">
                    <summary>
                      <span className={'source-icon ' + (e.kind === 'sql' ? '' : 'document-icon')}>
                        <Icon name={e.kind === 'sql' ? 'database' : 'file'} />
                      </span>
                      <span>
                        <b>{e.title}</b>
                        <small>
                          {e.kind === 'sql'
                            ? `Read-only query · ${e.rows?.length || 0} rows`
                            : `${e.source || 'Document'} · ${e.location || 'Source excerpt'}`}
                        </small>
                      </span>
                      <Icon name="plus" size={17} />
                    </summary>
                    <div className="evidence-body">
                      <EvidenceBody evidence={e} />
                    </div>
                  </details>
                ))}
              </div>
            )}
            {tab === 'trace' && (
              <div className="trace-grid">
                <div className="panel trace">
                  <h3>Execution trace</h3>
                  {run.events?.map((event, i) => (
                    <div className="trace-step" key={i}>
                      <span>{String(i + 1).padStart(2, '0')}</span>
                      <div>
                        <b>{event.stage}</b>
                        <p>{event.message}</p>
                        <small>{date(event.at)}</small>
                      </div>
                    </div>
                  ))}
                  {result.plan && (
                    <details>
                      <summary>Analysis plan and tool routing</summary>
                      <p>{result.plan.objective}</p>
                      {result.plan.tasks.map((task, i) => (
                        <div className="plan-task" key={i}>
                          <b>
                            {agentLabels[task.specialist] || task.specialist} · {task.route}
                          </b>
                          <p>{task.objective}</p>
                        </div>
                      ))}
                    </details>
                  )}
                  {!!result.tool_trace?.length && (
                    <details>
                      <summary>Tool call details ({result.tool_trace.length})</summary>
                      {result.tool_trace.map((trace, i) => (
                        <div className="tool-trace" key={trace.id || i}>
                          <b>
                            {trace.agent} · {trace.tool}
                          </b>
                          <small>
                            {trace.status} · {fmt(trace.elapsed_ms)} ms
                          </small>
                          {trace.input != null && <pre>{pretty(trace.input)}</pre>}
                          {trace.error && <Notice error>{trace.error}</Notice>}
                        </div>
                      ))}
                    </details>
                  )}
                </div>
                <div className="panel trace-metrics">
                  <h3>This run</h3>
                  <dl>
                    <dt>Total duration</dt>
                    <dd>{fmt(result.measurement.elapsed_ms / 1000)} s</dd>
                    <dt>Tool calls</dt>
                    <dd>{result.measurement.tool_calls}</dd>
                    <dt>Valid citation coverage</dt>
                    <dd>{percent(result.measurement.citation_coverage)}</dd>
                    <dt>API cost</dt>
                    <dd>
                      {result.measurement.estimated_cost == null
                        ? 'Not estimated'
                        : `$${result.measurement.estimated_cost.toFixed(4)}`}
                    </dd>
                    <dt>Decision engine</dt>
                    <dd>{d.model}</dd>
                  </dl>
                  <p className="help">
                    Citation coverage checks whether source identifiers are valid. Human review is
                    still needed to verify that sources support the claims. Local API costs exclude
                    hardware costs.
                  </p>
                </div>
              </div>
            )}
          </div>
          {result.verification &&
            (result.verification.conflicts?.length > 0 ||
              result.verification.missing_information?.length > 0 ||
              result.verification.unsupported_claims?.length > 0) && (
              <details className="verification panel">
                <summary>Evidence checks: items to review</summary>
                {[
                  ...(result.verification.conflicts || []),
                  ...(result.verification.missing_information || []),
                  ...(result.verification.unsupported_claims || []),
                ].map((item, i) => (
                  <p key={i}>{pretty(item)}</p>
                ))}
              </details>
            )}
          <section className="panel review-panel">
            <div>
              <p className="eyebrow">HUMAN IN THE LOOP</p>
              <h2>
                {run.status === 'approved'
                  ? 'This version has been approved.'
                  : run.status === 'rejected'
                    ? 'Requested improvements have been recorded.'
                    : 'The final decision is yours.'}
              </h2>
              <p className="help">
                Review the evidence, definitions, and assumptions, then record your decision.
              </p>
            </div>
            <div>
              {run.status === 'pending_review' ? (
                canReview ? (
                  <div className="review-form">
                    {!auth.enabled && (
                      <label>
                        Reviewer
                        <input
                          value={reviewer}
                          onChange={(e) => setReviewer(e.target.value)}
                          placeholder="Enter your name"
                          minLength={2}
                        />
                      </label>
                    )}
                    <label>
                      Review notes
                      <textarea
                        rows={3}
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                        placeholder="Which evidence did you verify? What still needs to change?"
                        maxLength={2000}
                      />
                    </label>
                    <div className="inline-actions">
                      <button
                        className="button secondary"
                        disabled={reviewBusy}
                        onClick={() => void review('rejected')}
                      >
                        Request revision
                      </button>
                      <button
                        className="button primary"
                        disabled={reviewBusy}
                        onClick={() => void review('approved')}
                      >
                        <Icon name="check" size={16} />
                        Approve this version
                      </button>
                    </div>
                  </div>
                ) : (
                  <Notice>
                    Awaiting a reviewer or administrator. You can continue checking the source
                    evidence.
                  </Notice>
                )
              ) : (
                run.review && (
                  <div className="review-record">
                    <p>
                      <b>{run.review.reviewer}</b> · {date(run.review.created_at)}
                    </p>
                    {run.review.notes && <blockquote>{run.review.notes}</blockquote>}
                    {run.status === 'approved' ? (
                      <div className="inline-actions">
                        <a
                          className="button primary"
                          href={`/api/analyses/${encodeURIComponent(run.id)}/report?format=pdf`}
                          download
                        >
                          <Icon name="download" size={16} />
                          PDF report
                        </a>
                        <a
                          className="button secondary"
                          href={`/api/analyses/${encodeURIComponent(run.id)}/report?format=html`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          HTML report
                        </a>
                        <a
                          className="button text"
                          href={`/api/analyses/${encodeURIComponent(run.id)}/export`}
                          download
                        >
                          JSON
                        </a>
                      </div>
                    ) : (
                      canWrite && (
                        <button className="button primary" onClick={revise}>
                          Revise and analyze again
                          <Icon name="arrow" size={16} />
                        </button>
                      )
                    )}
                  </div>
                )
              )}
            </div>
          </section>
          <details className="limitations">
            <summary>
              Methods and limitations · {result.warnings?.length || 0}{' '}
              {result.warnings?.length === 1 ? 'note' : 'notes'}
            </summary>
            <ul>
              {result.warnings?.map((warning, i) => (
                <li key={i}>{warning}</li>
              ))}
            </ul>
          </details>
          {['approved', 'rejected'].includes(run.status) && <Feedback runId={run.id} />}
        </section>
      )}
      {selectedEvidence && (
        <Modal title={selectedEvidence.title} onClose={() => setSelectedEvidence(null)}>
          <EvidenceBody evidence={selectedEvidence} />
        </Modal>
      )}
    </>
  );
}
function EvidenceBody({ evidence }: { evidence: Evidence }) {
  return evidence.kind === 'sql' ? (
    <>
      {evidence.sql && (
        <pre>
          <code>{evidence.sql}</code>
        </pre>
      )}
      <DataTable data={evidence} />
      <QueryChart data={evidence} />
    </>
  ) : (
    <>
      <blockquote className="document-content">{evidence.content}</blockquote>
      <p className="help">
        {evidence.source} · {evidence.location || `Excerpt ${evidence.ordinal || ''}`}
      </p>
      {evidence.retrieval_note && <p className="help">{evidence.retrieval_note}</p>}
      {evidence.file_id && (
        <a
          className="button secondary"
          href={'/api/files/' + encodeURIComponent(evidence.file_id)}
          download
        >
          <Icon name="download" size={16} />
          Download original file
        </a>
      )}
    </>
  );
}
function Feedback({ runId }: { runId: string }) {
  const { notify } = useWorkspace();
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    try {
      await api('/analyses/' + encodeURIComponent(runId) + '/feedback', {
        rating: Number(form.get('rating')),
        task_success: form.get('success') === 'yes',
        notes: String(form.get('notes') || ''),
      });
      setDone(true);
      notify('Feedback recorded.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="feedback">
      <summary>Was this analysis useful for your work?</summary>
      {done ? (
        <p>Thank you. Your feedback is saved in the evaluation records.</p>
      ) : (
        <form onSubmit={submit}>
          <div className="form-grid">
            <label>
              Overall rating
              <select name="rating" defaultValue="4">
                {[5, 4, 3, 2, 1].map((n) => (
                  <option key={n} value={n}>
                    {n} / 5
                  </option>
                ))}
              </select>
            </label>
            <label>
              Did you complete your task?
              <select name="success">
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </label>
          </div>
          <label>
            Suggestions (optional)
            <input name="notes" maxLength={2000} />
          </label>
          <button className="button secondary small" disabled={busy}>
            Submit feedback
          </button>
        </form>
      )}
    </details>
  );
}
