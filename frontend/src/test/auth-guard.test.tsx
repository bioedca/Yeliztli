import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Routes, Route } from 'react-router-dom'
import { fireEvent, render, screen, waitFor } from './test-utils'
import AuthGuard from '@/components/AuthGuard'

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
})

type StatusRoute = Record<string, unknown> | { errorStatus: number }

function failedStatus(errorStatus = 503): StatusRoute {
  return { errorStatus }
}

function responseFor(status: StatusRoute) {
  if ('errorStatus' in status) {
    return Promise.resolve({
      ok: false,
      status: status.errorStatus,
      json: () => Promise.resolve({}),
    })
  }

  return Promise.resolve({ ok: true, json: () => Promise.resolve(status) })
}

function routeStatus(opts: {
  setup: StatusRoute
  auth?: StatusRoute
}) {
  mockFetch.mockImplementation((url: string) => {
    const u = typeof url === 'string' ? url : String(url)
    if (u.includes('/api/auth/status')) {
      return responseFor(
        opts.auth ?? { auth_enabled: false, authenticated: false },
      )
    }
    if (u.includes('/api/setup/status')) {
      return responseFor(opts.setup)
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

  it('shows a backend error instead of the dashboard when setup status fails', async () => {
    routeStatus({ setup: failedStatus(503) })
    renderGuard()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(
      screen.getByText(/can't reach the Yeliztli backend/i),
    ).toBeInTheDocument()
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument()
  })

  it('shows a backend error instead of the dashboard when auth status fails', async () => {
    routeStatus({ setup: setupStatus(), auth: failedStatus(503) })
    renderGuard()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(
      screen.getByText(/can't reach the Yeliztli backend/i),
    ).toBeInTheDocument()
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument()
  })

  it('retries a failed setup status request and renders the dashboard after recovery', async () => {
    let setupCalls = 0
    mockFetch.mockImplementation((url: string) => {
      const u = typeof url === 'string' ? url : String(url)
      if (u.includes('/api/setup/status')) {
        setupCalls += 1
        return setupCalls === 1
          ? responseFor(failedStatus(503))
          : responseFor(setupStatus())
      }
      if (u.includes('/api/auth/status')) {
        return responseFor({ auth_enabled: false, authenticated: false })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    renderGuard()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText('DASHBOARD')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(setupCalls).toBe(2)
  })
})
