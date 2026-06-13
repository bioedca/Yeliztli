/**
 * Playwright global setup — bypass setup wizard for E2E tests.
 *
 * Creates the disclaimer flag file so `_is_disclaimer_accepted()` returns true.
 * This runs before the web servers start, so we write files directly instead of
 * calling the API.
 *
 * NOTE: the dashboard gate is now health-based — `needs_setup` stays true until
 * every required, downloadable reference DB is integrity-`ready`, which an empty
 * dummy file no longer satisfies. Specs that drive the dashboard therefore stub
 * `/api/setup/status` via `bypassSetup(page)` (see `helpers.ts`) rather than rely
 * on an on-disk DB. The dummy `gnomad_af.db` below is kept only as a harmless
 * legacy fallback for any spec that does not call `bypassSetup`.
 *
 * The backend data_dir defaults to ~/.yeliztli (Path.home() / ".yeliztli").
 */

import * as fs from 'fs'
import * as path from 'path'

export default async function globalSetup() {
  const dataDir = process.env.YELIZTLI_DATA_DIR
    ?? path.join(process.env.HOME ?? '/tmp', '.yeliztli')
  fs.mkdirSync(dataDir, { recursive: true })

  // Create disclaimer flag so _is_disclaimer_accepted() returns true
  const disclaimerPath = path.join(dataDir, '.disclaimer_accepted')
  if (!fs.existsSync(disclaimerPath)) {
    fs.writeFileSync(
      disclaimerPath,
      JSON.stringify({ accepted_at: new Date().toISOString(), version: '1.0' }),
    )
  }

  // Create dummy gnomad_af.db so _has_any_databases() returns true
  const dummyDb = path.join(dataDir, 'gnomad_af.db')
  if (!fs.existsSync(dummyDb)) {
    fs.writeFileSync(dummyDb, '')
  }
}
