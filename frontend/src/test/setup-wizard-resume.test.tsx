import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from './test-utils'
import SetupWizard from '@/pages/SetupWizard'

const navigateMock = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

const STEP_KEY = 'yeliztli.setupWizard.step'

beforeEach(() => {
  mockFetch.mockReset()
  navigateMock.mockReset()
  sessionStorage.clear()
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
        json: () => Promise.resolve({ title: 't', text: 'Disclaimer.', accept_label: 'Accept' }),
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

function activeStepLabel(): string | null | undefined {
  return document.querySelector('[aria-current="step"]')?.textContent
}

describe('SetupWizard — resume on reload', () => {
  it('resumes at the step stored in sessionStorage', async () => {
    sessionStorage.setItem(STEP_KEY, '3') // Credentials (0-indexed) → stepper "4"
    routeStatus(status({ disclaimer_accepted: true }))
    render(<SetupWizard />)

    await waitFor(() => expect(activeStepLabel()).toBe('4'))
  })

  it('persists the current step to sessionStorage', async () => {
    // Disclaimer accepted → wizard advances to step 1 (Import), which is persisted.
    routeStatus(status({ disclaimer_accepted: true }))
    render(<SetupWizard />)

    await waitFor(() => expect(sessionStorage.getItem(STEP_KEY)).toBe('1'))
  })

  it('clamps a stale resumed step back to the disclaimer when not yet accepted', async () => {
    sessionStorage.setItem(STEP_KEY, '3')
    routeStatus(status({ disclaimer_accepted: false }))
    render(<SetupWizard />)

    // Can't sit on a post-disclaimer step while the disclaimer is unaccepted.
    await waitFor(() => expect(activeStepLabel()).toBe('1'))
  })

  it('clears the resume hint and redirects when setup is complete', async () => {
    sessionStorage.setItem(STEP_KEY, '4')
    routeStatus(status({ needs_setup: false, disclaimer_accepted: true, required_dbs_ready: true }))
    render(<SetupWizard />)

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/', { replace: true }))
    expect(sessionStorage.getItem(STEP_KEY)).toBeNull()
  })

  it('keeps the hint cleared even when the clamp could fire under completed status', async () => {
    // Stored step 0 + completed: the clamp would advance 0→1 and the persist
    // effect would re-write the key after the redirect cleared it — guard that.
    sessionStorage.setItem(STEP_KEY, '0')
    routeStatus(status({ needs_setup: false, disclaimer_accepted: true, required_dbs_ready: true }))
    render(<SetupWizard />)

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/', { replace: true }))
    // Stays null (fails — never null — without the clamp's needs_setup guard).
    await waitFor(() => expect(sessionStorage.getItem(STEP_KEY)).toBeNull())
  })

  it('falls back to the first step when the stored step is invalid/out-of-range', async () => {
    sessionStorage.setItem(STEP_KEY, '99') // out of range → discarded → 0
    routeStatus(status({ disclaimer_accepted: true }))
    render(<SetupWizard />)

    // 99 discarded → step 0 → disclaimer clamp advances to step 1 (stepper "2"),
    // never an out-of-range/blank step.
    await waitFor(() => expect(activeStepLabel()).toBe('2'))
  })
})
