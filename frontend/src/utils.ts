import type {
  AgentMode,
  CandidateAction,
  Config,
  DecisionMode,
  Document,
  LocalModels,
  LocalProfile,
} from './types';
export const localProfileLabels: Record<LocalProfile, string> = {
  fast: 'Local Fast',
  quality: 'Local Quality',
  auto: 'Local Auto',
  custom: 'Custom local',
};
export function retrievalModelLabel(config: Config): string {
  if (
    (config.retrieval_mode || config.retrieval) === 'lexical' ||
    config.embedding_provider === 'lexical'
  )
    return 'Keyword retrieval · No embedding model';
  return config.embedding_model || 'Keyword retrieval';
}
export function localProfileIssue(
  profile: LocalProfile,
  config: Config,
  models: LocalModels | null,
): string | null {
  if (profile === 'custom') return null;
  const definition = config.local_profiles?.[profile];
  if (!definition) return 'This local run mode is not configured on the server.';
  if (!definition.available)
    return definition.unavailable_reason || 'This local run mode is unavailable.';
  if (!models?.available)
    return models?.error || 'Connect to Ollama and refresh local models first.';
  const missing = definition.required_models.filter((model) => !models.models.includes(model));
  return missing.length
    ? `Download the required model${missing.length === 1 ? '' : 's'} in Ollama, then refresh: ${missing.join(', ')}.`
    : null;
}
export const statusLabels: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  pending_review: 'Pending review',
  approved: 'Approved',
  rejected: 'Revision requested',
  failed: 'Failed',
  cancelled: 'Cancelled',
  completed: 'Completed',
  ready: 'Ready',
  active: 'Enabled',
  syncing: 'Syncing',
};
export const roleLabels: Record<string, string> = {
  admin: 'Administrator',
  analyst: 'Analyst',
  reviewer: 'Reviewer',
  viewer: 'Viewer',
};
export const agentLabels: Record<string, string> = {
  competitive: 'Competitive intelligence',
  customer: 'Customer analysis',
  product: 'Product analysis',
  market: 'Market analysis',
};
export function fmt(value: unknown, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('en-SG', { maximumFractionDigits: digits }).format(value)
    : value == null
      ? '—'
      : String(value);
}
export function percent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';
}
export function date(value?: string): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.valueOf())
    ? value
    : d.toLocaleString('en-SG', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
}
export function eligibleDocuments(documents: Document[], datasetId: string): Document[] {
  return documents.filter((d) => !datasetId || !d.dataset_id || d.dataset_id === datasetId);
}
export function compatibleDecision(
  mode: AgentMode,
  decision: DecisionMode | 'jev',
  localOnly: boolean,
  decisionsAvailable = true,
): DecisionMode {
  const normalized = decision === 'jev' ? 'openai_decisions' : decision;
  if ((localOnly || !decisionsAvailable) && normalized === 'openai_decisions')
    return mode === 'local' ? 'baseline' : 'llm';
  return mode === 'local' && normalized === 'llm' ? 'baseline' : normalized;
}
export function decisionProviderLabel(provider: string): string {
  return (
    {
      baseline: 'Rule baseline',
      openai_decisions: 'OpenAI Decisions',
      jev: 'Legacy TypeSafe JEV',
      live: 'OpenAI',
      ollama: 'Ollama',
    }[provider] || provider
  );
}
export function isProbabilisticDecision(provider?: string): boolean {
  return provider === 'openai_decisions' || provider === 'jev';
}
export function parseCandidates(value: string): CandidateAction[] | undefined {
  const lines = value
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) return undefined;
  if (lines.length > 12) throw new Error('Enter no more than 12 candidate actions.');
  return lines.map((line, i) => {
    const [title, ...rest] = line.split('|');
    if (!title.trim() || title.trim().length > 160)
      throw new Error('Candidate action titles must contain 1–160 characters.');
    return {
      id: `action_${i + 1}`,
      title: title.trim(),
      description: rest.join('|').trim() || title.trim(),
    };
  });
}
export function isRunning(status?: string): boolean {
  return status === 'queued' || status === 'running';
}
export function pretty(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}
