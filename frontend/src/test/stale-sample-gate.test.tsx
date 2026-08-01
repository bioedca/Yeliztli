/** Tests for <StaleSampleGate> (Step 14, Plan §7.5).
 *
 * Covers the full stale-sample lifecycle: initial gating, payload-driven
 * re-annotation, active-job reconnect/progress, automatic freshness polling,
 * conflict recovery, and retry after an active job disappears while the
 * sample remains stale.
 */

import type { ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import StaleSampleGate from '@/components/layout/StaleSampleGate'

const STALE_PAYLOAD = {
  installed_version: 'v1.0.0',
  required_version: 'v2.0.0',
  update_url: 'https://example.invalid/bundle-v2',
  reannotate_url: '/api/annotation/42',
}

const ACTIVE_JOB = {
  job_id: 'job-123',
  sample_id: 42,
  status: 'running',
  progress_pct: 37.5,
  message: 'Annotating variants',
}

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function createWrapper(initialEntries: string[] = ['/?sample_id=42']) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
}

function apiResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  })
}

function requestUrl(input: RequestInfo | URL) {
  return typeof input === 'string' ? input : input.toString()
}

function isStalenessRequest(url: string) {
  return url.startsWith('/api/variants/count')
}

function isActiveJobRequest(url: string) {
  return url === '/api/annotation/active/42'
}

describe('StaleSampleGate', () => {
  it('renders the labelled banner with payload-driven versions for a stale sample', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">protected content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(
      await screen.findByRole('region', { name: /sample requires re-annotation/i }),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    expect(screen.getByTestId('stale-installed-version')).toHaveTextContent('v1.0.0')
    expect(screen.getByTestId('stale-required-version')).toHaveTextContent('v2.0.0')
    expect(screen.getByRole('link', { name: /view bundle update/i })).toHaveAttribute(
      'href',
      STALE_PAYLOAD.update_url,
    )
  })

  it('renders children when the staleness probe returns 200', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      return apiResponse(200, { total: 12345 })
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">protected content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(await screen.findByTestId('protected-content')).toBeInTheDocument()
    expect(screen.queryByTestId('stale-sample-gate')).not.toBeInTheDocument()
    expect(mockFetch).toHaveBeenCalledWith('/api/variants/count?sample_id=42')
  })

  it('renders children without probing when no sample_id is in the URL', async () => {
    mockFetch.mockImplementation(() => apiResponse(200, {}))

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">no sample</div>
      </StaleSampleGate>,
      { wrapper: createWrapper(['/dashboard']) },
    )

    expect(await screen.findByTestId('protected-content')).toBeInTheDocument()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('derives the sample from a direct concordance-report route and posts its CTA', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === STALE_PAYLOAD.reannotate_url && init?.method === 'POST') {
        return apiResponse(202, {
          job_id: ACTIVE_JOB.job_id,
          sample_id: 42,
          status: 'pending',
        })
      }
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">concordance report</div>
      </StaleSampleGate>,
      { wrapper: createWrapper(['/samples/42/concordance']) },
    )

    expect(await screen.findByTestId('stale-sample-gate')).toBeInTheDocument()
    expect(mockFetch).toHaveBeenCalledWith('/api/variants/count?sample_id=42')
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('stale-reannotate-cta'))
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        STALE_PAYLOAD.reannotate_url,
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('prefers the direct concordance sample over a conflicting query parameter', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === '/api/annotation/42' && init?.method === 'POST') {
        return apiResponse(202, {
          job_id: ACTIVE_JOB.job_id,
          sample_id: 42,
          status: 'pending',
        })
      }
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (url === '/api/variants/count?sample_id=42') {
        return apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">concordance report</div>
      </StaleSampleGate>,
      { wrapper: createWrapper(['/samples/42/concordance?sample_id=99']) },
    )

    fireEvent.click(await screen.findByTestId('stale-reannotate-cta'))
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/annotation/42',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(mockFetch).toHaveBeenCalledWith('/api/variants/count?sample_id=42')
    expect(
      mockFetch.mock.calls.some(
        ([input]) => requestUrl(input as RequestInfo | URL).includes('sample_id=99'),
      ),
    ).toBe(false)
  })

  it('uses the canonical same-origin endpoint instead of an advertised cross-origin URL', async () => {
    const unsafePayload = {
      ...STALE_PAYLOAD,
      reannotate_url: 'https://untrusted.example/annotation/42',
    }
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === '/api/annotation/42' && init?.method === 'POST') {
        return apiResponse(202, {
          job_id: ACTIVE_JOB.job_id,
          sample_id: 42,
          status: 'pending',
        })
      }
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return apiResponse(423, { detail: unsafePayload })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    fireEvent.click(await screen.findByTestId('stale-reannotate-cta'))
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/annotation/42',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(
      mockFetch.mock.calls.some(([input]) => requestUrl(input as RequestInfo | URL) === unsafePayload.reannotate_url),
    ).toBe(false)
  })

  it('polls 423 to 200 after POST and lifts the gate without a remount', async () => {
    let started = false
    let postStartStalenessChecks = 0

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === STALE_PAYLOAD.reannotate_url && init?.method === 'POST') {
        started = true
        return apiResponse(202, {
          job_id: ACTIVE_JOB.job_id,
          sample_id: 42,
          status: 'pending',
        })
      }
      if (isActiveJobRequest(url)) {
        return started
          ? apiResponse(200, ACTIVE_JOB)
          : apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        if (!started) return apiResponse(423, { detail: STALE_PAYLOAD })
        postStartStalenessChecks += 1
        return postStartStalenessChecks >= 2
          ? apiResponse(200, { total: 12345 })
          : apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">fresh annotations</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    fireEvent.click(await screen.findByTestId('stale-reannotate-cta'))

    expect(await screen.findByTestId('stale-annotation-progress')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('progressbar', { name: /re-annotation progress/i }))
        .toHaveAttribute('aria-valuenow', '37.5')
    })
    expect(screen.getByTestId('stale-reannotate-cta')).toBeDisabled()

    expect(
      await screen.findByTestId('protected-content', {}, { timeout: 2_500 }),
    ).toBeInTheDocument()
    expect(postStartStalenessChecks).toBeGreaterThanOrEqual(2)
    expect(screen.queryByTestId('stale-sample-gate')).not.toBeInTheDocument()
  })

  it('reconnects to an active job on mount and disables duplicate submission', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) return apiResponse(200, ACTIVE_JOB)
      if (isStalenessRequest(url)) {
        return apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(await screen.findByText('Annotating variants')).toBeInTheDocument()
    expect(screen.getByText('37.5%')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: /re-annotation progress/i }))
      .toHaveAttribute('aria-valuenow', '37.5')
    expect(screen.getByTestId('stale-reannotate-cta')).toBeDisabled()
    expect(
      mockFetch.mock.calls.filter(([, init]) => init?.method === 'POST'),
    ).toHaveLength(0)
  })

  it('maps a 409 to friendly copy and reconnects without exposing the job UUID', async () => {
    const rawJobId = '6d15a253-69f6-41f6-bbe0-297c7196213a'
    let conflictReceived = false
    let recoveredActiveChecks = 0

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === STALE_PAYLOAD.reannotate_url && init?.method === 'POST') {
        conflictReceived = true
        return apiResponse(409, {
          detail: `Annotation already in progress for sample 42 (job ${rawJobId})`,
        })
      }
      if (isActiveJobRequest(url)) {
        if (!conflictReceived) {
          return apiResponse(404, { detail: 'No active job' })
        }
        recoveredActiveChecks += 1
        return recoveredActiveChecks === 1
          ? apiResponse(200, { ...ACTIVE_JOB, job_id: rawJobId })
          : apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    fireEvent.click(await screen.findByTestId('stale-reannotate-cta'))

    expect(await screen.findByTestId('stale-reconnect-status')).toHaveTextContent(
      /already running.*tracking progress/i,
    )
    expect(await screen.findByText('Annotating variants')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(rawJobId)
    expect(screen.getByTestId('stale-reannotate-cta')).toBeDisabled()

    await waitFor(
      () => expect(screen.getByTestId('stale-reannotate-cta')).toBeEnabled(),
      { timeout: 2_500 },
    )
    expect(screen.queryByTestId('stale-reconnect-status')).not.toBeInTheDocument()
    expect(screen.getByTestId('stale-sample-gate')).toBeInTheDocument()
  })

  it('re-enables retry when the active job disappears but the sample stays stale', async () => {
    let activeChecks = 0

    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        activeChecks += 1
        return activeChecks === 1
          ? apiResponse(200, ACTIVE_JOB)
          : apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    const cta = await screen.findByTestId('stale-reannotate-cta')
    expect(cta).toBeDisabled()

    await waitFor(() => expect(cta).toBeEnabled(), { timeout: 2_500 })
    expect(screen.getByTestId('stale-sample-gate')).toBeInTheDocument()
    expect(screen.queryByTestId('stale-annotation-progress')).not.toBeInTheDocument()
    expect(activeChecks).toBeGreaterThanOrEqual(2)
  })

  it('surfaces a non-conflict error and keeps the retry control enabled', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === STALE_PAYLOAD.reannotate_url && init?.method === 'POST') {
        return apiResponse(500, { detail: 'annotator unavailable' })
      }
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return apiResponse(423, { detail: STALE_PAYLOAD })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    fireEvent.click(await screen.findByTestId('stale-reannotate-cta'))

    expect(await screen.findByTestId('stale-error')).toHaveTextContent(
      /unable to start re-annotation\. please try again\./i,
    )
    expect(document.body).not.toHaveTextContent('annotator unavailable')
    expect(screen.getByTestId('stale-sample-gate')).toBeInTheDocument()
    expect(screen.getByTestId('stale-reannotate-cta')).toBeEnabled()
  })
})
