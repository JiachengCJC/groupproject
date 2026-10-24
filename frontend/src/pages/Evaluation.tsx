import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from '../api';
import { usePermissions, useWorkspace } from '../context';
import type {
  AgentMode,
  Benchmark,
  DecisionMode,
  EvaluationCase,
  EvaluationResult,
  LocalProfile,
} from '../types';
import { compatibleDecision, date, fmt, localProfileIssue, percent, pretty } from '../utils';
import LocalProfileSettings from '../components/LocalProfileSettings';
import { TaskProgress } from '../components/Tasks';
import { Empty, Icon, Metric, Notice, PageHeading, Status } from '../components/ui';
const metricLabels: Record<string, string> = {
  run_count: 'Total analysis runs',
  terminal_count: 'Finished analyses',
  completed_count: 'Completed analyses',
  reviewed_count: 'Reviewed analyses',
  sql_result_match: 'SQL result match',
  correct_tool_route: 'Correct tool route',
  reference_coverage: 'Valid citation coverage',
  action_match: 'Expected action match',
  model_judged_faithfulness: 'Model-assisted faithfulness score',
  latency_p50_ms: 'P50 latency (ms)',
  latency_p95_ms: 'P95 latency (ms)',
  input_tokens: 'Input tokens',
  output_tokens: 'Output tokens',
  known_api_cost: 'Known API cost',
  unpriced_runs: 'Unpriced runs',
  user_satisfaction: 'User satisfaction / 5',
  user_task_success: 'User task success rate',
  completion_rate: 'Task completion rate',
  task_completion_rate: 'Task completion rate',
  sql_accuracy: 'SQL result accuracy',
  text_to_sql_accuracy: 'SQL result accuracy',
  retrieval_recall: 'Retrieval recall',
  retrieval_recall_at_k: 'Retrieval recall',
  recall_at_k: 'Retrieval recall',
  attribution_coverage: 'Citation coverage',
  citation_coverage: 'Valid citation coverage',
  tool_call_accuracy: 'Tool selection accuracy',
  routing_accuracy: 'Routing accuracy',
  action_accuracy: 'Candidate action match',
  mean_latency_ms: 'Mean latency (ms)',
  mean_elapsed_ms: 'Mean latency (ms)',
  human_approval_rate: 'Human approval rate',
  average_rating: 'Average user rating',
  user_task_success_rate: 'User task success rate',
  p95_latency_ms: 'P95 latency (ms)',
  p50_latency_ms: 'P50 latency (ms)',
  analyses: 'Analysis records',
  total_runs: 'Analysis records',
  total_feedback: 'User feedback',
  feedback_count: 'User feedback',
  estimated_cost: 'Estimated API cost',
  estimated_cost_usd: 'Estimated API cost',
  failed_runs: 'Failed tasks',
  total_cases: 'Evaluation cases',
};
function metricValue(key: string, value: unknown) {
  if (typeof value !== 'number') return value == null ? 'No samples yet' : pretty(value);
  return /rate|accuracy|recall|coverage|agreement|match|correct_tool_route|user_task_success|faithfulness/.test(
    key,
  )
    ? percent(value)
    : fmt(value, 3);
}
export default function Evaluation() {
  const { notify, sources, config, models } = useWorkspace();
  const { canWrite } = usePermissions();
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const [cases, setCases] = useState<EvaluationCase[]>([]);
  const [results, setResults] = useState<EvaluationResult[]>([]);
  const [metrics, setMetrics] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState('');
  const [taskId, setTaskId] = useState('');
  const [mode, setMode] = useState<AgentMode>('local');
  const [decision, setDecision] = useState<DecisionMode>('baseline');
  const [split, setSplit] = useState('holdout');
  const [model, setModel] = useState(config.ollama_model || '');
  const [localProfile, setLocalProfile] = useState<LocalProfile>('custom');
  const namedProfile = mode === 'ollama' && localProfile !== 'custom';
  const profileIssue = mode === 'ollama' ? localProfileIssue(localProfile, config, models) : null;
  const [judgeMode, setJudgeMode] = useState<'none' | 'ollama' | 'live'>('none');
  useEffect(() => {
    if (models?.available && !models.models.includes(model))
      setModel(
        models.models.includes(models.default_model || '')
          ? models.default_model!
          : models.models[0] || '',
      );
  }, [models, model]);
  async function load() {
    const responses = await Promise.allSettled([
      api<Benchmark | null>('/benchmarks'),
      api<EvaluationCase[]>('/evaluation/cases'),
      api<EvaluationResult[]>('/evaluation/results'),
      api<Record<string, unknown>>('/metrics'),
    ]);
    const messages: string[] = [];
    if (responses[0].status === 'fulfilled') setBenchmark(responses[0].value);
    else messages.push('Regression evaluation: ' + errorMessage(responses[0].reason));
    if (responses[1].status === 'fulfilled') setCases(responses[1].value);
    else messages.push('Evaluation cases: ' + errorMessage(responses[1].reason));
    if (responses[2].status === 'fulfilled') setResults(responses[2].value);
    else messages.push('Evaluation results: ' + errorMessage(responses[2].reason));
    if (responses[3].status === 'fulfilled') setMetrics(responses[3].value);
    else messages.push('Usage metrics: ' + errorMessage(responses[3].reason));
    setErrors(messages);
  }
  useEffect(() => {
    void load();
  }, []);
  async function runBenchmark() {
    setBusy('benchmark');
    try {
      setBenchmark(await api<Benchmark>('/benchmarks', {}));
      notify('Synthetic scenario regression check completed.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy('');
    }
  }
  async function createCase(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const element = e.currentTarget;
    const form = new FormData(element);
    setBusy('case');
    try {
      const selectedDocs = form.getAll('documents').map(String);
      const payload: Record<string, unknown> = {
        name: String(form.get('name')),
        question: String(form.get('question')),
        split: String(form.get('split')),
        document_ids: selectedDocs,
      };
      for (const key of [
        'dataset_id',
        'expected_sql',
        'expected_route',
        'expected_action',
        'expected_answer',
      ]) {
        const value = String(form.get(key) || '').trim();
        if (value) payload[key] = value;
      }
      const expectedDocs = String(form.get('expected_document_ids') || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      if (expectedDocs.length) payload.expected_document_ids = expectedDocs;
      await api('/evaluation/cases', payload);
      element.reset();
      await load();
      notify('Evaluation case saved.');
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy('');
    }
  }
  async function evaluate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (profileIssue) {
      notify(profileIssue, true);
      return;
    }
    setBusy('evaluation');
    try {
      const result = await api<{ status?: string; task_id?: string }>('/evaluation/run', {
        agent_mode: mode,
        decision_mode: decision,
        split,
        judge_mode: judgeMode,
        ...(judgeMode === 'ollama' ? { judge_model: model } : {}),
        ...(mode === 'ollama'
          ? namedProfile
            ? { local_profile: localProfile }
            : {
                model: model || models?.default_model || models?.models[0],
                local_profile: 'custom',
              }
          : {}),
      });
      if (result.task_id) setTaskId(result.task_id);
      notify(
        result.task_id || result.status === 'queued'
          ? 'Evaluation queued. Refresh later to view the results.'
          : 'Evaluation completed.',
      );
      await load();
    } catch (err) {
      notify(errorMessage(err), true);
    } finally {
      setBusy('');
    }
  }
  const metricOrder = [
    'completion_rate',
    'human_approval_rate',
    'latency_p50_ms',
    'latency_p95_ms',
    'user_satisfaction',
    'user_task_success',
    'known_api_cost',
    'unpriced_runs',
    'run_count',
    'reviewed_count',
    'input_tokens',
    'output_tokens',
  ];
  const topMetrics = metricOrder
    .filter((key) => key in metrics)
    .map((key) => [key, metrics[key]] as const);
  return (
    <>
      <PageHeading
        eyebrow="MEASURE BEFORE YOU TRUST"
        title="Make quality visible."
        description="Test answers with independent questions and improve your workflow with real feedback."
        action={
          <button className="button secondary" onClick={() => void load()} disabled={!!busy}>
            <Icon name="refresh" size={16} />
            Refresh results
          </button>
        }
      />
      {errors.map((error, i) => (
        <Notice error key={i}>
          {error}
        </Notice>
      ))}
      {topMetrics.length > 0 && (
        <div className="metric-grid">
          {topMetrics.map(([key, value]) => (
            <Metric
              key={key}
              label={metricLabels[key] || key.replaceAll('_', ' ')}
              value={metricValue(key, value)}
            />
          ))}
        </div>
      )}
      {typeof metrics.note === 'string' && <p className="help">{metrics.note}</p>}
      {taskId && (
        <TaskProgress
          taskId={taskId}
          onComplete={() => {
            void load();
          }}
        />
      )}
      <div className="section-heading evaluation-heading">
        <div>
          <p className="eyebrow">QUALITY EVALUATION</p>
          <h2>Independent evaluation</h2>
        </div>
        <span className="badge">{cases.length} labeled cases</span>
      </div>
      <div className="evaluation-layout">
        <section className="panel">
          <div className="section-heading">
            <h3>Evaluation history</h3>
            <small>Development and holdout results are reported separately</small>
          </div>
          {results.length ? (
            results.map((result) => (
              <details className="evaluation-result" key={result.id}>
                <summary>
                  <div>
                    <b>
                      {result.split === 'holdout'
                        ? 'Holdout set'
                        : result.split === 'development'
                          ? 'Development set'
                          : 'Quality evaluation'}
                    </b>
                    <small>
                      {date(result.created_at)} · {result.id}
                    </small>
                  </div>
                  {result.status && <Status status={result.status} />}
                </summary>
                {(result.summary || result.metrics) && (
                  <div className="evaluation-metrics">
                    {Object.entries(result.summary || result.metrics || {}).map(([key, value]) => (
                      <div key={key}>
                        <span>{metricLabels[key] || key.replaceAll('_', ' ')}</span>
                        <b>
                          {metricValue(key, value)}
                          {result.sample_counts?.[key] != null && (
                            <small>n = {result.sample_counts[key]}</small>
                          )}
                        </b>
                      </div>
                    ))}
                  </div>
                )}
                {!!result.cases?.length && (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Case</th>
                          <th>Result</th>
                          <th>Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.cases.map((item, i) => (
                          <tr key={i}>
                            <td>{String(item.name || item.title || item.case_id || i + 1)}</td>
                            <td>
                              {item.error
                                ? 'Failed'
                                : item.completed === false
                                  ? 'Incomplete'
                                  : 'Executed'}
                            </td>
                            <td>
                              <details>
                                <summary>View metrics</summary>
                                <pre>{pretty(item)}</pre>
                              </details>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {result.note && <p className="help">{result.note}</p>}
                {result.error != null && <Notice error>{String(result.error)}</Notice>}
              </details>
            ))
          ) : (
            <Empty title="Start with real questions and reference answers" icon="chart">
              Add labeled cases to evaluate SQL results, retrieved sources, and tool routing.
              Metrics without labels are reported as unavailable.
            </Empty>
          )}
        </section>
        {canWrite && (
          <form className="panel evaluation-run" onSubmit={evaluate}>
            <h3>Run quality evaluation</h3>
            <label>
              Case set
              <select value={split} onChange={(e) => setSplit(e.target.value)}>
                <option value="holdout">Holdout set</option>
                <option value="development">Development set</option>
              </select>
            </label>
            <label>
              Analysis model
              <select
                value={mode}
                onChange={(e) => {
                  const value = e.target.value as AgentMode;
                  setMode(value);
                  if (value !== 'ollama') setLocalProfile('custom');
                  setDecision(
                    compatibleDecision(
                      value,
                      value === 'local' ? 'baseline' : 'llm',
                      !!config.local_only,
                    ),
                  );
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
                onChange={(value) => {
                  setLocalProfile(value);
                  if (value !== 'custom') {
                    setDecision('llm');
                    if (judgeMode === 'live') setJudgeMode('none');
                  }
                }}
                disabled={!!busy}
              />
            )}
            {((mode === 'ollama' && !namedProfile) || judgeMode === 'ollama') && (
              <label>
                Local model
                <select
                  value={model || models?.default_model || models?.models[0] || ''}
                  onChange={(e) => setModel(e.target.value)}
                  required
                >
                  <option value="">Select a model</option>
                  {models?.models.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              </label>
            )}
            <label>
              Decision method
              <select
                value={decision}
                onChange={(e) => setDecision(e.target.value as DecisionMode)}
                disabled={namedProfile}
              >
                <option value="baseline">Rule scoring</option>
                <option value="llm" disabled={mode === 'local'}>
                  Model recommendation
                </option>
                <option
                  value="openai_decisions"
                  disabled={config.local_only || !config.openai_decisions_available}
                >
                  OpenAI Decisions
                </option>
              </select>
            </label>
            <label>
              Answer faithfulness evaluation
              <select
                value={judgeMode}
                onChange={(event) => setJudgeMode(event.target.value as 'none' | 'ollama' | 'live')}
              >
                <option value="none">Exact metrics only · No model judge</option>
                <option value="ollama">Local model-assisted evaluation</option>
                <option
                  value="live"
                  disabled={namedProfile || config.local_only || !config.live_agents_available}
                >
                  Cloud model-assisted evaluation
                </option>
              </select>
            </label>
            <p className="help">
              Model judging requires reference answers and verifiable sources and is only a
              supporting signal. Cloud analysis or judging sends relevant source material and incurs
              API charges. The holdout set measures generalization.
            </p>
            <button
              className="button primary full"
              disabled={!!busy || !!profileIssue || !cases.some((c) => c.split === split)}
            >
              {busy === 'evaluation' ? (
                <span className="spinner" />
              ) : (
                <Icon name="chart" size={17} />
              )}
              Run evaluation
            </button>
          </form>
        )}
      </div>
      {canWrite && (
        <details className="panel evaluation-add">
          <summary>
            <Icon name="plus" size={18} />
            Add a labeled case
          </summary>
          <form onSubmit={createCase}>
            <div className="form-grid">
              <label>
                Case name
                <input name="name" required maxLength={120} />
              </label>
              <label>
                Case set
                <select name="split" defaultValue="development">
                  <option value="development">Development set</option>
                  <option value="holdout">Holdout set</option>
                </select>
              </label>
            </div>
            <label>
              Business question
              <textarea name="question" required minLength={8} maxLength={2000} rows={2} />
            </label>
            <div className="form-grid">
              <label>
                Dataset
                <select name="dataset_id">
                  <option value="">Documents only</option>
                  {sources.datasets.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Expected tool route
                <select name="expected_route">
                  <option value="">Do not evaluate</option>
                  <option value="sql">SQL query</option>
                  <option value="rag">Document retrieval</option>
                  <option value="mixed">Query and retrieval</option>
                </select>
              </label>
            </div>
            <details>
              <summary>Select input documents ({sources.documents.length})</summary>
              <div className="document-choices">
                {sources.documents.map((d) => (
                  <label className="check-row" key={d.id}>
                    <input type="checkbox" name="documents" value={d.id} />
                    <span>
                      {d.title}
                      <small>{d.id}</small>
                    </span>
                  </label>
                ))}
              </div>
            </details>
            <label>
              Reference answer (optional)
              <textarea
                name="expected_answer"
                rows={3}
                placeholder="Record verified key facts and the correct conclusion."
              />
            </label>
            <details>
              <summary>Exact evaluation labels (optional)</summary>
              <label>
                Reference SQL
                <textarea
                  name="expected_sql"
                  rows={3}
                  placeholder="Compare query results using the same data snapshot."
                />
              </label>
              <label>
                Expected document IDs
                <input name="expected_document_ids" placeholder="Separate IDs with commas" />
              </label>
              <label>
                Expected action ID
                <input name="expected_action" placeholder="For example: protect_margin" />
              </label>
            </details>
            <button className="button secondary" disabled={!!busy}>
              Save case
            </button>
          </form>
        </details>
      )}
      <section className="benchmark-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">SYNTHETIC REGRESSION</p>
            <h2>Demo scenario regression checks</h2>
          </div>
          {canWrite && (
            <button
              className="button secondary"
              onClick={() => void runBenchmark()}
              disabled={!!busy}
            >
              {busy === 'benchmark' ? (
                <span className="spinner" />
              ) : (
                <Icon name="refresh" size={16} />
              )}
              Run five scenarios
            </button>
          )}
        </div>
        <Notice>
          These synthetic cases test fixed rules and software behavior. The pass rate does not
          measure real-world business accuracy.
        </Notice>
        {benchmark ? (
          <>
            <div className="metric-grid">
              <Metric label="Expected action match" value={percent(benchmark.pass_rate)} />
              <Metric label="Task completion rate" value={percent(benchmark.completion_rate)} />
              <Metric
                label="Valid citation coverage"
                value={percent(benchmark.mean_citation_coverage)}
              />
              <Metric label="Synthetic cases" value={benchmark.cases.length} />
            </div>
            <div className="panel table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Scenario</th>
                    <th>Expected action</th>
                    <th>Actual action</th>
                    <th>Duration</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {benchmark.cases.map((c, i) => (
                    <tr key={i}>
                      <td>{c.title}</td>
                      <td>{c.expected}</td>
                      <td>{c.actual}</td>
                      <td>{fmt(c.elapsed_ms)} ms</td>
                      <td>
                        <span className={'badge ' + (c.passed ? 'success' : 'danger')}>
                          {c.passed ? 'Passed' : 'Failed'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="panel">
            <Empty title="Check the core workflow" icon="chart">
              Regression checks use built-in synthetic sources without calling cloud models.
            </Empty>
          </div>
        )}
      </section>
    </>
  );
}
