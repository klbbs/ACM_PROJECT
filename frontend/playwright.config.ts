import { defineConfig } from '@playwright/test'
const testPort = Number(process.env.ACM_EDITOR_TEST_PORT || 18766)
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: `http://127.0.0.1:${testPort}`,
    browserName: 'chromium',
    channel: 'chromium',
    viewport: { width: 1600, height: 1100 },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `${process.env.ACM_TEST_PYTHON || 'python3'} ../scripts/editor_test_server.py`,
    url: `http://127.0.0.1:${testPort}/api/health`,
    reuseExistingServer: false,
    env: { ACM_EDITOR_TEST_SERVER: '1', ACM_EDITOR_TEST_PORT: String(testPort) },
    timeout: 30000,
  },
})
