import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Routes, Route } from 'react-router-dom'
import { render, screen, waitFor } from './test-utils'
import AuthGuard from '@/components/AuthGuard'

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
})

function routeStatus(opts: {
  setup: Record<string, unknown>
  auth?: Record<string, unknown>
}) {
  mockFetch.mockImplementation((url: string) => {
    const u = typeof url === 'string' ? url : String(url)
    if (u.includes('/api/auth/status')) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve(opts.auth ?? { auth_enabled: false, authenticated: false }),
      })
    }
    if (u.includes('/api/setup/status')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(opts.setup) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

function setupStatus(overrides: Record<string, unknown> = {}) {
  return {
    needs_setup: false,
    disclaimer_accepted: true,
    has_databases: true,
    required_dbs_ready: true,
    db_readiness: [],
    has_samples: true,
    data_dir: '/tmp',
    ...overrides,
  }
}

function renderGuard() {
  return render(
    <Routes>
      <Route element={<AuthGuard />}>
        <Route path="/" element={<div>DASHBOARD</div>} />
      </Route>
      <Route path="/setup" element={<div>SETUP PAGE</div>} />
      <Route path="/login" element={<div>LOGIN PAGE</div>} />
    </Routes>,
  )
}

describe('AuthGuard — health-gated dashboard access', () => {
  it('redirects to /setup when needs_setup is true (required DBs unhealthy)', async () => {
    // The backend now flips needs_setup=true whenever a required, downloadable
    // DB is not integrity-ready. AuthGuard must keep the user out of the
    // dashboard — this is the regression that previously let a failed/partial
    // download silently land on a broken dashboard.
    routeStatus({ setup: setupStatus({ needs_setup: true, required_dbs_ready: false }) })
    renderGuard()

    await waitFor(() => expect(screen.getByText('SETUP PAGE')).toBeInTheDocument())
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument()
  })

  it('renders the dashboard when required DBs are ready', async () => {
    routeStatus({ setup: setupStatus({ needs_setup: false, required_dbs_ready: true }) })
    renderGuard()

    await waitFor(() => expect(screen.getByText('DASHBOARD')).toBeInTheDocument())
    expect(screen.queryByText('SETUP PAGE')).not.toBeInTheDocument()
  })

  it('redirects to /login when auth is enabled and unauthenticated', async () => {
    routeStatus({
      setup: setupStatus(),
      auth: { auth_enabled: true, authenticated: false },
    })
    renderGuard()

    await waitFor(() => expect(screen.getByText('LOGIN PAGE')).toBeInTheDocument())
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument()
  })
})
