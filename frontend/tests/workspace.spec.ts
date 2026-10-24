import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
const scenarios = [
  {
    id: 'pricing',
    title: 'Competitor price reduction',
    tag: 'Competition',
    question: 'A competitor has cut prices. Should we match them or protect margins?',
    dataset_id: 'data_a',
  },
  {
    id: 'retention',
    title: 'Customer churn',
    tag: 'Customers',
    question: 'How should we improve customer retention?',
    dataset_id: 'data_b',
  },
  {
    id: 'quality',
    title: 'Increasing returns',
    tag: 'Product',
    question: 'Should we prioritize product quality?',
    dataset_id: 'data_a',
  },
  {
    id: 'expansion',
    title: 'New market opportunity',
    tag: 'Market',
    question: 'Should we expand into the new market?',
    dataset_id: 'data_a',
  },
  {
    id: 'supply',
    title: 'Supply constraints',
    tag: 'Operations',
    question: 'Should we stabilize supply before marketing?',
    dataset_id: 'data_a',
  },
];
const sources = {
  datasets: [
    {
      id: 'data_a',
      name: 'Quarterly sales data',
      row_count: 6,
      synthetic: true,
      columns: [
        { name: 'period', type: 'TEXT' },
        { name: 'revenue', type: 'REAL' },
      ],
    },
    {
      id: 'data_b',
      name: 'Customer research data',
      row_count: 20,
      synthetic: false,
      columns: [{ name: 'customer', type: 'TEXT' }],
    },
  ],
  documents: [
    {
      id: 'doc_a',
      title: 'Competitor pricing report',
      source: 'Internal report',
      dataset_id: 'data_a',
      chunk_count: 2,
    },
    {
      id: 'shared',
      title: 'Team decision policy',
      source: 'Policy',
      dataset_id: null,
      chunk_count: 3,
    },
    {
      id: 'doc_b',
      title: 'Customer interviews',
      source: 'Interviews',
      dataset_id: 'data_b',
      chunk_count: 1,
    },
  ],
};
const fastModel = 'qwen3.5:4b-q4_K_M';
const qualityModel = 'qwen3.5:9b-q4_K_M';
const localProfiles = {
  fast: {
    label: 'Local Fast',
    description: 'Use the smaller model for daily analysis.',
    models: { planner: fastModel, analysis: fastModel },
    required_models: [fastModel],
    available: true,
    unavailable_reason: null,
  },
  quality: {
    label: 'Local Quality',
    description: 'Use the larger model for complex analysis.',
    models: { planner: qualityModel, analysis: qualityModel },
    required_models: [qualityModel],
    available: true,
    unavailable_reason: null,
  },
  auto: {
    label: 'Local Auto',
    description:
      'Plan with the smaller model and choose the analysis model from the task structure.',
    models: { planner: fastModel, analysis: qualityModel },
    required_models: [fastModel, qualityModel],
    available: true,
    unavailable_reason: null,
  },
};
const user = { id: 'user_1', name: 'Test administrator', email: 'test@example.com', role: 'admin' };
const result = {
  question: scenarios[0].question,
  dataset: { name: 'Quarterly sales data' },
  metrics: { revenue_growth: -0.24, gross_margin: 0.22, churn_rate: 0.04, return_rate: 0.02 },
  agents: [
    {
      id: 'competitive',
      name: 'Competitive',
      summary: 'Protect margins',
      claims: [
        {
          text: 'A price reduction would further compress the current margin.',
          evidence_ids: ['metrics'],
        },
      ],
      limitations: ['Demand elasticity needs validation'],
      tool_calls: 2,
      elapsed_ms: 32,
    },
  ],
  evidence: [
    {
      id: 'metrics',
      kind: 'sql',
      title: 'Period comparison',
      sql: 'SELECT period, revenue FROM data_a',
      columns: ['period', 'revenue'],
      rows: [
        { period: 'previous', revenue: 100000 },
        { period: 'current', revenue: 76000 },
      ],
    },
    {
      id: 'doc_a_1',
      kind: 'document',
      title: 'Competitor pricing report',
      content: 'The competitor price is 82. New pricing proposals require financial review.',
      source: 'Internal report',
      location: 'Page 1',
      file_id: 'file_1',
      document_id: 'doc_a',
    },
  ],
  decision: {
    provider: 'baseline',
    model: 'rules-v1',
    choice: 'protect_margin',
    actions: {
      protect_margin: {
        title: 'Protect margins with targeted offers first',
        description: 'Test a bundled offer before considering a broad price cut.',
      },
      gather_data: {
        title: 'Gather evidence before deciding',
        description: 'Collect demand data.',
      },
    },
    scores: { protect_margin: 95, gather_data: 35 },
    probabilities: null,
    confidence: null,
  },
  rationale:
    'Revenue fell by 24%, and the current gross margin is 22%. Test a targeted offer first and verify demand changes before adjusting prices.',
  rationale_evidence_ids: ['metrics', 'doc_a_1'],
  warnings: ['This is a synthetic business case.'],
  agent_mode: 'local',
  decision_mode: 'baseline',
  measurement: { elapsed_ms: 1200, tool_calls: 9, citation_coverage: 1, estimated_cost: 0 },
  plan: {
    objective: 'Review pricing and margins',
    tasks: [
      { specialist: 'competitive', route: 'hybrid', objective: 'Compare prices and margins' },
    ],
    missing_information: [],
  },
};
async function mockWorkspace(
  page: Page,
  options: {
    authenticated?: boolean;
    setup?: boolean;
    localOnly?: boolean;
    role?: string;
    decisionsAvailable?: boolean;
    decisionProvider?: 'openai_decisions' | 'jev';
    installedModels?: string[];
    profileBlocked?: boolean;
    initialProfile?: 'fast' | 'quality' | 'auto';
  } = {},
) {
  let authenticated = options.authenticated ?? true;
  let setup = options.setup ?? false;
  let runStatus = 'pending_review';
  let posted: Record<string, unknown> = options.initialProfile
    ? {
        local_profile: options.initialProfile,
        agent_mode: 'ollama',
        decision_mode: 'llm',
        model: 'historical-model:9b',
        local_profile_snapshot: { policy_version: 'historic' },
      }
    : {};
  const requests: { path: string; body: unknown }[] = [];
  let members = [
    user,
    {
      id: 'user_2',
      name: 'Analyst colleague',
      email: 'colleague@example.com',
      role: 'analyst',
      disabled: false,
    },
  ];
  const connectors: Record<string, unknown>[] = [];
  const schedules: Record<string, unknown>[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.slice(4);
    const method = route.request().method();
    let body: unknown;
    try {
      body = route.request().postDataJSON();
    } catch {
      body = null;
    }
    if (method !== 'GET') requests.push({ path, body });
    const json = (data: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/auth/status')
      return json({
        enabled: true,
        setup_required: setup,
        user: authenticated ? { ...user, role: options.role || 'admin' } : null,
      });
    if (path === '/auth/login' || path === '/auth/setup') {
      authenticated = true;
      setup = false;
      return json({ user });
    }
    if (path === '/auth/logout') {
      authenticated = false;
      return json({ success: true });
    }
    if (path === '/config')
      return json({
        live_agents_available: true,
        openai_decisions_available: options.decisionsAvailable ?? true,
        openai_decision_model: 'gpt-6-luna',
        local_only: options.localOnly || false,
        scenarios,
        ollama_model: 'qwen3:8b',
        ollama_vision_model: fastModel,
        embedding_model: 'bge-m3',
        embedding_provider: 'ollama',
        local_profiles: Object.fromEntries(
          Object.entries(localProfiles).map(([key, value]) => [
            key,
            {
              ...value,
              available: !options.profileBlocked,
              unavailable_reason: options.profileBlocked
                ? 'Configure local embeddings before selecting a local run mode.'
                : null,
            },
          ]),
        ),
        openai_model: 'configured-cloud-model',
        database: 'postgresql',
        worker_mode: 'external',
        retrieval_mode: 'hybrid',
      });
    if (path === '/sources') return json(sources);
    if (path === '/models/ollama')
      return json({
        available: true,
        models: options.installedModels || ['qwen3:8b', fastModel, qualityModel, 'bge-m3'],
        default_model: 'qwen3:8b',
      });
    if (path === '/analyses' && method === 'POST') {
      posted = body as Record<string, unknown>;
      return json({ id: 'run_1', status: 'queued' }, 202);
    }
    if (path === '/analyses' && method === 'GET')
      return json([
        {
          id: 'run_1',
          status: runStatus,
          created_at: '2026-10-24T08:00:00Z',
          question: scenarios[0].question,
        },
      ]);
    if (path === '/analyses/run_1/review') {
      runStatus = (body as { decision: string }).decision;
      return json({ status: runStatus });
    }
    if (path === '/analyses/run_1')
      return json({
        id: 'run_1',
        status: runStatus,
        created_at: '2026-10-24T08:00:00Z',
        request: {
          question: scenarios[0].question,
          dataset_id: 'data_a',
          document_ids: [],
          agent_mode: 'local',
          decision_mode: options.decisionProvider || 'baseline',
          ...posted,
        },
        result: options.decisionProvider
          ? {
              ...result,
              decision_mode: options.decisionProvider,
              decision: {
                ...result.decision,
                provider: options.decisionProvider,
                model: options.decisionProvider === 'jev' ? 'legacy-jev-model' : 'gpt-6-luna',
                scores: null,
                probabilities: { protect_margin: 0.82, gather_data: 0.18 },
                confidence: 0.76,
              },
            }
          : posted.local_profile && posted.local_profile !== 'custom'
            ? {
                ...result,
                decision: {
                  ...result.decision,
                  provider: 'ollama',
                  model: posted.local_profile === 'fast' ? fastModel : qualityModel,
                  scores: null,
                },
                local_routing: {
                  profile: posted.local_profile,
                  planner_model: posted.local_profile === 'quality' ? qualityModel : fastModel,
                  analysis_model: posted.local_profile === 'fast' ? fastModel : qualityModel,
                  reason:
                    posted.local_profile === 'auto'
                      ? 'Combined SQL and document evidence requires the quality model.'
                      : 'The requested local run mode selected this model.',
                  policy_version: 'local-profiles-v1',
                  stages: {
                    AnalysisPlan: posted.local_profile === 'quality' ? qualityModel : fastModel,
                    EvidenceAudit: posted.local_profile === 'fast' ? fastModel : qualityModel,
                  },
                  vision_model: fastModel,
                  embedding_model: 'bge-m3',
                },
              }
            : result,
        events: [
          { at: '2026-10-24T08:00:00Z', stage: 'route', message: 'Analysis scope confirmed.' },
          { at: '2026-10-24T08:00:01Z', stage: 'review', message: 'Awaiting review.' },
        ],
        review: ['approved', 'rejected'].includes(runStatus)
          ? {
              reviewer: user.name,
              notes: '',
              decision: runStatus,
              created_at: '2026-10-24T08:01:00Z',
            }
          : null,
      });
    if (path === '/uploads') return json({ task_id: 'task_upload', status: 'queued' }, 202);
    if (path === '/tasks/task_upload')
      return json({
        id: 'task_upload',
        kind: 'upload',
        status: 'completed',
        result: { documents: [{ id: 'new' }], warnings: [] },
      });
    if (path === '/tasks') return json([]);
    if (path === '/connectors' && method === 'POST') {
      const connector = { ...(body as object), id: 'connector_1' };
      connectors.push(connector);
      return json(connector, 201);
    }
    if (path === '/connectors') return json(connectors);
    if (path === '/connectors/connector_1/sync')
      return json({ task_id: 'task_sync', status: 'queued' }, 202);
    if (path === '/tasks/task_sync')
      return json({ id: 'task_sync', kind: 'sync', status: 'completed', result: {} });
    if (path === '/evaluation/run') return json({ task_id: 'task_eval', status: 'queued' }, 202);
    if (path === '/tasks/task_eval')
      return json({ id: 'task_eval', kind: 'evaluation', status: 'completed', result: {} });
    if (path === '/evaluation/cases' && method === 'POST')
      return json({ ...(body as object), id: 'case_1' }, 201);
    if (path === '/evaluation/cases')
      return json([
        {
          id: 'case_existing',
          name: 'Revenue comparison',
          question: 'Compare our revenue',
          split: 'holdout',
        },
      ]);
    if (path === '/evaluation/results')
      return json([
        {
          id: 'eval_1',
          split: 'holdout',
          created_at: '2026-10-24T08:00:00Z',
          summary: { sql_result_match: 1, retrieval_recall: null },
          sample_counts: { sql_result_match: 2, retrieval_recall: 0 },
          completion_rate: 1,
          cases: [],
        },
      ]);
    if (path === '/metrics')
      return json({
        completion_rate: 0.95,
        human_approval_rate: 0.8,
        latency_p50_ms: 1200,
        user_satisfaction: 4.3,
      });
    if (path === '/benchmarks') return json(null);
    if (path === '/schedules' && method === 'POST') {
      const schedule = { ...(body as object), id: 'schedule_1' };
      schedules.push(schedule);
      return json(schedule, 201);
    }
    if (path === '/schedules') return json(schedules);
    if (path === '/schedules/schedule_1') {
      Object.assign(schedules[0], body);
      return json(schedules[0]);
    }
    if (path === '/auth/users/user_2') {
      members = members.map((member) =>
        member.id === 'user_2' ? { ...member, ...(body as object) } : member,
      );
      return json({ id: 'user_2' });
    }
    if (path === '/auth/users') return json(members);
    if (path === '/datasets/data_a')
      return json({ dataset: sources.datasets[0], preview: result.evidence[0] });
    if (path === '/datasets/data_a/dictionary')
      return json({
        description: '',
        currency: 'SGD',
        timezone: 'Asia/Singapore',
        period_definition: '',
        columns: {},
        metrics: {},
      });
    return json({ detail: 'Unmocked API: ' + method + ' ' + path }, 404);
  });
  return requests;
}
test('analysis to review retains evidence and enforces rejection notes', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/');
  await page.getByLabel('Business question', { exact: true }).fill(scenarios[0].question);
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect(
    page.getByRole('heading', { name: 'Protect margins with targeted offers first' }),
  ).toBeVisible();
  await expect(page.getByRole('link', { name: 'PDF report' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Data source', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('SELECT period');
  await page.getByRole('button', { name: 'Close details' }).click();
  await page.getByRole('button', { name: 'Request revision' }).click();
  await expect(page.getByRole('alert')).toContainText('Please describe the changes needed');
  expect(requests.filter((r) => r.path.endsWith('/review'))).toHaveLength(0);
  await page.getByRole('button', { name: 'Approve this version' }).click();
  await expect(page.getByRole('link', { name: 'PDF report' })).toBeVisible();
  expect(requests.find((r) => r.path.endsWith('/review'))?.body).toMatchObject({
    reviewer: user.name,
    decision: 'approved',
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: 'test-results/analysis-approved.png',
    fullPage: true,
    animations: 'disabled',
  });
});
test('local-only workspace disables cloud and chooses local model explicitly', async ({ page }) => {
  const requests = await mockWorkspace(page, { localOnly: true });
  await page.goto('/');
  await page.getByText('Model and analysis settings', { exact: false }).click();
  await expect(page.getByRole('option', { name: /Cloud model/ })).toHaveAttribute('disabled', '');
  await expect(page.getByRole('option', { name: /OpenAI Decisions/ })).toHaveAttribute(
    'disabled',
    '',
  );
  await page.getByRole('combobox', { name: 'Analysis model', exact: true }).selectOption('ollama');
  await page
    .getByLabel('Business question', { exact: true })
    .fill('Which action is supported by our sales evidence?');
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect
    .poll(() => requests.find((r) => r.path === '/analyses')?.body)
    .toMatchObject({
      agent_mode: 'ollama',
      decision_mode: 'llm',
      model: 'qwen3:8b',
      routing_mode: 'auto',
    });
});
test('changing dataset clears incompatible document selections', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/');
  await page.getByText('Select reference documents', { exact: false }).click();
  await page.getByRole('checkbox', { name: 'Competitor pricing report' }).check();
  await page.getByLabel('Analysis scope', { exact: true }).selectOption('data_b');
  await expect(page.getByRole('checkbox', { name: 'Competitor pricing report' })).toHaveCount(0);
  await page
    .getByLabel('Business question', { exact: true })
    .fill('Analyze customer retention trends for this period.');
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect
    .poll(() => requests.find((r) => r.path === '/analyses')?.body)
    .toMatchObject({ dataset_id: 'data_b', document_ids: [] });
});
test('upload follows the durable background task to completion', async ({ page }) => {
  await mockWorkspace(page);
  await page.goto('/#sources');
  await page.getByLabel('Choose files to upload').setInputFiles({
    name: 'research.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Market research evidence'),
  });
  await page.getByRole('button', { name: 'Upload and process' }).click();
  await expect(page.getByText('Background parsing completed.', { exact: true })).toBeVisible();
  await expect(page.getByText('research.txt', { exact: true })).toBeVisible();
});
test('login recovers authenticated workspace without exposing credentials', async ({ page }) => {
  const requests = await mockWorkspace(page, { authenticated: false });
  await page.goto('/');
  await page.getByLabel('Email', { exact: true }).fill('test@example.com');
  await page.getByLabel('Password', { exact: true }).fill('a-long-secure-password');
  await page.getByRole('button', { name: 'Sign in to workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Make your next move clearer.' })).toBeVisible();
  expect(requests.find((r) => r.path === '/auth/login')?.body).toEqual({
    email: 'test@example.com',
    password: 'a-long-secure-password',
  });
  await expect(page.locator('body')).not.toContainText('a-long-secure-password');
});
test('viewer can read history but cannot create analyses or approve', async ({ page }) => {
  await mockWorkspace(page, { role: 'viewer' });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start analysis' })).toBeDisabled();
  await page.getByRole('link', { name: 'Analysis and review' }).click();
  await page.getByRole('button', { name: /A competitor has cut prices/ }).click();
  await expect(
    page.getByText('Awaiting a reviewer or administrator.', { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve this version' })).toHaveCount(0);
});
test('independent evaluation uses mixed route contract and shows denominators', async ({
  page,
}) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#evaluation');
  await page.locator('.evaluation-result > summary').first().click();
  await expect(page.getByText('n = 2', { exact: true })).toBeVisible();
  await expect(page.locator('.evaluation-metrics')).toContainText('No samples yet');
  await page.getByText('Add a labeled case', { exact: false }).click();
  await page.getByLabel('Case name', { exact: true }).fill('New labeled case');
  await page
    .getByLabel('Business question', { exact: true })
    .fill('Compare price data and competition reports.');
  await page.getByRole('combobox', { name: 'Dataset', exact: true }).selectOption('data_a');
  await page
    .getByRole('combobox', { name: 'Expected tool route', exact: true })
    .selectOption('mixed');
  await page.getByRole('button', { name: 'Save case' }).click();
  await expect
    .poll(() => requests.find((r) => r.path === '/evaluation/cases')?.body)
    .toMatchObject({ expected_route: 'mixed', dataset_id: 'data_a', split: 'development' });
});
test('mobile navigation and studio fit the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockWorkspace(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Make your next move clearer.' })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('link', { name: 'Knowledge library' }).click();
  await expect(page.getByRole('heading', { name: 'Your knowledge library.' })).toBeVisible();
  await expect
    .poll(() => page.locator('.sidebar').evaluate((el) => el.getBoundingClientRect().right))
    .toBeLessThanOrEqual(0);
  await page.screenshot({
    path: 'test-results/mobile-knowledge.png',
    fullPage: true,
    animations: 'disabled',
  });
});

test('dataset dictionary saves explicit business definitions without server metadata', async ({
  page,
}) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#sources');
  await page.getByRole('button', { name: /Quarterly sales data/ }).click();
  await page.getByText('Business definitions and data dictionary', { exact: true }).click();
  await page.getByLabel('Dataset description', { exact: true }).fill('Monthly recognized sales');
  await page.getByLabel('Currency', { exact: true }).fill('SGD');
  await page
    .getByLabel('Business metrics', { exact: false })
    .fill('Net revenue = Revenue - Returns');
  await page.getByRole('button', { name: 'Save business definitions' }).click();
  await expect
    .poll(() => requests.find((r) => r.path.endsWith('/dictionary'))?.body)
    .toMatchObject({
      description: 'Monthly recognized sales',
      currency: 'SGD',
      metrics: { 'Net revenue': 'Revenue - Returns' },
    });
  expect(requests.find((r) => r.path.endsWith('/dictionary'))?.body).not.toHaveProperty(
    'dataset_id',
  );
});

test('first administrator setup validates password and authenticates', async ({ page }) => {
  const requests = await mockWorkspace(page, { authenticated: false, setup: true });
  await page.goto('/');
  await page.getByLabel('Name', { exact: true }).fill('Team administrator');
  await page.getByLabel('Email', { exact: true }).fill('admin@example.com');
  await page.getByLabel('Password', { exact: true }).fill('secure-password-for-test');
  await page.getByLabel('Setup token', { exact: false }).fill('test-setup-token');
  await page.getByRole('button', { name: 'Create workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Make your next move clearer.' })).toBeVisible();
  expect(requests.find((r) => r.path === '/auth/setup')?.body).toMatchObject({
    name: 'Team administrator',
    setup_token: 'test-setup-token',
  });
});

test('admin changes member access and resets password without showing it', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#settings');
  await page.getByRole('combobox', { name: 'Analyst colleague role' }).selectOption('reviewer');
  await expect
    .poll(() => requests.find((r) => r.path === '/auth/users/user_2')?.body)
    .toEqual({ role: 'reviewer' });
  const row = page.getByRole('row').filter({ hasText: 'colleague@example.com' });
  await row.getByRole('button', { name: 'Disable account' }).click();
  await expect(row.getByText('Disabled')).toBeVisible();
  await row.getByRole('button', { name: 'Reset password' }).click();
  await page.getByLabel('New password', { exact: true }).fill('new-strong-password-123');
  await page.getByRole('button', { name: 'Save new password' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(requests.filter((r) => r.path === '/auth/users/user_2').at(-1)?.body).toEqual({
    password: 'new-strong-password-123',
  });
  await expect(page.locator('body')).not.toContainText('new-strong-password-123');
});

test('recurring analysis sends valid schedule and supports pausing', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#settings');
  const form = page
    .locator('form')
    .filter({ has: page.getByRole('heading', { name: 'Create scheduled analysis' }) });
  await form.getByLabel('Task name', { exact: true }).fill('Daily revenue check');
  await form
    .getByLabel('Business question', { exact: true })
    .fill('Compare daily revenue with the previous period.');
  await form.getByRole('combobox', { name: 'Dataset', exact: true }).selectOption('data_a');
  await form.getByRole('button', { name: 'Create scheduled task' }).click();
  await expect
    .poll(() => requests.find((r) => r.path === '/schedules')?.body)
    .toMatchObject({
      interval_minutes: 1440,
      enabled: true,
      request: { dataset_id: 'data_a', agent_mode: 'local', decision_mode: 'baseline' },
    });
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Enable', exact: true })).toBeVisible();
  expect(requests.find((r) => r.path === '/schedules/schedule_1')?.body).toEqual({
    enabled: false,
  });
});

test('RSS connector saves its source and tracks synchronization', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#sources');
  const form = page
    .locator('form')
    .filter({ has: page.getByRole('heading', { name: 'Add data connection' }) });
  await form.getByLabel('Name', { exact: true }).fill('Industry news');
  await form.getByRole('combobox', { name: 'Source type', exact: true }).selectOption('rss');
  await form.getByLabel('Source URL', { exact: true }).fill('https://example.com/feed.xml');
  await form.getByRole('button', { name: 'Save connection' }).click();
  await page.getByRole('button', { name: 'Sync', exact: true }).click();
  await expect(page.getByText('Background processing completed', { exact: true })).toBeVisible();
  expect(requests.find((r) => r.path === '/connectors')?.body).toMatchObject({
    kind: 'rss',
    url: 'https://example.com/feed.xml',
  });
});

test('evaluation can explicitly use a local faithfulness judge', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#evaluation');
  await page
    .getByRole('combobox', { name: 'Answer faithfulness evaluation', exact: true })
    .selectOption('ollama');
  await page.getByRole('combobox', { name: 'Local model', exact: true }).selectOption('qwen3:8b');
  await page.getByRole('button', { name: 'Run evaluation', exact: true }).click();
  await expect(page.getByText('Background processing completed', { exact: true })).toBeVisible();
  expect(requests.find((r) => r.path === '/evaluation/run')?.body).toMatchObject({
    agent_mode: 'local',
    decision_mode: 'baseline',
    split: 'holdout',
    judge_mode: 'ollama',
    judge_model: 'qwen3:8b',
  });
});

test('logout remains reachable in a short desktop viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await mockWorkspace(page);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeInViewport();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in to workspace' })).toBeVisible();
});

test('OpenAI Decisions sends custom actions and shows model probabilities with clear limits', async ({
  page,
}) => {
  const requests = await mockWorkspace(page, { decisionProvider: 'openai_decisions' });
  await page.goto('/');
  await page.getByText('Model and analysis settings', { exact: false }).click();
  const decision = page.getByRole('combobox', { name: 'Decision method', exact: true });
  await decision.selectOption('openai_decisions');
  await page.getByRole('combobox', { name: 'Analysis model', exact: true }).selectOption('ollama');
  await expect(decision).toHaveValue('openai_decisions');
  await page
    .getByLabel('Custom candidate actions (optional)', { exact: true })
    .fill('Pilot | Cap spending\nWait | Collect more evidence');
  await page.getByLabel('Business question', { exact: true }).fill(scenarios[0].question);
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect(page.getByText('Model choice probability', { exact: true })).toBeVisible();
  await expect(page.locator('.recommendation footer')).toContainText('OpenAI Decisions');
  await expect(page.locator('.recommendation footer')).toContainText('Model confidence 76.0%');
  await expect(page.locator('.ranking')).toContainText('82.0%');
  await expect(page.locator('.ranking')).toContainText('not the chance of business success');
  const submitted = requests.find((request) => request.path === '/analyses')?.body;
  expect(submitted).toMatchObject({
    agent_mode: 'ollama',
    decision_mode: 'openai_decisions',
    candidate_actions: [
      { id: 'action_1', title: 'Pilot', description: 'Cap spending' },
      { id: 'action_2', title: 'Wait', description: 'Collect more evidence' },
    ],
  });
  await expect(page.getByLabel('Custom candidate actions (optional)', { exact: true })).toHaveValue(
    'Pilot | Cap spending\nWait | Collect more evidence',
  );
  await page.getByRole('button', { name: 'Dismiss notification' }).click();
  await page.screenshot({
    path: 'test-results/openai-decisions-desktop.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.locator('.sidebar').evaluate((element) => element.getBoundingClientRect().right),
    )
    .toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({
    path: 'test-results/openai-decisions-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
});

test('OpenAI Decisions availability follows the OpenAI capability and local-only policy', async ({
  page,
}) => {
  await mockWorkspace(page, { decisionsAvailable: false });
  await page.goto('/');
  await page.getByText('Model and analysis settings', { exact: false }).click();
  await expect(page.getByRole('option', { name: /OpenAI Decisions/ })).toHaveAttribute(
    'disabled',
    '',
  );
  await page.getByRole('link', { name: 'Workspace', exact: true }).click();
  const service = page
    .locator('.capability')
    .filter({ has: page.getByText('OpenAI Decisions', { exact: true }) });
  await expect(service).toContainText('OPENAI_API_KEY');
  await expect(service).toContainText('Disabled');
  await expect(page.getByText(/TYPESAFE_API_KEY/)).toHaveCount(0);
});

test('historical JEV results retain their provider and reruns use OpenAI Decisions', async ({
  page,
}) => {
  const requests = await mockWorkspace(page, { decisionProvider: 'jev' });
  await page.goto('/#analysis?id=run_1');
  await expect(page.locator('.recommendation footer')).toContainText('Legacy TypeSafe JEV');
  await expect(page.locator('.ranking')).toContainText('82.0%');
  await page.getByText('Model and analysis settings', { exact: false }).click();
  await expect(page.getByRole('combobox', { name: 'Decision method', exact: true })).toHaveValue(
    'openai_decisions',
  );
  await expect(page.getByRole('option', { name: /JEV/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect
    .poll(() => requests.find((request) => request.path === '/analyses')?.body)
    .toMatchObject({ decision_mode: 'openai_decisions' });
});

for (const profile of ['fast', 'quality', 'auto'] as const) {
  test(`Local ${profile} submits a server-managed profile and shows actual routing`, async ({
    page,
  }) => {
    const requests = await mockWorkspace(page);
    await page.goto('/');
    await page.getByText('Model and analysis settings', { exact: false }).click();
    await page
      .getByRole('combobox', { name: 'Decision method', exact: true })
      .selectOption('openai_decisions');
    await page
      .getByRole('combobox', { name: 'Analysis model', exact: true })
      .selectOption('ollama');
    await page.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption(profile);
    await expect(page.getByRole('combobox', { name: 'Decision method', exact: true })).toHaveValue(
      'llm',
    );
    await expect(
      page.getByRole('combobox', { name: 'Decision method', exact: true }),
    ).toBeDisabled();
    await expect(page.getByRole('combobox', { name: 'Local model', exact: true })).toHaveCount(0);
    await expect(page.locator('.local-profile-card')).toContainText('bge-m3');
    await page.getByLabel('Business question', { exact: true }).fill(scenarios[0].question);
    await page.getByRole('button', { name: 'Start analysis' }).click();
    const routing = page.getByRole('region', { name: 'Local model routing' });
    await expect(routing).toContainText(localProfiles[profile].label);
    await expect(routing).toContainText(profile === 'fast' ? fastModel : qualityModel);
    if (profile === 'auto')
      await expect(routing).toContainText('Combined SQL and document evidence');
    const request = requests.find((r) => r.path === '/analyses')?.body;
    expect(request).toMatchObject({
      agent_mode: 'ollama',
      decision_mode: 'llm',
      local_profile: profile,
    });
    expect(request).not.toHaveProperty('model');
    expect(request).not.toHaveProperty('local_profile_snapshot');
    await page.reload();
    await page.getByText('Model and analysis settings', { exact: false }).click();
    await expect(page.getByRole('combobox', { name: 'Local run mode', exact: true })).toHaveValue(
      profile,
    );
    await page.getByRole('button', { name: 'Start analysis' }).click();
    await expect.poll(() => requests.filter((r) => r.path === '/analyses').length).toBe(2);
    expect(requests.filter((r) => r.path === '/analyses').at(-1)?.body).toMatchObject({
      local_profile: profile,
    });
    if (profile === 'auto') {
      await page.getByRole('button', { name: 'Dismiss notification' }).click();
      await page.screenshot({
        path: 'test-results/local-auto-desktop.png',
        fullPage: true,
        animations: 'disabled',
      });
      await page.setViewportSize({ width: 390, height: 844 });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await page.screenshot({
        path: 'test-results/local-auto-mobile.png',
        fullPage: true,
        animations: 'disabled',
      });
    }
  });
}

test('missing model blocks Quality and Auto with installation guidance but permits Fast', async ({
  page,
}) => {
  const requests = await mockWorkspace(page, { installedModels: [fastModel] });
  await page.goto('/');
  await page.getByText('Model and analysis settings', { exact: false }).click();
  await page.getByRole('combobox', { name: 'Analysis model', exact: true }).selectOption('ollama');
  await page.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption('auto');
  await expect(page.getByRole('button', { name: 'Start analysis' })).toBeDisabled();
  await expect(page.locator('.local-profile-settings')).toContainText(
    'Download the required model',
  );
  await expect(page.locator('.local-profile-settings')).toContainText(qualityModel);
  await page.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption('quality');
  await expect(page.getByRole('button', { name: 'Start analysis' })).toBeDisabled();
  expect(requests.filter((r) => r.path === '/analyses')).toHaveLength(0);
  await page.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption('fast');
  await expect(page.getByRole('button', { name: 'Start analysis' })).toBeEnabled();
});

test('server local-retrieval policy blocks named modes and leaves Custom local available', async ({
  page,
}) => {
  await mockWorkspace(page, { profileBlocked: true });
  await page.goto('/');
  await page.getByText('Model and analysis settings', { exact: false }).click();
  await page.getByRole('combobox', { name: 'Analysis model', exact: true }).selectOption('ollama');
  await page.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption('fast');
  await expect(page.locator('.local-profile-settings')).toContainText('Configure local embeddings');
  await expect(page.getByRole('button', { name: 'Start analysis' })).toBeDisabled();
  await page.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption('custom');
  await expect(page.getByRole('button', { name: 'Start analysis' })).toBeEnabled();
  await expect(page.getByRole('combobox', { name: 'Decision method', exact: true })).toBeEnabled();
});

test('historical profile reruns use current configuration without resending its model snapshot', async ({
  page,
}) => {
  const requests = await mockWorkspace(page, { initialProfile: 'quality' });
  await page.goto('/#analysis?id=run_1');
  await page.getByText('Model and analysis settings', { exact: false }).click();
  await expect(page.getByRole('combobox', { name: 'Local run mode', exact: true })).toHaveValue(
    'quality',
  );
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect.poll(() => requests.filter((r) => r.path === '/analyses').length).toBe(1);
  const submitted = requests.find((r) => r.path === '/analyses')?.body;
  expect(submitted).toMatchObject({ local_profile: 'quality' });
  expect(submitted).not.toHaveProperty('model');
  expect(submitted).not.toHaveProperty('local_profile_snapshot');
});

test('named local evaluation resets cloud judging and submits the profile', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#evaluation');
  await page
    .getByRole('combobox', { name: 'Answer faithfulness evaluation', exact: true })
    .selectOption('live');
  await page.getByRole('combobox', { name: 'Analysis model', exact: true }).selectOption('ollama');
  await page.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption('quality');
  await expect(
    page.getByRole('combobox', { name: 'Answer faithfulness evaluation', exact: true }),
  ).toHaveValue('none');
  await expect(
    page.getByRole('option', { name: 'Cloud model-assisted evaluation', exact: true }),
  ).toHaveAttribute('disabled', '');
  await page.getByRole('button', { name: 'Run evaluation', exact: true }).click();
  await expect
    .poll(() => requests.find((r) => r.path === '/evaluation/run')?.body)
    .toMatchObject({
      agent_mode: 'ollama',
      decision_mode: 'llm',
      local_profile: 'quality',
      judge_mode: 'none',
    });
  expect(requests.find((r) => r.path === '/evaluation/run')?.body).not.toHaveProperty('model');
});

test('scheduled local analysis retains its named mode', async ({ page }) => {
  const requests = await mockWorkspace(page);
  await page.goto('/#settings');
  const form = page
    .locator('form')
    .filter({ has: page.getByRole('heading', { name: 'Create scheduled analysis' }) });
  await form.getByLabel('Task name', { exact: true }).fill('Daily local report');
  await form.getByLabel('Business question', { exact: true }).fill(scenarios[0].question);
  await form.getByRole('combobox', { name: 'Dataset', exact: true }).selectOption('data_a');
  await form.getByRole('combobox', { name: 'Analysis model', exact: true }).selectOption('ollama');
  await form.getByRole('combobox', { name: 'Local run mode', exact: true }).selectOption('auto');
  await form.getByRole('button', { name: 'Create scheduled task' }).click();
  await expect(page.locator('.schedule-row')).toContainText('Local Auto');
  const submitted = requests.find((r) => r.path === '/schedules')?.body as {
    request: Record<string, unknown>;
  };
  expect(submitted.request).toMatchObject({
    local_profile: 'auto',
    agent_mode: 'ollama',
    decision_mode: 'llm',
  });
  expect(submitted.request).not.toHaveProperty('model');
});

test('a requested revision keeps the local profile and links the rejected report', async ({
  page,
}) => {
  const requests = await mockWorkspace(page, { initialProfile: 'auto' });
  await page.goto('/#analysis?id=run_1');
  await page
    .getByLabel('Review notes', { exact: true })
    .fill('Check the current policy before recommending a price change.');
  await page.getByRole('button', { name: 'Request revision', exact: true }).click();
  await page.getByRole('button', { name: 'Revise and analyze again' }).click();
  await expect(page.getByText('Creating a revised version.', { exact: false })).toBeVisible();
  await page.getByText('Model and analysis settings', { exact: false }).click();
  await expect(page.getByRole('combobox', { name: 'Local run mode', exact: true })).toHaveValue(
    'auto',
  );
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect
    .poll(() => requests.find((r) => r.path === '/analyses')?.body)
    .toMatchObject({
      parent_run_id: 'run_1',
      local_profile: 'auto',
      agent_mode: 'ollama',
      decision_mode: 'llm',
    });
});
