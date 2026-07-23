const { defineConfig } = require('@playwright/test');

const pythonCommand = process.platform === 'win32'
  ? 'venv\\Scripts\\python.exe main.py'
  : '.venv/bin/python main.py';

module.exports = defineConfig({
  testDir: './tests/browser',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5000',
    browserName: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: process.env.PCP_TEST_SERVER_COMMAND || pythonCommand,
    url: 'http://127.0.0.1:5000/v2',
    timeout: 120_000,
    reuseExistingServer: true,
  },
});
