import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  outputDir: process.env.E2E_LIVE ? 'test-results-live' : 'test-results',
  testMatch: process.env.E2E_LIVE ? '**/live.spec.ts' : '**/workspace.spec.ts',
  fullyParallel: true,
  timeout: 30000,
  expect: { timeout: 6000 },
  retries: 0,
  reporter: [
    ['list'],
    [
      'html',
      {
        open: 'never',
        outputFolder: process.env.E2E_LIVE ? 'playwright-report-live' : 'playwright-report',
      },
    ],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:4173',
    trace: process.env.E2E_LIVE ? 'off' : 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...(process.env.PLAYWRIGHT_CHROME_CHANNEL ? { channel: 'chrome' } : {}),
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.E2E_LIVE
    ? undefined
    : {
        command: 'npm run dev -- --port 4173 --strictPort',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
      },
});
