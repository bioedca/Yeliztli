/** Tests for the Database Health panel (Settings → System Health) and the
 * db-health API hooks.
 *
 * Covers state badges per DB, the "needs attention" banner, the Resume /
 * Verify / Clean recovery actions (and the endpoints they POST to), and the
 * integrity / last-error detail text. Mirrors the fetch-mock + QueryClient
 * wrapper conventions from settings-update-manager.test.tsx.
 */

import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import DatabaseHealthPanel from '@/components/settings/DatabaseHealthPanel'
import type { DatabaseHealth } from '@/api/db-health'

// ── Mocks ────────────────────────────────────────────────────────────

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ── Fixtures ─────────────────────────────────────────────────────────

/** Full DatabaseHealth row with sensible defaults; override per test. */
function makeDb(overrides: Partial<DatabaseHealth> = {}): DatabaseHealth {
  return {
    name: 'clinvar',
    display_name: 'ClinVar',
    build_mode: 'download',
    required: true,
    state: 'ready',
    present: true,
    version: '20260315',
    downloaded_at: '2026-03-15T00:00:00',
    file_size_bytes: 250_000_000,
    expected_size_bytes: 250_000_000,
    integrity_ok: true,
    integrity_detail: null,
    resumable: false,
    download_id: null,
    downloaded_bytes: null,
    total_bytes: null,
    progress_pct: null,
    active_job_id: null,
    last_error: null,
    can_clean: false,
    can_resume: false,
    can_verify: true,
    ...overrides,
  }
}

/** Mock the GET /api/databases/health endpoint with the given rows. */
function setupHealth(databases: DatabaseHealth[]) {
  mockFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (typeof url === 'string') {
      if (url.includes('/api/databases/health') && (!init || init.method == null)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ databases }),
        })
      }
      if (url.includes('/api/databases/resume') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          status: 202,
          json: async () => ({
            session_id: 'sess-resume-1',
            downloads: [{ db_name: 'gnomad', job_id: 'job-1' }],
          }),
        })
      }
      const verifyMatch = url.match(/\/api\/databases\/([^/]+)\/verify$/)
      if (verifyMatch && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            db_name: verifyMatch[1],
            ok: true,
            detail: 'quick_check ok',
            depth: 'deep',
          }),
        })
      }
      const cleanMatch = url.match(/\/api\/databases\/([^/]+)\/clean$/)
      if (cleanMatch && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            db_name: cleanMatch[1],
            removed: [`${cleanMatch[1]}.db.tmp`],
          }),
        })
      }
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  })
}

// ── Wrapper ──────────────────────────────────────────────────────────

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function renderPanel() {
  return render(<DatabaseHealthPanel />, { wrapper: createWrapper() })
}

/** All seven distinct states, one DB each. */
function allStatesPayload(): DatabaseHealth[] {
  return [
    makeDb({ name: 'clinvar', display_name: 'ClinVar', state: 'ready', can_verify: true }),
    makeDb({
      name: 'gnomad',
      display_name: 'gnomAD',
      state: 'partial',
      version: null,
      integrity_ok: false,
      integrity_detail: 'partial download (interrupted)',
      resumable: true,
      download_id: 7,
      downloaded_bytes: 50_000_000,
      total_bytes: 100_000_000,
      progress_pct: 50,
      can_resume: true,
      can_clean: true,
      can_verify: false,
    }),
    makeDb({
      name: 'dbnsfp',
      display_name: 'dbNSFP',
      state: 'corrupt',
      integrity_ok: false,
      integrity_detail: 'dbnsfp_scores table is empty',
      can_clean: true,
      can_verify: true,
    }),
    makeDb({
      name: 'cpic',
      display_name: 'CPIC',
      state: 'failed',
      version: null,
      integrity_ok: null,
      last_error: 'HTTP 503 from upstream',
      can_clean: true,
      can_verify: false,
    }),
    makeDb({
      name: 'gwas_catalog',
      display_name: 'GWAS Catalog',
      state: 'not_installed',
      present: false,
      version: null,
      file_size_bytes: null,
      integrity_ok: null,
      can_verify: false,
    }),
    makeDb({
      name: 'dbsnp',
      display_name: 'dbSNP',
      state: 'downloading',
      version: null,
      integrity_ok: null,
      active_job_id: 'job-dl',
      can_verify: false,
    }),
    makeDb({
      name: 'vep_bundle',
      display_name: 'VEP bundle',
      state: 'building',
      version: null,
      integrity_ok: null,
      active_job_id: 'job-build',
      can_verify: false,
    }),
  ]
}

// ── Tests: rendering + state badges ──────────────────────────────────

describe('DatabaseHealthPanel — rendering', () => {
  it('renders a row per database with the correct state badge text', async () => {
    setupHealth(allStatesPayload())
    renderPanel()

    // Wait for data to load (heading is static, so wait on a row).
    expect(await screen.findByText('ClinVar')).toBeInTheDocument()

    // One display name per database.
    expect(screen.getByText('gnomAD')).toBeInTheDocument()
    expect(screen.getByText('dbNSFP')).toBeInTheDocument()
    expect(screen.getByText('CPIC')).toBeInTheDocument()
    expect(screen.getByText('GWAS Catalog')).toBeInTheDocument()
    expect(screen.getByText('dbSNP')).toBeInTheDocument()
    expect(screen.getByText('VEP bundle')).toBeInTheDocument()

    // State badges (one each, exact label text). These labels are unique to
    // the state column...
    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(screen.getByText('Partial')).toBeInTheDocument()
    expect(screen.getByText('Corrupt')).toBeInTheDocument()
    expect(screen.getByText('Not installed')).toBeInTheDocument()
    expect(screen.getByText('Downloading')).toBeInTheDocument()
    expect(screen.getByText('Building')).toBeInTheDocument()

    // ...except "Failed", which the integrity column also renders for any
    // integrity_ok === false row (partial + corrupt here). Scope to the
    // state badge of the failed DB (the CPIC row) instead.
    const cpicRow = screen.getByText('CPIC').closest('tr') as HTMLElement
    expect(cpicRow).not.toBeNull()
    expect(within(cpicRow).getByText('Failed')).toBeInTheDocument()
  })

  it('shows the empty state when no databases are registered', async () => {
    setupHealth([])
    renderPanel()
    expect(await screen.findByText('No databases registered.')).toBeInTheDocument()
  })

  it('renders the health table headers once data loads', async () => {
    setupHealth([makeDb()])
    renderPanel()
    expect(await screen.findByText('ClinVar')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Database' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Version' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Integrity' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Actions' })).toBeInTheDocument()
  })
})

// ── Tests: needs-attention banner ────────────────────────────────────

describe('DatabaseHealthPanel — needs-attention banner', () => {
  it('shows the banner when any DB is partial/corrupt/failed', async () => {
    setupHealth(allStatesPayload())
    renderPanel()
    // partial + corrupt + failed = 3 need attention.
    const banner = await screen.findByRole('status')
    expect(banner).toHaveTextContent('3 databases need')
    expect(banner).toHaveTextContent('attention')
  })

  it('uses the singular form for exactly one unhealthy DB', async () => {
    setupHealth([
      makeDb({ name: 'clinvar', display_name: 'ClinVar', state: 'ready' }),
      makeDb({
        name: 'gnomad',
        display_name: 'gnomAD',
        state: 'corrupt',
        integrity_ok: false,
        integrity_detail: 'gnomad_af table is empty',
        can_clean: true,
      }),
    ])
    renderPanel()
    const banner = await screen.findByRole('status')
    expect(banner).toHaveTextContent('1 database needs')
    expect(banner).not.toHaveTextContent('databases need')
  })

  it('hides the banner when every DB is healthy', async () => {
    setupHealth([
      makeDb({ name: 'clinvar', display_name: 'ClinVar', state: 'ready' }),
      makeDb({ name: 'gnomad', display_name: 'gnomAD', state: 'ready' }),
    ])
    renderPanel()
    expect(await screen.findByText('ClinVar')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

// ── Tests: integrity / last_error detail text ────────────────────────

describe('DatabaseHealthPanel — detail text', () => {
  it('renders integrity-failed detail text', async () => {
    setupHealth([
      makeDb({
        name: 'dbnsfp',
        display_name: 'dbNSFP',
        state: 'corrupt',
        integrity_ok: false,
        integrity_detail: 'dbnsfp_scores table is empty',
        can_clean: true,
      }),
    ])
    renderPanel()
    expect(await screen.findByText('dbnsfp_scores table is empty')).toBeInTheDocument()
  })

  it('renders last_error detail text for a failed DB', async () => {
    setupHealth([
      makeDb({
        name: 'cpic',
        display_name: 'CPIC',
        state: 'failed',
        integrity_ok: null,
        last_error: 'HTTP 503 from upstream',
        can_clean: true,
        can_verify: false,
      }),
    ])
    renderPanel()
    expect(await screen.findByText('HTTP 503 from upstream')).toBeInTheDocument()
  })
})

// ── Tests: Resume action ─────────────────────────────────────────────

describe('DatabaseHealthPanel — Resume', () => {
  it('shows a Resume button on a partial + resumable DB and POSTs on click', async () => {
    setupHealth([
      makeDb({
        name: 'gnomad',
        display_name: 'gnomAD',
        state: 'partial',
        version: null,
        integrity_ok: false,
        integrity_detail: 'partial download (interrupted)',
        resumable: true,
        download_id: 7,
        downloaded_bytes: 50_000_000,
        total_bytes: 100_000_000,
        progress_pct: 50,
        can_resume: true,
        can_clean: true,
        can_verify: false,
      }),
    ])
    renderPanel()

    const resumeBtn = await screen.findByRole('button', { name: /Resume/i })
    fireEvent.click(resumeBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/databases/resume',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ db_name: 'gnomad' }),
        }),
      )
    })
  })

  it('does not show a Resume button when the DB is not resumable', async () => {
    setupHealth([makeDb({ state: 'ready', can_resume: false })])
    renderPanel()
    expect(await screen.findByText('ClinVar')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Resume$/i })).not.toBeInTheDocument()
  })

  it('renders downloaded / total progress for a resumable partial', async () => {
    setupHealth([
      makeDb({
        name: 'gnomad',
        display_name: 'gnomAD',
        state: 'partial',
        resumable: true,
        downloaded_bytes: 50_000_000,
        total_bytes: 100_000_000,
        progress_pct: 50,
        can_resume: true,
        can_clean: true,
      }),
    ])
    renderPanel()
    // formatBytes(50_000_000) -> "47.7 MB", total -> "95.4 MB"
    expect(await screen.findByText(/47\.7 MB \/ 95\.4 MB/)).toBeInTheDocument()
    expect(screen.getByText('(50%)')).toBeInTheDocument()
  })

  it('renders live downloaded / total progress for an active download', async () => {
    setupHealth([
      makeDb({
        name: 'gnomad',
        display_name: 'gnomAD',
        state: 'downloading',
        version: null,
        file_size_bytes: null,
        integrity_ok: null,
        resumable: false,
        downloaded_bytes: 650_000_000,
        total_bytes: 1_300_000_000,
        progress_pct: 50,
        can_resume: false,
        can_clean: false,
        can_verify: false,
      }),
    ])
    renderPanel()

    expect(await screen.findByText(/619\.9 MB \/ 1\.21 GB/)).toBeInTheDocument()
    expect(screen.getByText('(50%)')).toBeInTheDocument()
  })
})

// ── Tests: Verify action ─────────────────────────────────────────────

describe('DatabaseHealthPanel — Verify', () => {
  it('shows a Verify action on a ready DB, POSTs verify, and shows the result', async () => {
    setupHealth([makeDb({ name: 'clinvar', display_name: 'ClinVar', state: 'ready', can_verify: true })])
    renderPanel()

    const verifyBtn = await screen.findByRole('button', { name: /Verify/i })
    fireEvent.click(verifyBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/databases/clinvar/verify',
        expect.objectContaining({ method: 'POST' }),
      )
    })

    // The mock returns ok:true -> component shows "Integrity OK".
    expect(await screen.findByText('Integrity OK')).toBeInTheDocument()
  })

  it('shows a failure result when verify reports not ok', async () => {
    mockFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string') {
        if (url.includes('/api/databases/health') && (!init || init.method == null)) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              databases: [makeDb({ name: 'clinvar', display_name: 'ClinVar', state: 'ready', can_verify: true })],
            }),
          })
        }
        if (/\/api\/databases\/clinvar\/verify$/.test(url) && init?.method === 'POST') {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              db_name: 'clinvar',
              ok: false,
              detail: 'malformed database disk image',
              depth: 'deep',
            }),
          })
        }
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
    })
    renderPanel()

    const verifyBtn = await screen.findByRole('button', { name: /Verify/i })
    fireEvent.click(verifyBtn)

    expect(await screen.findByText('Failed: malformed database disk image')).toBeInTheDocument()
  })
})

// ── Tests: Clean action ──────────────────────────────────────────────

describe('DatabaseHealthPanel — Clean', () => {
  it('shows a Clean action on a corrupt DB and POSTs clean after confirm', async () => {
    const confirmSpy = vi.fn().mockReturnValue(true)
    vi.stubGlobal('confirm', confirmSpy)

    setupHealth([
      makeDb({
        name: 'dbnsfp',
        display_name: 'dbNSFP',
        state: 'corrupt',
        integrity_ok: false,
        integrity_detail: 'dbnsfp_scores table is empty',
        can_clean: true,
      }),
    ])
    renderPanel()

    const cleanBtn = await screen.findByRole('button', { name: /Clean/i })
    fireEvent.click(cleanBtn)

    expect(confirmSpy).toHaveBeenCalledTimes(1)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/databases/dbnsfp/clean',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('offers Clean on a partial DB too', async () => {
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
    setupHealth([
      makeDb({
        name: 'gnomad',
        display_name: 'gnomAD',
        state: 'partial',
        resumable: true,
        can_resume: true,
        can_clean: true,
        can_verify: false,
      }),
    ])
    renderPanel()

    const cleanBtn = await screen.findByRole('button', { name: /Clean/i })
    fireEvent.click(cleanBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/databases/gnomad/clean',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('offers Clean on a failed DB', async () => {
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
    setupHealth([
      makeDb({
        name: 'cpic',
        display_name: 'CPIC',
        state: 'failed',
        integrity_ok: null,
        last_error: 'HTTP 503 from upstream',
        can_clean: true,
        can_verify: false,
      }),
    ])
    renderPanel()

    const cleanBtn = await screen.findByRole('button', { name: /Clean/i })
    fireEvent.click(cleanBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/databases/cpic/clean',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('does not POST clean when the confirm dialog is cancelled', async () => {
    const confirmSpy = vi.fn().mockReturnValue(false)
    vi.stubGlobal('confirm', confirmSpy)

    setupHealth([
      makeDb({
        name: 'dbnsfp',
        display_name: 'dbNSFP',
        state: 'corrupt',
        integrity_ok: false,
        integrity_detail: 'dbnsfp_scores table is empty',
        can_clean: true,
      }),
    ])
    renderPanel()

    const cleanBtn = await screen.findByRole('button', { name: /Clean/i })
    fireEvent.click(cleanBtn)

    expect(confirmSpy).toHaveBeenCalledTimes(1)

    // Give any (incorrectly) fired mutation a tick to land.
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    const cleanCalls = mockFetch.mock.calls.filter(
      ([url, init]) =>
        typeof url === 'string' &&
        /\/clean$/.test(url) &&
        (init as RequestInit | undefined)?.method === 'POST',
    )
    expect(cleanCalls).toHaveLength(0)
  })

  it('does not offer Clean on a healthy ready DB', async () => {
    setupHealth([makeDb({ state: 'ready', can_clean: false, can_verify: true })])
    renderPanel()
    expect(await screen.findByText('ClinVar')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Clean/i })).not.toBeInTheDocument()
  })
})

// ── Tests: error / loading surfaces ──────────────────────────────────

describe('DatabaseHealthPanel — fetch failure', () => {
  it('renders an error message when the health fetch fails', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/databases/health')) {
        return Promise.resolve({
          ok: false,
          status: 500,
          text: async () => 'boom',
          json: async () => ({}),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
    })
    renderPanel()
    expect(await screen.findByText(/Database health fetch failed: 500/)).toBeInTheDocument()
  })
})
