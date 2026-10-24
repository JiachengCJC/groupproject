export interface User {
  id: string;
  email: string;
  name: string;
  role: 'admin' | 'analyst' | 'reviewer' | 'viewer';
  workspace_id?: string;
  disabled?: boolean;
}
export interface AuthStatus {
  enabled: boolean;
  setup_required: boolean;
  setup_token_required?: boolean;
  user: User | null;
}
export interface Scenario {
  id: string;
  title: string;
  description: string;
  question: string;
  dataset_id: string;
  tag: string;
}
export interface Config {
  version?: string;
  deployment?: string;
  local_only?: boolean;
  auth_enabled?: boolean;
  live_agents_available: boolean;
  openai_decisions_available: boolean;
  openai_model?: string;
  ollama_model?: string;
  ollama_vision_model?: string;
  openai_decision_model?: string;
  database?: string;
  worker_mode?: string;
  retrieval?: string;
  retrieval_mode?: string;
  embedding_provider?: string;
  embedding_model?: string;
  local_profiles?: Partial<Record<NamedLocalProfile, LocalProfileConfig>>;
  scenarios: Scenario[];
  max_file_bytes?: number;
}
export interface Column {
  name: string;
  type: string;
  label?: string;
}
export interface Dataset {
  id: string;
  name: string;
  row_count: number;
  columns: Column[];
  synthetic: boolean;
  created_at?: string;
}
export interface Document {
  id: string;
  title: string;
  source: string;
  dataset_id?: string | null;
  chunk_count?: number;
  synthetic?: boolean;
  category?: string;
  extraction_method?: string;
  file_id?: string;
  content?: string;
  warnings?: string[];
}
export interface Sources {
  datasets: Dataset[];
  documents: Document[];
}
export interface LocalModels {
  available: boolean;
  models: string[];
  error?: string;
  default_model?: string;
}
export type AgentMode = 'local' | 'ollama' | 'live';
export type DecisionMode = 'baseline' | 'llm' | 'openai_decisions';
export type NamedLocalProfile = 'fast' | 'quality' | 'auto';
export type LocalProfile = 'custom' | NamedLocalProfile;
export interface LocalProfileConfig {
  label: string;
  description: string;
  models: { planner: string; analysis: string };
  required_models: string[];
  available: boolean;
  unavailable_reason: string | null;
}
export interface LocalRouting {
  profile: NamedLocalProfile;
  planner_model: string;
  analysis_model: string;
  reason: string;
  policy_version: string;
  stages: Record<string, string>;
  vision_model: string | null;
  embedding_model: string | null;
}
export interface CandidateAction {
  id: string;
  title: string;
  description: string;
}
export interface AnalysisRequest {
  question: string;
  dataset_id?: string | null;
  document_ids: string[];
  agent_mode: AgentMode;
  decision_mode: DecisionMode;
  model?: string;
  local_profile?: LocalProfile;
  routing_mode?: 'auto' | 'all';
  parent_run_id?: string;
  candidate_actions?: CandidateAction[];
}
export interface QueryResult {
  sql?: string;
  columns: string[];
  rows: Record<string, unknown>[];
  truncated?: boolean;
  dataset_id?: string;
}
export interface Evidence extends Partial<QueryResult> {
  id: string;
  kind: string;
  title: string;
  source?: string;
  content?: string;
  relevance?: number;
  location?: string;
  document_id?: string;
  file_id?: string;
  synthetic?: boolean;
  ordinal?: number;
  retrieval_note?: string;
}
export interface Claim {
  text: string;
  evidence_ids: string[];
}
export interface Agent {
  id: string;
  name: string;
  summary: string;
  claims: Claim[];
  limitations: string[];
  tool_calls: number;
  elapsed_ms: number;
}
export interface Decision {
  provider: string;
  model: string;
  choice: string;
  actions: Record<string, { title: string; description: string }>;
  scores?: Record<string, number> | null;
  probabilities?: Record<string, number> | null;
  confidence?: number | null;
}
export interface ToolTrace {
  id?: string;
  agent?: string;
  tool: string;
  status: string;
  elapsed_ms?: number;
  input?: unknown;
  error?: string;
}
export interface AnalysisResult {
  question: string;
  dataset: { name: string; id?: string };
  metrics: Record<string, number | null>;
  agents: Agent[];
  evidence: Evidence[];
  decision: Decision;
  rationale: string;
  rationale_evidence_ids: string[];
  warnings: string[];
  agent_mode: string;
  decision_mode: string;
  local_routing?: LocalRouting;
  measurement: {
    elapsed_ms: number;
    tool_calls: number;
    citation_coverage: number;
    citation_coverage_note?: string;
    estimated_cost?: number | null;
    provider_usage?: unknown[];
  };
  plan?: {
    objective: string;
    tasks: { specialist: string; route: string; objective: string; search_query?: string }[];
    missing_information: string[];
  };
  tool_trace?: ToolTrace[];
  verification?: {
    status: string;
    conflicts: unknown[];
    unsupported_claims: unknown[];
    missing_information: unknown[];
    checks: unknown[];
  };
}
export interface Run {
  id: string;
  status: string;
  created_at: string;
  question?: string;
  request: Omit<AnalysisRequest, 'decision_mode'> & { decision_mode: DecisionMode | 'jev' };
  result?: AnalysisResult | null;
  error?: string;
  events: { at: string; stage: string; message: string }[];
  review?: { reviewer: string; notes: string; decision: string; created_at: string } | null;
}
export interface Benchmark {
  cases: {
    title: string;
    expected: string;
    actual: string;
    passed: boolean;
    completed: boolean;
    citation_coverage: number;
    elapsed_ms: number;
  }[];
  pass_rate: number;
  completion_rate: number;
  mean_citation_coverage: number;
  created_at: string;
  note: string;
}
export interface Connector {
  id: string;
  name: string;
  kind: 'web' | 'rss' | 'kaggle';
  url?: string;
  dataset?: string;
  dataset_id?: string | null;
  status?: string;
  last_sync?: string;
  last_error?: string;
  [key: string]: unknown;
}
export interface EvaluationCase {
  id: string;
  name: string;
  question: string;
  split: string;
  expected_route?: string;
  expected_action?: string;
  dataset_id?: string;
  document_ids?: string[];
  [key: string]: unknown;
}
export interface EvaluationResult {
  id: string;
  created_at?: string;
  status?: string;
  split?: string;
  cases?: Record<string, unknown>[];
  metrics?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  sample_counts?: Record<string, number>;
  completion_rate?: number;
  note?: string;
  [key: string]: unknown;
}
export interface Schedule {
  id: string;
  name: string;
  interval_minutes: number;
  enabled: boolean;
  request: AnalysisRequest;
  next_run_at?: string;
  last_run_at?: string;
  last_error?: string;
  [key: string]: unknown;
}
