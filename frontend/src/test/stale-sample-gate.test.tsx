/** Tests for <StaleSampleGate> (Step 14, Plan §7.5).
 *
 * Covers the full stale-sample lifecycle: initial gating, payload-driven
 * re-annotation, active-job reconnect/progress, automatic freshness polling,
 * conflict recovery, and retry after an active job disappears while the
 * sample remains stale.
 */

import { act, type ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { Link, MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { fetchActiveAnnotationJob, useStartAnnotation } from '@/api/annotation'
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
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function createTestQueryClient(gcTime = 0) {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime },
      mutations: { retry: false },
    },
  })
}

function createWrapper(
  initialEntries: string[] = ['/?sample_id=42'],
  queryClient = createTestQueryClient(),
) {
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

/** Minimal dashboard-side caller for the shared annotation-launch hook. */
function DashboardAnnotationLauncher({ sampleId = 42 }: { sampleId?: number }) {
  const startAnnotation = useStartAnnotation()
  return (
    <button type="button" onClick={() => startAnnotation.mutate(sampleId)}>
      Run annotation
    </button>
  )
}

/** Keeps one active result refetch unresolved after launch. */
function LaunchWithUnresolvedResult({ sampleId = 42 }: { sampleId?: number }) {
  const startAnnotation = useStartAnnotation()
  useQuery({
    queryKey: ['pharma-genes', sampleId],
    queryFn: () => new Promise<never>(() => {}),
    initialData: { cached: 'pre-annotation result' },
    staleTime: Infinity,
  })
  return (
    <>
      <button type="button" onClick={() => startAnnotation.mutate(sampleId)}>
        Start annotation
      </button>
      {startAnnotation.isSuccess ? <span data-testid="annotation-launch-accepted">Accepted</span> : null}
    </>
  )
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

  it.each(['javascript:alert(1)', 'data:text/html,unsafe'])(
    'omits an unsafe update URL (%s)',
    async (updateUrl) => {
      mockFetch.mockImplementation((input: RequestInfo | URL) => {
        const url = requestUrl(input)
        if (isActiveJobRequest(url)) {
          return apiResponse(404, { detail: 'No active job' })
        }
        if (isStalenessRequest(url)) {
          return apiResponse(423, {
            detail: { ...STALE_PAYLOAD, update_url: updateUrl },
          })
        }
        return apiResponse(200, {})
      })

      render(
        <StaleSampleGate>
          <div data-testid="protected-content">protected content</div>
        </StaleSampleGate>,
        { wrapper: createWrapper() },
      )

      expect(await screen.findByTestId('stale-sample-gate')).toBeInTheDocument()
      expect(screen.queryByRole('link', { name: /view bundle update/i })).not.toBeInTheDocument()
    },
  )

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
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/variants/count?sample_id=42',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('fences an already-fresh route after a dashboard launch reaches a 423 probe', async () => {
    let started = false
    let stalenessCalls = 0
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === '/api/annotation/42' && init?.method === 'POST') {
        started = true
        return apiResponse(202, {
          job_id: ACTIVE_JOB.job_id,
          sample_id: 42,
          status: 'pending',
        })
      }
      if (isActiveJobRequest(url)) {
        return started ? apiResponse(200, ACTIVE_JOB) : apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        stalenessCalls += 1
        return started ? apiResponse(423, { detail: STALE_PAYLOAD }) : apiResponse(200, { total: 12345 })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <DashboardAnnotationLauncher />
        <div data-testid="protected-content">fresh content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(await screen.findByTestId('protected-content')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run annotation' }))

    expect(await screen.findByTestId('stale-sample-gate')).toBeInTheDocument()
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    expect(stalenessCalls).toBeGreaterThanOrEqual(2)
  })

  it('accepts a launch without waiting for an invalidated active result refetch', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === '/api/annotation/42' && init?.method === 'POST') {
        return apiResponse(202, {
          job_id: ACTIVE_JOB.job_id,
          sample_id: 42,
          status: 'pending',
        })
      }
      return apiResponse(500, { detail: 'not mocked' })
    })

    const queryClient = createTestQueryClient(Infinity)
    render(<LaunchWithUnresolvedResult />, {
      wrapper: createWrapper(['/dashboard'], queryClient),
    })

    fireEvent.click(screen.getByRole('button', { name: 'Start annotation' }))
    expect(await screen.findByTestId('annotation-launch-accepted')).toHaveTextContent('Accepted')
    queryClient.clear()
  })

  it('revalidates freshness before rendering a newly navigated analysis route', async () => {
    let stalenessCalls = 0
    let resolveRouteProbe: (() => void) | undefined
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        stalenessCalls += 1
        if (stalenessCalls === 1) return apiResponse(200, { total: 12345 })
        return new Promise((resolve) => {
          resolveRouteProbe = () =>
            resolve({
              ok: false,
              status: 423,
              json: async () => ({ detail: STALE_PAYLOAD }),
              text: async () => JSON.stringify({ detail: STALE_PAYLOAD }),
            })
        })
      }
      return apiResponse(200, {})
    })

    render(
      <Routes>
        <Route
          element={(
            <StaleSampleGate>
              <Outlet />
            </StaleSampleGate>
          )}
        >
          <Route
            path="/findings"
            element={(
              <>
                <div data-testid="findings-content">fresh findings</div>
                <Link to="/variants?sample_id=42">Open variants</Link>
              </>
            )}
          />
          <Route
            path="/variants"
            element={<div data-testid="variants-content">cached variants</div>}
          />
        </Route>
      </Routes>,
      { wrapper: createWrapper(['/findings?sample_id=42']) },
    )

    expect(await screen.findByTestId('findings-content')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: 'Open variants' }))

    await waitFor(() => expect(resolveRouteProbe).toBeDefined())
    expect(screen.queryByTestId('variants-content')).not.toBeInTheDocument()

    resolveRouteProbe?.()
    expect(await screen.findByTestId('stale-sample-gate')).toBeInTheDocument()
  })

  it('invalidates cached sample results after re-annotation completes away from the gate', async () => {
    const queryClient = createTestQueryClient(Infinity)
    queryClient.setQueryData(['findings', 42], { cached: 'pre-annotation findings' })
    queryClient.setQueryData(['pharma-genes', 42], { cached: 'pre-annotation pharmacogenomics' })
    queryClient.setQueryData(['pharma-genes', 99], { cached: 'other sample' })
    queryClient.setQueryData(['nutrigenomics-pathway-detail', 42, 99], {
      cached: 'other sample, target-sized pathway ID',
    })
    queryClient.setQueryData(['findings', 99, null, null, null, 42, 0], {
      cached: 'other sample, target-sized page limit',
    })
    let stalenessCalls = 0
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
        stalenessCalls += 1
        if (stalenessCalls === 1) return apiResponse(423, { detail: STALE_PAYLOAD })
        if (stalenessCalls === 2) return new Promise(() => {})
        return apiResponse(200, { total: 12345 })
      }
      return apiResponse(200, {})
    })

    render(
      <Routes>
        <Route
          path="/findings"
          element={(
            <>
              <Link to="/settings">Open settings</Link>
              <StaleSampleGate>
                <div data-testid="fresh-content">fresh findings</div>
              </StaleSampleGate>
            </>
          )}
        />
        <Route
          path="/settings"
          element={<Link to="/findings?sample_id=42">Return to findings</Link>}
        />
      </Routes>,
      { wrapper: createWrapper(['/findings?sample_id=42'], queryClient) },
    )

    fireEvent.click(await screen.findByTestId('stale-reannotate-cta'))
    await waitFor(() => expect(stalenessCalls).toBeGreaterThanOrEqual(2))

    fireEvent.click(screen.getByRole('link', { name: 'Open settings' }))
    fireEvent.click(await screen.findByRole('link', { name: 'Return to findings' }))

    expect(await screen.findByTestId('fresh-content')).toBeInTheDocument()
    await waitFor(() => {
      expect(queryClient.getQueryState(['findings', 42])?.isInvalidated).toBe(true)
      expect(queryClient.getQueryState(['pharma-genes', 42])?.isInvalidated).toBe(true)
      expect(queryClient.getQueryState(['pharma-genes', 99])?.isInvalidated).toBe(false)
      expect(
        queryClient.getQueryState(['nutrigenomics-pathway-detail', 42, 99])?.isInvalidated,
      ).toBe(false)
      expect(
        queryClient.getQueryState(['findings', 99, null, null, null, 42, 0])?.isInvalidated,
      ).toBe(false)
    })
  })

  it('keeps fresh content mounted during a background staleness refetch', async () => {
    const queryClient = createTestQueryClient(Infinity)
    let stalenessCalls = 0
    let resolveBackgroundProbe: (() => void) | undefined
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        stalenessCalls += 1
        if (stalenessCalls === 1) return apiResponse(200, { total: 12345 })
        return new Promise((resolve) => {
          resolveBackgroundProbe = () =>
            resolve({
              ok: true,
              status: 200,
              json: async () => ({ total: 12345 }),
              text: async () => JSON.stringify({ total: 12345 }),
            })
        })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">fresh content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper(['/?sample_id=42'], queryClient) },
    )

    expect(await screen.findByTestId('protected-content')).toBeInTheDocument()

    act(() => {
      void queryClient.invalidateQueries({ queryKey: ['sample-staleness', 42] })
    })

    await waitFor(() => expect(resolveBackgroundProbe).toBeDefined())
    expect(screen.getByTestId('protected-content')).toBeInTheDocument()

    act(() => resolveBackgroundProbe?.())
    await waitFor(() => expect(stalenessCalls).toBe(2))
  })

  it('lets routes render their missing-sample fallback when the probe returns 404', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return apiResponse(404, { detail: 'Sample not found' })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">missing-sample fallback</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(await screen.findByTestId('protected-content')).toHaveTextContent(
      'missing-sample fallback',
    )
    expect(screen.queryByTestId('staleness-probe-unavailable')).not.toBeInTheDocument()
  })

  it('does not block a fresh route on an unresolved active-job probe', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return new Promise(() => {})
      }
      if (isStalenessRequest(url)) {
        return apiResponse(200, { total: 12345 })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">fresh content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(await screen.findByTestId('protected-content')).toBeInTheDocument()
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/annotation/active/42',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/variants/count?sample_id=42',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('surfaces an active-job probe timeout instead of treating it as no job', async () => {
    vi.useFakeTimers()
    let activeJobSignal: AbortSignal | undefined
    mockFetch.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      activeJobSignal = init?.signal ?? undefined
      return new Promise((_resolve, reject) => {
        activeJobSignal?.addEventListener('abort', () => {
          reject(new DOMException('Timed out', 'AbortError'))
        })
      })
    })

    const queryController = new AbortController()
    const activeJob = fetchActiveAnnotationJob(42, queryController.signal)
    const activeJobFailure = expect(activeJob).rejects.toMatchObject({ name: 'AbortError' })
    expect(activeJobSignal).toBeDefined()
    expect(activeJobSignal?.aborted).toBe(false)

    await vi.advanceTimersByTimeAsync(10_000)
    expect(activeJobSignal?.aborted).toBe(true)
    await activeJobFailure
  })

  it('times out a staleness probe and keeps sample content fenced', async () => {
    vi.useFakeTimers()
    let stalenessSignal: AbortSignal | undefined
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        stalenessSignal = init?.signal ?? undefined
        return new Promise((_resolve, reject) => {
          stalenessSignal?.addEventListener('abort', () => {
            reject(new DOMException('Timed out', 'AbortError'))
          })
        })
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">protected content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(stalenessSignal).toBeDefined()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
      await vi.advanceTimersByTimeAsync(1)
    })

    expect(stalenessSignal?.aborted).toBe(true)
    expect(screen.getByTestId('staleness-probe-unavailable')).toBeInTheDocument()
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
  })

  it('preserves query cancellation for an active-job probe', async () => {
    let activeJobSignal: AbortSignal | undefined
    mockFetch.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      activeJobSignal = init?.signal ?? undefined
      return new Promise((_resolve, reject) => {
        activeJobSignal?.addEventListener('abort', () => {
          reject(new DOMException('Cancelled', 'AbortError'))
        })
      })
    })

    const queryController = new AbortController()
    const activeJob = fetchActiveAnnotationJob(42, queryController.signal)
    queryController.abort()

    await expect(activeJob).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('renders stale recovery when the active-job probe is unavailable', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return Promise.reject(new DOMException('Unavailable', 'AbortError'))
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

    expect(await screen.findByTestId('stale-sample-gate')).toBeInTheDocument()
    expect(screen.getByTestId('stale-reannotate-cta')).toBeEnabled()
  })

  it('keeps protected content fenced when the staleness probe cannot establish freshness', async () => {
    const rawDiagnostic = 'sqlite:///private/data/sample.db: connection refused'
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input)
      if (isActiveJobRequest(url)) {
        return apiResponse(404, { detail: 'No active job' })
      }
      if (isStalenessRequest(url)) {
        return Promise.reject(new Error(rawDiagnostic))
      }
      return apiResponse(200, {})
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">protected content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    const unavailable = await screen.findByTestId('staleness-probe-unavailable')
    expect(unavailable).toHaveTextContent('Unable to verify sample freshness')
    expect(unavailable).toHaveTextContent('Retry freshness check')
    expect(unavailable).not.toHaveTextContent(rawDiagnostic)
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
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
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/variants/count?sample_id=42',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('stale-reannotate-cta'))
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        STALE_PAYLOAD.reannotate_url,
        expect.objectContaining({ method: 'POST', signal: expect.any(AbortSignal) }),
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
        expect.objectContaining({ method: 'POST', signal: expect.any(AbortSignal) }),
      )
    })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/variants/count?sample_id=42',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
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

  it('keeps conflict recovery fenced and polling when the active-job probe is unavailable', async () => {
    let conflictReceived = false
    let recoveredActiveChecks = 0

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === STALE_PAYLOAD.reannotate_url && init?.method === 'POST') {
        conflictReceived = true
        return apiResponse(409, { detail: 'Annotation already in progress' })
      }
      if (isActiveJobRequest(url)) {
        if (!conflictReceived) return apiResponse(404, { detail: 'No active job' })
        recoveredActiveChecks += 1
        return recoveredActiveChecks === 1
          ? apiResponse(503, { detail: 'temporary outage' })
          : apiResponse(200, ACTIVE_JOB)
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
    fireEvent.click(cta)

    await waitFor(() => expect(recoveredActiveChecks).toBe(1))
    expect(screen.getByTestId('stale-reconnect-status')).toBeInTheDocument()
    expect(cta).toBeDisabled()

    await waitFor(() => expect(recoveredActiveChecks).toBeGreaterThanOrEqual(2), {
      timeout: 2_500,
    })
    expect(await screen.findByText('Annotating variants')).toBeInTheDocument()
    expect(recoveredActiveChecks).toBeGreaterThanOrEqual(2)
    expect(cta).toBeDisabled()
  })

  it('re-enables retry after a failed conflict probe later confirms no active job', async () => {
    let conflictReceived = false
    let recoveredActiveChecks = 0

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === STALE_PAYLOAD.reannotate_url && init?.method === 'POST') {
        conflictReceived = true
        return apiResponse(409, { detail: 'Annotation already in progress' })
      }
      if (isActiveJobRequest(url)) {
        if (!conflictReceived) return apiResponse(404, { detail: 'No active job' })
        recoveredActiveChecks += 1
        return recoveredActiveChecks === 1
          ? apiResponse(503, { detail: 'temporary outage' })
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
    fireEvent.click(cta)

    await waitFor(() => expect(recoveredActiveChecks).toBeGreaterThanOrEqual(2), {
      timeout: 2_500,
    })
    await waitFor(() => expect(cta).toBeEnabled())
    expect(screen.queryByTestId('stale-reconnect-status')).not.toBeInTheDocument()
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

  it('times out re-annotation with a safe retry message', async () => {
    vi.useFakeTimers()
    let reannotationSignal: AbortSignal | undefined
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      if (url === STALE_PAYLOAD.reannotate_url && init?.method === 'POST') {
        reannotationSignal = init.signal ?? undefined
        return new Promise((_resolve, reject) => {
          reannotationSignal?.addEventListener('abort', () => {
            reject(new DOMException('Timed out', 'AbortError'))
          })
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
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    fireEvent.click(screen.getByTestId('stale-reannotate-cta'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(reannotationSignal).toBeDefined()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
      await vi.advanceTimersByTimeAsync(1)
    })

    expect(reannotationSignal?.aborted).toBe(true)
    expect(screen.getByTestId('stale-error')).toHaveTextContent(
      'Unable to start re-annotation. Please try again.',
    )
    expect(screen.getByTestId('stale-reannotate-cta')).toBeEnabled()
  })
})
