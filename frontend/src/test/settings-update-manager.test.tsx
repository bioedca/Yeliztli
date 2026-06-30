/** Tests for Update Manager Settings sub-page (P4-18, T4-22v). */

import type { ReactNode } from 'react'
import { afterEach, describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Settings from '@/pages/Settings'
import UpdateManager, {
  isOutsideBandwidthWindow,
} from '@/components/settings/UpdateManager'

// ── Mocks ────────────────────────────────────────────────────────────

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ── Mock responses ───────────────────────────────────────────────────

function mockStatusResponse(statuses?: unknown[]) {
  return {
    ok: true,
    status: 200,
    json: async () =>
      statuses ?? [
        {
          db_name: 'clinvar',
          display_name: 'ClinVar',
          current_version: '20260315',
          version_display: 'Mar 2026',
          downloaded_at: '2026-03-15T00:00:00',
          auto_update: true,
          update_available: false,
        },
        {
          db_name: 'gnomad',
          display_name: 'gnomAD',
          current_version: '2.1.1',
          version_display: '2.1.1',
          downloaded_at: '2026-03-01T00:00:00',
          auto_update: false,
          update_available: false,
        },
        {
          db_name: 'dbnsfp',
          display_name: 'dbNSFP',
          current_version: null,
          version_display: null,
          downloaded_at: null,
          auto_update: false,
          update_available: false,
        },
      ],
  }
}

function mockCheckResponse(available?: unknown[]) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      available: available ?? [],
      up_to_date: ['clinvar', 'gnomad'],
      errors: [],
      checked_at: '2026-03-25T10:00:00Z',
    }),
  }
}

function mockHistoryResponse(entries?: unknown[]) {
  return {
    ok: true,
    status: 200,
    json: async () =>
      entries ?? [
        {
          id: 1,
          db_name: 'clinvar',
          previous_version: '20260301',
          new_version: '20260315',
          updated_at: '2026-03-15T02:00:00Z',
          variants_added: 150,
          variants_reclassified: 3,
          download_size_bytes: 5242880,
          duration_seconds: 45,
        },
      ],
  }
}

function mockPromptsResponse(prompts?: unknown[]) {
  return {
    ok: true,
    status: 200,
    json: async () => prompts ?? [],
  }
}

function setupFetchMocks(options: {
  statuses?: unknown[]
  available?: unknown[]
  history?: unknown[]
  prompts?: unknown[]
  triggerResponse?: unknown
  autoUpdateResponse?: unknown
  appUpdate?: unknown
  health?: { databases: unknown[] }
} = {}) {
  mockFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (typeof url === 'string') {
      if (url.includes('/api/updates/trigger') && init?.method === 'POST')
        return Promise.resolve({
          ok: true,
          status: 202,
          json: async () =>
            options.triggerResponse ?? {
              job_id: 'job-default',
              db_name: 'unknown',
              message: 'Update queued',
            },
        })
      if (url.includes('/api/updates/auto-update') && init?.method === 'POST')
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => options.autoUpdateResponse ?? {},
        })
      if (url.includes('/api/updates/app-update'))
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            options.appUpdate ?? {
              update_available: false,
              current_version: '0.1.0',
              latest_version: '0.1.0',
              release_url: null,
              release_notes: null,
              error: null,
            },
        })
      if (url.includes('/api/databases/health'))
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => options.health ?? { databases: [] },
        })
      if (url.includes('/api/updates/status'))
        return Promise.resolve(mockStatusResponse(options.statuses))
      if (url.includes('/api/updates/check'))
        return Promise.resolve(mockCheckResponse(options.available))
      if (url.includes('/api/updates/history'))
        return Promise.resolve(mockHistoryResponse(options.history))
      if (url.includes('/api/updates/prompts'))
        return Promise.resolve(mockPromptsResponse(options.prompts))
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  })
}

// ── Helpers ──────────────────────────────────────────────────────────

function createWrapper(initialEntries: string[] = ['/settings/updates']) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

/** Wrapper that renders Settings inside its parent route context. */
function createSettingsWrapper(initialEntries: string[] = ['/settings/updates']) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return () => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/settings/*" element={<Settings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function renderSettings(initialEntries: string[] = ['/settings/updates']) {
  const Wrapper = createSettingsWrapper(initialEntries)
  return render(<Wrapper />)
}

// ── Settings page structure ──────────────────────────────────────────

describe('Settings page', () => {
  it('renders settings heading and navigation', () => {
    setupFetchMocks()
    renderSettings()
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(screen.getByText('General')).toBeInTheDocument()
    // "Update Manager" appears in both nav and content heading
    expect(screen.getAllByText('Update Manager').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('System Health')).toBeInTheDocument()
    expect(screen.getByText('About')).toBeInTheDocument()
  })

  it('has accessible settings navigation', () => {
    setupFetchMocks()
    renderSettings()
    expect(screen.getByRole('navigation', { name: /Settings sections/i })).toBeInTheDocument()
  })

  it('shows Update Manager content on /settings/updates', async () => {
    setupFetchMocks()
    renderSettings(['/settings/updates'])
    expect(
      await screen.findByText(/Manage reference database versions/),
    ).toBeInTheDocument()
  })

  it('shows placeholder for General tab', () => {
    setupFetchMocks()
    renderSettings(['/settings/general'])
    expect(screen.getByText('General Settings')).toBeInTheDocument()
  })

  it('shows placeholder for System Health tab', () => {
    setupFetchMocks()
    renderSettings(['/settings/health'])
    // "System Health" appears in both nav and placeholder heading
    expect(screen.getAllByText('System Health').length).toBeGreaterThanOrEqual(2)
  })

  it('shows About page with version info', () => {
    setupFetchMocks()
    renderSettings(['/settings/about'])
    // "About" in nav + "About Yeliztli" in heading
    expect(screen.getByText('About')).toBeDefined()
    expect(screen.getByText('About Yeliztli')).toBeDefined()
    expect(screen.getByText('Current Version')).toBeDefined()
  })
})

// ── Update Manager component ─────────────────────────────────────────

describe('UpdateManager', () => {
  it('displays per-database table with version info', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })

    expect(await screen.findByText('ClinVar')).toBeInTheDocument()
    expect(screen.getByText('gnomAD')).toBeInTheDocument()
    expect(screen.getByText('dbNSFP')).toBeInTheDocument()
    expect(screen.getByText('Mar 2026')).toBeInTheDocument()
    expect(screen.getByText('2.1.1')).toBeInTheDocument()
    expect(screen.getByText('Not installed')).toBeInTheDocument()
  })

  it('has accessible database table', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByRole('table', { name: /Database versions/i })).toBeInTheDocument()
  })

  it('shows "All databases up to date" when no updates available', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText('All databases up to date')).toBeInTheDocument()
  })

  it('shows update available count when updates exist', async () => {
    setupFetchMocks({
      available: [
        {
          db_name: 'clinvar',
          latest_version: '20260320',
          download_size_bytes: 5242880,
          release_date: '2026-03-20',
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText('1 update available')).toBeInTheDocument()
  })

  it('shows "Update now" button for databases with updates', async () => {
    setupFetchMocks({
      available: [
        {
          db_name: 'clinvar',
          latest_version: '20260320',
          download_size_bytes: 5242880,
          release_date: '2026-03-20',
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText('Update now')).toBeInTheDocument()
  })

  const clinvarUpdate = {
    available: [
      {
        db_name: 'clinvar',
        latest_version: '20260320',
        download_size_bytes: 5242880,
        release_date: '2026-03-20',
      },
    ],
  }

  it('disables and relabels "Update now" while the DB is building', async () => {
    setupFetchMocks({
      ...clinvarUpdate,
      health: { databases: [{ name: 'clinvar', state: 'building', active_job_id: null }] },
    })
    render(<UpdateManager />, { wrapper: createWrapper() })
    const btn = await screen.findByTestId('db-update-clinvar')
    expect(btn).toBeDisabled()
    expect(btn).toHaveTextContent('Building…')
    expect(btn).not.toHaveTextContent('Update now')
  })

  it('disables "Update now" while the DB has an active job', async () => {
    setupFetchMocks({
      ...clinvarUpdate,
      health: {
        databases: [{ name: 'clinvar', state: 'ready', active_job_id: 'dbdl-clinvar-1' }],
      },
    })
    render(<UpdateManager />, { wrapper: createWrapper() })
    const btn = await screen.findByTestId('db-update-clinvar')
    expect(btn).toBeDisabled()
    expect(btn).toHaveTextContent('In progress…')
  })

  it('keeps "Update now" enabled when the DB is idle', async () => {
    setupFetchMocks({
      ...clinvarUpdate,
      health: { databases: [{ name: 'clinvar', state: 'ready', active_job_id: null }] },
    })
    render(<UpdateManager />, { wrapper: createWrapper() })
    const btn = await screen.findByTestId('db-update-clinvar')
    expect(btn).toBeEnabled()
    expect(btn).toHaveTextContent('Update now')
  })

  it('shows auto-update toggle for each database', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    // Match only the per-DB auto-update toggles by accessible name — this also
    // waits for the async DB rows (the "Automatically check for updates" master
    // toggle (#1287) is also role="switch" but renders immediately, so an
    // unfiltered findAllByRole would resolve on it before the rows arrive).
    const toggles = await screen.findAllByRole('switch', { name: /^Auto-update / })
    expect(toggles.length).toBe(3) // clinvar, gnomad, dbnsfp
    // ClinVar should be on
    expect(toggles[0]).toHaveAttribute('aria-checked', 'true')
    // gnomAD should be off
    expect(toggles[1]).toHaveAttribute('aria-checked', 'false')
  })

  it('toggles automatic update checks via the preferences API (#1287)', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    const master = await screen.findByRole('switch', {
      name: 'Automatically check for updates',
    })
    // The default fetch fallthrough returns {} → interval undefined → shown on.
    expect(master).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(master)
    await waitFor(() => {
      const put = mockFetch.mock.calls.find(
        (c) =>
          c[0] === '/api/preferences/update-check-interval' && c[1]?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      // Turning off from the on default persists "off".
      expect(JSON.parse(put![1].body as string)).toEqual({
        update_check_interval: 'off',
      })
    })
  })

  it('shows cadence labels', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText('Weekly')).toBeInTheDocument()
    expect(screen.getAllByText('Manual').length).toBeGreaterThanOrEqual(1)
  })

  it('has "Check for updates" button', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText('Check for updates')).toBeInTheDocument()
  })

  it('shows last checked timestamp', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText(/Last checked:/)).toBeInTheDocument()
  })
})

// ── Update History ──────────────────────────────────────────────────

describe('Update History', () => {
  it('renders collapsed by default', async () => {
    setupFetchMocks()
    render(<UpdateManager />, { wrapper: createWrapper() })
    const historyButton = await screen.findByRole('button', { name: /Update History/i })
    expect(historyButton).toHaveAttribute('aria-expanded', 'false')
  })

  it('expands to show history entries', async () => {
    setupFetchMocks({
      history: [
        {
          id: 1,
          db_name: 'clinvar',
          previous_version: '20260301',
          new_version: '20260315',
          updated_at: '2026-03-15T02:00:00Z',
          variants_added: 150,
          variants_reclassified: 3,
          download_size_bytes: 5242880,
          duration_seconds: 45,
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })

    const historyButton = await screen.findByRole('button', { name: /Update History/i })
    fireEvent.click(historyButton)
    expect(historyButton).toHaveAttribute('aria-expanded', 'true')

    // Should show the clinvar section
    expect(await screen.findByText('clinvar (1)')).toBeInTheDocument()
  })

  it('shows reclassification count in history', async () => {
    setupFetchMocks({
      history: [
        {
          id: 1,
          db_name: 'clinvar',
          previous_version: '20260301',
          new_version: '20260315',
          updated_at: '2026-03-15T02:00:00Z',
          variants_added: 150,
          variants_reclassified: 3,
          download_size_bytes: 5242880,
          duration_seconds: 45,
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })

    // Expand history
    const historyButton = await screen.findByRole('button', { name: /Update History/i })
    fireEvent.click(historyButton)

    // Expand clinvar section
    const clinvarButton = await screen.findByText('clinvar (1)')
    fireEvent.click(clinvarButton)

    expect(await screen.findByText('3 reclassified')).toBeInTheDocument()
    expect(screen.getByText('20260301 → 20260315')).toBeInTheDocument()
  })

  it('shows empty state when no history', async () => {
    setupFetchMocks({ history: [] })
    render(<UpdateManager />, { wrapper: createWrapper() })

    const historyButton = await screen.findByRole('button', { name: /Update History/i })
    fireEvent.click(historyButton)

    expect(await screen.findByText('No update history yet.')).toBeInTheDocument()
  })
})

// ── Re-annotation Banner ────────────────────────────────────────────

describe('Re-annotation Banner', () => {
  it('does not show when no prompts', async () => {
    setupFetchMocks({ prompts: [] })
    render(<UpdateManager />, { wrapper: createWrapper() })
    await screen.findByText('Update Manager')
    expect(screen.queryByText('Re-annotation recommended')).not.toBeInTheDocument()
  })

  it('shows banner with prompt details', async () => {
    setupFetchMocks({
      prompts: [
        {
          id: 1,
          sample_id: 1,
          db_name: 'clinvar',
          db_version: '20260315',
          candidate_count: 5,
          created_at: '2026-03-15T03:00:00Z',
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText('Re-annotation recommended')).toBeInTheDocument()
    expect(screen.getByText(/5 potential reclassification/)).toBeInTheDocument()
  })

  it('shows neutral reference-data staleness prompt details', async () => {
    setupFetchMocks({
      prompts: [
        {
          id: 2,
          sample_id: 1,
          db_name: 'reference_data',
          db_version: 'multiple',
          candidate_count: 0,
          prompt_type: 'version_staleness',
          stale_databases: [
            {
              db_name: 'gnomad',
              recorded_version: '2.1.1',
              current_version: '4.1.0',
            },
          ],
          created_at: '2026-06-29T03:00:00Z',
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })

    expect(await screen.findByText('Reference data updated')).toBeInTheDocument()
    expect(screen.getByText(/Reference data is newer than 1 analysis/)).toBeInTheDocument()
    expect(screen.getByText(/gnomad/)).toBeInTheDocument()
    expect(screen.queryByText(/potential reclassification/)).not.toBeInTheDocument()
    expect(screen.getByText('Dismiss (reference data)')).toBeInTheDocument()
  })

  it('counts distinct samples when summarizing reclassification prompts', async () => {
    setupFetchMocks({
      prompts: [
        {
          id: 3,
          sample_id: 1,
          db_name: 'clinvar',
          db_version: '20260601',
          candidate_count: 2,
          created_at: '2026-06-29T03:00:00Z',
        },
        {
          id: 4,
          sample_id: 1,
          db_name: 'gwas_catalog',
          db_version: '20260601',
          candidate_count: 1,
          created_at: '2026-06-29T03:00:00Z',
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })

    expect(await screen.findByText('Re-annotation recommended')).toBeInTheDocument()
    expect(
      screen.getByText(/3 potential reclassifications across 1 sample\./),
    ).toBeInTheDocument()
  })

  it('has dismiss button for each prompt', async () => {
    setupFetchMocks({
      prompts: [
        {
          id: 1,
          sample_id: 1,
          db_name: 'clinvar',
          db_version: '20260315',
          candidate_count: 5,
          created_at: '2026-03-15T03:00:00Z',
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })
    expect(await screen.findByText('Dismiss (clinvar)')).toBeInTheDocument()
  })

  it('calls dismiss endpoint when dismiss clicked', async () => {
    setupFetchMocks({
      prompts: [
        {
          id: 42,
          sample_id: 1,
          db_name: 'clinvar',
          db_version: '20260315',
          candidate_count: 5,
          created_at: '2026-03-15T03:00:00Z',
        },
      ],
    })
    render(<UpdateManager />, { wrapper: createWrapper() })

    const dismissBtn = await screen.findByText('Dismiss (clinvar)')
    fireEvent.click(dismissBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/updates/prompts/42/dismiss',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })
})

// ── Trigger Update ──────────────────────────────────────────────────

describe('Trigger Update', () => {
  it('calls trigger endpoint when "Update now" clicked', async () => {
    setupFetchMocks({
      available: [
        {
          db_name: 'clinvar',
          latest_version: '20260320',
          download_size_bytes: 5242880,
          release_date: '2026-03-20',
        },
      ],
      triggerResponse: {
        job_id: 'job-123',
        db_name: 'clinvar',
        message: 'Update queued for clinvar',
      },
    })

    render(<UpdateManager />, { wrapper: createWrapper() })

    const updateBtn = await screen.findByText('Update now')
    fireEvent.click(updateBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/updates/trigger',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ db_name: 'clinvar' }),
        }),
      )
    })
  })
})

// ── Bundle build_date display (Step 30) ──────────────────────────────

describe('Bundle build_date rendering', () => {
  it('renders combined version · build_date string from version_display', async () => {
    setupFetchMocks({
      statuses: [
        {
          db_name: 'lai_bundle',
          display_name: 'LAI bundle',
          current_version: 'v1.1',
          version_display: 'v1.1 · 2026-04-07',
          downloaded_at: '2026-04-07T00:00:00',
          auto_update: false,
          update_available: false,
        },
      ],
    })

    render(<UpdateManager />, { wrapper: createWrapper() })

    expect(await screen.findByText('v1.1 · 2026-04-07')).toBeInTheDocument()
  })
})

// ── App version row (Step 30) ────────────────────────────────────────

describe('App version row', () => {
  it('renders the Yeliztli row with current version', async () => {
    setupFetchMocks({
      appUpdate: {
        update_available: false,
        current_version: '0.1.0',
        latest_version: '0.1.0',
        release_url: null,
        release_notes: null,
        error: null,
      },
    })

    render(<UpdateManager />, { wrapper: createWrapper() })

    const row = await screen.findByTestId('app-version-row')
    expect(row).toHaveTextContent('Yeliztli')
    expect(row).toHaveTextContent('v0.1.0')
    expect(row).toHaveTextContent('Up to date')
  })

  it('shows release notes link when an update is available', async () => {
    setupFetchMocks({
      appUpdate: {
        update_available: true,
        current_version: '0.1.0',
        latest_version: '0.2.0',
        release_url: 'https://github.com/bioedca/Yeliztli/releases/v0.2.0',
        release_notes: 'New features',
        error: null,
      },
    })

    render(<UpdateManager />, { wrapper: createWrapper() })

    const row = await screen.findByTestId('app-version-row')
    expect(row).toHaveTextContent('v0.2.0')
    const link = row.querySelector('a')
    expect(link).not.toBeNull()
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/bioedca/Yeliztli/releases/v0.2.0',
    )
    expect(link).toHaveTextContent('Release notes')
  })
})

// ── Bandwidth window helper (Step 30) ────────────────────────────────

describe('isOutsideBandwidthWindow', () => {
  it('returns false when window is null/undefined/empty', () => {
    expect(isOutsideBandwidthWindow(null)).toBe(false)
    expect(isOutsideBandwidthWindow(undefined)).toBe(false)
    expect(isOutsideBandwidthWindow('')).toBe(false)
  })

  it('treats malformed window as no window (returns false)', () => {
    expect(isOutsideBandwidthWindow('bogus')).toBe(false)
    expect(isOutsideBandwidthWindow('02:00')).toBe(false)
  })

  it('returns false when current time is inside a simple window', () => {
    const now = new Date('2026-05-13T03:00:00')
    expect(isOutsideBandwidthWindow('02:00-06:00', now)).toBe(false)
  })

  it('returns true when current time is outside a simple window', () => {
    const now = new Date('2026-05-13T15:00:00')
    expect(isOutsideBandwidthWindow('02:00-06:00', now)).toBe(true)
  })

  it('handles windows that wrap midnight', () => {
    const lateNight = new Date('2026-05-13T23:30:00')
    const morning = new Date('2026-05-13T05:30:00')
    const midday = new Date('2026-05-13T13:00:00')
    expect(isOutsideBandwidthWindow('22:00-06:00', lateNight)).toBe(false)
    expect(isOutsideBandwidthWindow('22:00-06:00', morning)).toBe(false)
    expect(isOutsideBandwidthWindow('22:00-06:00', midday)).toBe(true)
  })
})

// ── Outside-window tooltip + Force update (Step 30) ──────────────────

describe('Outside-window tooltip and Force update', () => {
  beforeEach(() => {
    // Only fake the Date constructor — leaving setTimeout/setInterval real
    // so React Query's internal scheduling keeps working.
    vi.useFakeTimers({ toFake: ['Date'] })
    // 13:00 local — outside an 02:00-06:00 window
    vi.setSystemTime(new Date('2026-05-13T13:00:00'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('annotates "Update now" with an outside-window tooltip', async () => {
    setupFetchMocks({
      statuses: [
        {
          db_name: 'clinvar',
          display_name: 'ClinVar',
          current_version: '20260315',
          version_display: 'Mar 2026',
          downloaded_at: '2026-03-15T00:00:00',
          auto_update: true,
          update_available: false,
          update_download_window: '02:00-06:00',
        },
      ],
      available: [
        {
          db_name: 'clinvar',
          latest_version: '20260320',
          download_size_bytes: 5242880,
          release_date: '2026-03-20',
        },
      ],
    })

    render(<UpdateManager />, { wrapper: createWrapper() })

    const updateBtn = await screen.findByText('Update now')
    expect(updateBtn.closest('button')).toHaveAttribute(
      'title',
      expect.stringContaining('Outside bandwidth window'),
    )
    // The window string uses an en-dash for display
    expect(updateBtn.closest('button')!.getAttribute('title')).toContain('02:00–06:00')
  })

  it('does not show the tooltip when inside the window', async () => {
    vi.setSystemTime(new Date('2026-05-13T03:00:00'))
    setupFetchMocks({
      statuses: [
        {
          db_name: 'clinvar',
          display_name: 'ClinVar',
          current_version: '20260315',
          version_display: 'Mar 2026',
          downloaded_at: '2026-03-15T00:00:00',
          auto_update: true,
          update_available: false,
          update_download_window: '02:00-06:00',
        },
      ],
      available: [
        {
          db_name: 'clinvar',
          latest_version: '20260320',
          download_size_bytes: 5242880,
          release_date: '2026-03-20',
        },
      ],
    })

    render(<UpdateManager />, { wrapper: createWrapper() })

    const updateBtn = await screen.findByText('Update now')
    expect(updateBtn.closest('button')?.getAttribute('title') ?? '').toBe('')
    // Force button is not present inside the window
    expect(screen.queryByText('Force')).not.toBeInTheDocument()
  })

  it('Force update calls trigger with force=true (after confirm)', async () => {
    setupFetchMocks({
      statuses: [
        {
          db_name: 'clinvar',
          display_name: 'ClinVar',
          current_version: '20260315',
          version_display: 'Mar 2026',
          downloaded_at: '2026-03-15T00:00:00',
          auto_update: true,
          update_available: false,
          update_download_window: '02:00-06:00',
        },
      ],
      available: [
        {
          db_name: 'clinvar',
          latest_version: '20260320',
          download_size_bytes: 5242880,
          release_date: '2026-03-20',
        },
      ],
      triggerResponse: {
        job_id: 'job-force-1',
        db_name: 'clinvar',
        message: 'Update queued',
      },
    })

    const confirmSpy = vi.fn().mockReturnValue(true)
    vi.stubGlobal('confirm', confirmSpy)

    render(<UpdateManager />, { wrapper: createWrapper() })

    const forceBtn = await screen.findByText('Force')
    fireEvent.click(forceBtn)

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy.mock.calls[0][0]).toContain('Force update bypasses')

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/updates/trigger',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ db_name: 'clinvar', force: true }),
        }),
      )
    })

    // afterEach() calls vi.unstubAllGlobals() so no manual cleanup needed.
  })

  it('Force update does nothing when the confirm dialog is cancelled', async () => {
    setupFetchMocks({
      statuses: [
        {
          db_name: 'clinvar',
          display_name: 'ClinVar',
          current_version: '20260315',
          version_display: 'Mar 2026',
          downloaded_at: '2026-03-15T00:00:00',
          auto_update: true,
          update_available: false,
          update_download_window: '02:00-06:00',
        },
      ],
      available: [
        {
          db_name: 'clinvar',
          latest_version: '20260320',
          download_size_bytes: 5242880,
          release_date: '2026-03-20',
        },
      ],
    })

    const confirmSpy = vi.fn().mockReturnValue(false)
    vi.stubGlobal('confirm', confirmSpy)

    render(<UpdateManager />, { wrapper: createWrapper() })

    const forceBtn = await screen.findByText('Force')
    fireEvent.click(forceBtn)

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    // No trigger call was made — only the initial fetches
    const triggerCalls = mockFetch.mock.calls.filter(
      ([url, init]) =>
        typeof url === 'string' &&
        url.includes('/api/updates/trigger') &&
        (init as RequestInit | undefined)?.method === 'POST',
    )
    expect(triggerCalls).toHaveLength(0)

    // afterEach() calls vi.unstubAllGlobals() so no manual cleanup needed.
  })
})
