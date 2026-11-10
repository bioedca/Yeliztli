/** First-attempt native browser evidence for the synthetic #2672 cases. */
import { defineConfig } from '@playwright/test'
import path from 'node:path'
import base from './playwright.config'

const directory = process.env.YELIZTLI_BROWSER_DIAGNOSTICS
if (!directory || !process.env.YELIZTLI_DATA_DIR) {
  throw new Error('Set YELIZTLI_BROWSER_DIAGNOSTICS and a disposable YELIZTLI_DATA_DIR')
}
const diagnostics = path.resolve(directory)

export default defineConfig({
  ...base,
  globalSetup: undefined,
  testMatch: ['error-text-contrast.spec.ts', 'resource-not-found.spec.ts'],
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [['list'], ['json', { outputFile: path.join(diagnostics, 'browser-results.json') }]],
  outputDir: path.join(diagnostics, 'test-results'),
  use: { ...base.use, baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    {
      command: 'uvicorn backend.main:app --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: 'cd frontend && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
})
