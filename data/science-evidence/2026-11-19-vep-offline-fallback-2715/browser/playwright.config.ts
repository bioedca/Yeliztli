import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const root = resolve(__dirname, '../../../..')
const packet = 'data/science-evidence/2026-11-19-vep-offline-fallback-2715'

export default defineConfig({
  testDir: __dirname,
  testMatch: 'vep-witness.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 60_000,
  outputDir: resolve(root, 'output/playwright/vep-fallback'),
  reporter: 'list',
  use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:42716' },
  webServer: [
    {
      command: `python ${packet}/browser/serve_probe.py`,
      cwd: root,
      url: 'http://127.0.0.1:42715/witness-health',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 42716 --strictPort',
      cwd: resolve(root, 'frontend'),
      env: { VITE_API_PORT: '42715' },
      url: 'http://127.0.0.1:42716',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
