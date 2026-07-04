import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from './test-utils'
import App from '../App'

function jsonResponse(body: unknown): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL): Promise<Response> => {
        const url = requestUrl(input)

        if (url.includes('/api/auth/status')) {
          return jsonResponse({
            auth_enabled: false,
            has_password: false,
            authenticated: true,
          })
        }

        if (url.includes('/api/setup/status')) {
          return jsonResponse({
            needs_setup: false,
            disclaimer_accepted: true,
            has_databases: true,
            required_dbs_ready: true,
            db_readiness: [],
            has_samples: false,
            data_dir: '/tmp/.yeliztli',
          })
        }

        if (url.includes('/api/samples') || url.includes('/api/individuals')) {
          return jsonResponse([])
        }

        return jsonResponse({})
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders a navigable 404 page for unmatched app routes', async () => {
    render(<App />, { route: '/individuals' })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Page not found' }),
    ).toBeInTheDocument()
    expect(screen.getByText('/individuals')).toBeInTheDocument()
    expect(
      screen.getByRole('navigation', { name: 'Main navigation' }),
    ).toBeInTheDocument()
  })

  it('keeps the Back action inside the app on direct 404 entry', async () => {
    render(<App />, { route: '/individuals' })

    await screen.findByRole('heading', { level: 1, name: 'Page not found' })
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Dashboard' }),
    ).toBeInTheDocument()
  })
})
