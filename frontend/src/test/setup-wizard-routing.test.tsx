import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from './test-utils'
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
