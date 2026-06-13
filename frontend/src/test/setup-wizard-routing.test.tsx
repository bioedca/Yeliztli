import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from './test-utils'
import SetupWizard from '@/pages/SetupWizard'

// Spy on navigation. Keep the rest of react-router-dom real (MemoryRouter used
// by test-utils, etc.) — SetupWizard only uses useNavigate for its redirects.
const navigateMock = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
  navigateMock.mockReset()
})

function routeStatus(setup: Record<string, unknown>) {
  mockFetch.mockImplementation((url: string) => {
    const u = typeof url === 'string' ? url : String(url)
    if (u.includes('/api/setup/status')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(setup) })
    }
    if (u.includes('/api/setup/disclaimer')) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({ title: 't', text: 'Disclaimer.', accept_label: 'Accept' }),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

function status(overrides: Record<string, unknown> = {}) {
  return {
    needs_setup: true,
    disclaimer_accepted: false,
    has_databases: false,
    required_dbs_ready: false,
    db_readiness: [],
    has_samples: false,
    data_dir: '/tmp',
    ...overrides,
  }
}

describe('SetupWizard — no silent dashboard redirect', () => {
  it('does not navigate to / while required DBs are not ready', async () => {
    routeStatus(status({ needs_setup: true, required_dbs_ready: false }))
    render(<SetupWizard />)

    await waitFor(() => expect(screen.getByText('Setup Wizard')).toBeInTheDocument())
    // The status-driven redirect effect must NOT fire for an unhealthy install.
    expect(navigateMock).not.toHaveBeenCalledWith('/', { replace: true })
  })

  it('navigates to / once setup is complete (health-ready)', async () => {
    routeStatus(
      status({ needs_setup: false, required_dbs_ready: true, disclaimer_accepted: true }),
    )
    render(<SetupWizard />)

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith('/', { replace: true }),
    )
  })
})

describe('SetupWizard — Import "Go to Dashboard" is readiness-gated', () => {
  function mockExistingInstall(setup: Record<string, unknown>) {
    mockFetch.mockImplementation((url: string) => {
      const u = typeof url === 'string' ? url : String(url)
      if (u.includes('/api/setup/status')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(setup) })
      }
      if (u.includes('/api/setup/detect-existing')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              existing_found: true,
              has_config: true,
              has_samples: true,
              has_databases: true,
              data_dir: '/tmp',
            }),
        })
      }
      if (u.includes('/api/databases/health')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ databases: [] }) })
      }
      if (u.includes('/api/databases')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              databases: [
                {
                  name: 'clinvar',
                  display_name: 'ClinVar',
                  description: 'Clinical variants',
                  filename: 'clinvar.db',
                  expected_size_bytes: 1,
                  required: true,
                  phase: 1,
                  downloaded: false,
                  file_size_bytes: null,
                  build_mode: 'pipeline',
                },
              ],
              total_size_bytes: 1,
              downloaded_count: 0,
              total_count: 1,
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
  }

  it('routes to the Databases recovery step (not the dashboard) when required DBs are not ready', async () => {
    // Disclaimer accepted → wizard opens on the Import step. detect-existing
    // reports a complete-by-presence install, so "Go to Dashboard" appears —
    // but the install is not health-ready.
    mockExistingInstall(
      status({ needs_setup: true, disclaimer_accepted: true, required_dbs_ready: false }),
    )
    render(<SetupWizard />)

    const goBtn = await screen.findByRole('button', { name: /go to dashboard/i })
    fireEvent.click(goBtn)

    // Must never silently hand off to a broken dashboard...
    expect(navigateMock).not.toHaveBeenCalledWith('/', { replace: true })
    // ...instead it lands on the Databases recovery step.
    expect(await screen.findByText('Reference Databases')).toBeInTheDocument()
  })
})
