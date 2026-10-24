import { test, expect } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { copyFile, readFile, writeFile } from 'node:fs/promises';

// Opt-in only: run against a disposable local deployment, never a production URL.
test('real deployment: setup/login, analysis, review, exports, upload and session recovery', async ({
  page,
  baseURL,
}) => {
  const localProfile = process.env.E2E_LOCAL_PROFILE;
  if (localProfile && !['fast', 'quality', 'auto'].includes(localProfile))
    throw new Error('Unknown local QA profile.');
  test.setTimeout(localProfile ? 480_000 : 180_000);
  page.setDefaultTimeout(15_000);
  if (!baseURL || !['127.0.0.1', 'localhost'].includes(new URL(baseURL).hostname))
    throw new Error('Live QA requires a local disposable deployment.');
  const credentialPath = process.env.E2E_CREDENTIAL_PATH || '/private/tmp/market-lens-qa.json';
  let credentials: { email: string; password: string; run_id?: string };
  try {
    credentials = JSON.parse(await readFile(credentialPath, 'utf8'));
  } catch {
    credentials = {
      email: 'qa@market-lens.invalid',
      password: randomBytes(24).toString('base64url'),
    };
    await writeFile(credentialPath, JSON.stringify(credentials), { mode: 0o600 });
  }
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  const status = await (await page.request.get('/api/auth/status')).json();
  if (status.setup_required) {
    const environment = await readFile(
      process.env.E2E_ENV_FILE || '/private/tmp/market-lens-refactor.env',
      'utf8',
    );
    const token =
      environment
        .split('\n')
        .find((line) => line.startsWith('SETUP_TOKEN='))
        ?.slice('SETUP_TOKEN='.length)
        .trim()
        .replace(/^['"]|['"]$/g, '') || '';
    await page.getByLabel('Name', { exact: true }).fill('QA Workspace Admin');
    await page.getByLabel('Email', { exact: true }).fill(credentials.email);
    await page.getByLabel('Password', { exact: true }).fill(credentials.password);
    await page
      .getByLabel('Setup token', { exact: false })
      .fill(token)
      .catch(() => {
        throw new Error('Unable to enter deployment setup token');
      });
    await page.getByRole('button', { name: 'Create workspace' }).click();
  } else if (!status.user) {
    await page.getByLabel('Email', { exact: true }).fill(credentials.email);
    await page.getByLabel('Password', { exact: true }).fill(credentials.password);
    await page.getByRole('button', { name: 'Sign in to workspace' }).click();
  }
  await expect(page.getByRole('heading', { name: 'Make your next move clearer.' })).toBeVisible();
  const config = await (await page.request.get('/api/config')).json();
  const scenario = config.scenarios[0];
  const useDecisions = process.env.E2E_DECISION_MODE === 'openai_decisions';
  if (useDecisions && localProfile) throw new Error('Local profiles cannot use cloud decisions.');
  if (localProfile) {
    const title = 'QA support policy ' + Date.now();
    const created = await page.request.post('/api/documents', {
      data: {
        title,
        content:
          'Customer support response policy: the first response to every new support ticket is due within one business day. The support lead reviews the open queue every morning and assigns an owner to each unanswered ticket.',
        source: 'Synthetic local profile browser acceptance fixture',
      },
    });
    expect(created.ok()).toBeTruthy();
    await page.reload();
    await page.getByRole('combobox', { name: 'Analysis scope', exact: true }).selectOption('');
    await page.getByText('Select reference documents', { exact: false }).click();
    await page.getByRole('checkbox', { name: title }).check();
    await page
      .getByLabel('Business question', { exact: true })
      .fill(
        'According to the customer support policy, when is the first response due and what daily action should the support lead take?',
      );
    await page.getByText('Model and analysis settings', { exact: false }).click();
    await page
      .getByRole('combobox', { name: 'Analysis model', exact: true })
      .selectOption('ollama');
    await page
      .getByRole('combobox', { name: 'Local run mode', exact: true })
      .selectOption(localProfile);
    await expect(page.getByRole('combobox', { name: 'Decision method', exact: true })).toHaveValue(
      'llm',
    );
    if (
      (config.retrieval_mode || config.retrieval) === 'lexical' ||
      config.embedding_provider === 'lexical'
    ) {
      await expect(page.locator('.local-profile-card')).toContainText(
        'Keyword retrieval · No embedding model',
      );
    }
  } else {
    await page
      .getByRole('combobox', { name: 'Analysis scope', exact: true })
      .selectOption(scenario.dataset_id);
    await page.getByLabel('Business question', { exact: true }).fill(scenario.question);
    if (useDecisions) {
      expect(config.openai_decisions_available).toBe(true);
      await page.getByText('Model and analysis settings', { exact: false }).click();
      await page
        .getByRole('combobox', { name: 'Decision method', exact: true })
        .selectOption('openai_decisions');
    }
  }
  await page.getByRole('button', { name: 'Start analysis' }).click();
  await expect(page.getByRole('button', { name: 'Approve this version' })).toBeVisible({
    timeout: localProfile ? 360_000 : 90_000,
  });
  credentials.run_id =
    new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('id') || undefined;
  expect(credentials.run_id).toBeTruthy();
  await writeFile(credentialPath, JSON.stringify(credentials), { mode: 0o600 });
  if (localProfile) {
    const response = await page.request.get(`/api/analyses/${credentials.run_id}`);
    expect(response.ok()).toBeTruthy();
    const run = await response.json();
    expect(run.result.local_routing.profile).toBe(localProfile);
    expect(run.result.decision.provider).toBe('ollama');
    const selectedAction = run.result.decision.actions[run.result.decision.choice];
    expect(selectedAction.title.length).toBeGreaterThan(0);
    expect(selectedAction.title.length).toBeLessThan(100);
    expect(
      run.result.measurement.provider_usage.every(
        (usage: { provider: string }) => usage.provider === 'ollama',
      ),
    ).toBe(true);
    const analysisText = [
      run.result.rationale,
      ...run.result.agents.flatMap((agent: { claims: { text: string }[] }) =>
        agent.claims.map((claim) => claim.text),
      ),
    ].join(' ');
    expect(analysisText).toMatch(/(?:one|1) business day/i);
    expect(analysisText).toMatch(/owner/i);
    expect(run.result.rationale_evidence_ids.length).toBeGreaterThan(0);
    await expect(page.getByRole('region', { name: 'Local model routing' })).toContainText(
      run.result.local_routing.analysis_model,
    );
  }
  if (useDecisions) {
    const runResponse = await page.request.get(`/api/analyses/${credentials.run_id}`);
    expect(runResponse.ok()).toBeTruthy();
    const run = await runResponse.json();
    const decision = run.result.decision;
    expect(decision.provider).toBe('openai_decisions');
    expect(decision.actions).toHaveProperty(decision.choice);
    const probabilities = Object.values(decision.probabilities) as number[];
    expect(probabilities.length).toBeGreaterThan(1);
    expect(probabilities.every((value) => Number.isFinite(value) && value >= 0 && value <= 1)).toBe(
      true,
    );
    expect(probabilities.reduce((sum, value) => sum + value, 0)).toBeCloseTo(1, 2);
    expect(decision.confidence).toBeGreaterThanOrEqual(0);
    expect(decision.confidence).toBeLessThanOrEqual(1);
    await expect(page.locator('.recommendation footer')).toContainText('OpenAI Decisions');
    await expect(page.locator('.recommendation footer')).toContainText('Model confidence');
    await expect(page.locator('.ranking')).toContainText('not the chance of business success');
  }
  await page.getByRole('tab', { name: /Source evidence/ }).click();
  await expect(page.locator('.evidence-list .evidence').first()).toBeVisible();
  await page.locator('.evidence-list .evidence > summary').first().click();
  await expect(page.locator('.evidence-body').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Execution history' }).click();
  await expect(page.getByRole('heading', { name: 'Execution trace' })).toBeVisible();
  await page
    .getByLabel('Review notes', { exact: true })
    .fill('QA checked evidence, query results and provenance.');
  await page.getByRole('button', { name: 'Approve this version' }).click();
  await expect(page.getByRole('link', { name: 'PDF report' })).toBeVisible();
  for (const [format, type] of [
    ['pdf', 'application/pdf'],
    ['html', 'text/html'],
  ] as const) {
    const response = await page.request.get(
      `/api/analyses/${credentials.run_id}/report?format=${format}`,
    );
    expect(response.ok()).toBeTruthy();
    expect(response.headers()['content-type']).toContain(type);
    const data = await response.body();
    expect(data.length).toBeGreaterThan(100);
    if (format === 'pdf') expect(data.subarray(0, 5).toString()).toBe('%PDF-');
  }
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('link', { name: 'JSON', exact: true }).click();
  const download = await downloadPromise;
  expect(await download.failure()).toBeNull();
  await page.getByRole('tab', { name: 'Decision overview' }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: 'test-results-live/live-analysis-approved.png',
    fullPage: true,
    animations: 'disabled',
  });
  const desktopViewport = page.viewportSize();
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
    path: 'test-results-live/live-analysis-approved-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  if (localProfile) {
    await copyFile(
      'test-results-live/live-analysis-approved.png',
      `/private/tmp/market-lens-local-${localProfile}-desktop.png`,
    );
    await copyFile(
      'test-results-live/live-analysis-approved-mobile.png',
      `/private/tmp/market-lens-local-${localProfile}-mobile.png`,
    );
  }
  if (desktopViewport) await page.setViewportSize(desktopViewport);
  await page.reload();
  await expect(page.getByRole('link', { name: 'PDF report' })).toBeVisible();
  await page.getByRole('link', { name: /Knowledge library/ }).click();
  const filename = 'qa-research-' + Date.now() + '.txt';
  await page.getByLabel('Choose files to upload').setInputFiles({
    name: filename,
    mimeType: 'text/plain',
    buffer: Buffer.from(
      'QA source: customer interviews suggest testing targeted retention offers before broad discounts. Evidence date: 2026-10-24. Test source: ' +
        filename,
    ),
  });
  await page.getByRole('button', { name: 'Upload and process' }).click();
  await expect(page.getByText('Background parsing completed.', { exact: true })).toBeVisible({
    timeout: 45_000,
  });
  await page.reload();
  await page.getByLabel('Search sources').fill(filename.replace('.txt', ''));
  await expect(page.locator('.source-library .source-row')).toHaveCount(1);
  await page.locator('.source-library .source-row').click();
  await expect(page.getByRole('dialog')).toContainText('customer interviews');
  await page.getByRole('button', { name: 'Close details' }).click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in to workspace' })).toBeVisible();
  await page.getByLabel('Email', { exact: true }).fill(credentials.email);
  await page.getByLabel('Password', { exact: true }).fill(credentials.password);
  await page.getByRole('button', { name: 'Sign in to workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Your knowledge library.' })).toBeVisible();
  await page.goto('/#analysis?id=' + credentials.run_id);
  await expect(page.getByRole('link', { name: 'PDF report' })).toBeVisible();
  expect(errors).toEqual([]);
});
