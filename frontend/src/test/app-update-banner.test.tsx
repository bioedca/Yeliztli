/** Tests for AppUpdateBanner — Dashboard banner announcing new releases (P4-21b, Step 29). */

import type { ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import AppUpdateBanner from '@/components/layout/AppUpdateBanner'

// ── Mocks ────────────────────────────────────────────────────────────

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
  window.localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

interface AppUpdatePayload {
  update_available: boolean
  current_version: string
  latest_version: string | null
  release_url: string | null
  release_notes: string | null
  error: string | null
}

interface LAIStatusPayload {
  bundle_downloaded: boolean
  java_available: boolean
  lai_available: boolean
  coverage_policy_available?: boolean
  message: string
  insufficient_data_reason?: { message: string } | null
  degraded_coverage?: boolean
}

const LAI_STATUS_CLEAR: LAIStatusPayload = {
  bundle_downloaded: true,
  java_available: true,
  lai_available: true,
  message: 'Chromosome painting is available.',
  degraded_coverage: false,
}

function setupAppUpdateMock(
  payload: AppUpdatePayload,
  laiStatus: LAIStatusPayload = LAI_STATUS_CLEAR,
) {
  mockFetch.mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/api/updates/app-update')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => payload,
      })
    }
    if (typeof url === 'string' && url.includes('/api/analysis/ancestry/lai/status')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => laiStatus,
      })
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  })
}

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

const UPDATE_AVAILABLE: AppUpdatePayload = {
  update_available: true,
  current_version: '0.1.0',
  latest_version: '1.2.0',
  release_url: 'https://github.com/bioedca/Yeliztli/releases/tag/v1.2.0',
  release_notes: null,
  error: null,
}

const UP_TO_DATE: AppUpdatePayload = {
  update_available: false,
  current_version: '1.2.0',
  latest_version: '1.2.0',
  release_url: null,
  release_notes: null,
  error: null,
}

// ── Tests ────────────────────────────────────────────────────────────

describe('AppUpdateBanner', () => {
  it('renders banner when an update is available', async () => {
    setupAppUpdateMock(UPDATE_AVAILABLE)
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    expect(
      await screen.findByText(/Yeliztli v1\.2\.0 is available/),
    ).toBeInTheDocument()
  })

  it('shows release notes link when release_url is provided', async () => {
    setupAppUpdateMock(UPDATE_AVAILABLE)
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    const link = await screen.findByRole('link', { name: /view release notes/i })
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/bioedca/Yeliztli/releases/tag/v1.2.0',
    )
  })

  it('does not render when up to date', async () => {
    setupAppUpdateMock(UP_TO_DATE)
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    // Wait long enough for the query to resolve, then assert absent.
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/updates/app-update')
    })
    expect(
      screen.queryByText(/is available/),
    ).not.toBeInTheDocument()
  })

  it('persists dismissal per-version in localStorage', async () => {
    setupAppUpdateMock(UPDATE_AVAILABLE)
    const { unmount } = render(<AppUpdateBanner />, { wrapper: createWrapper() })

    const dismissBtn = await screen.findByRole('button', {
      name: /dismiss update notification/i,
    })
    fireEvent.click(dismissBtn)

    await waitFor(() => {
      expect(window.localStorage.getItem('appUpdateDismissed')).toBe('1.2.0')
    })
    // Banner disappears for the same version
    expect(screen.queryByText(/is available/)).not.toBeInTheDocument()

    // Re-mount with the same version: banner stays hidden
    unmount()
    render(<AppUpdateBanner />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/updates/app-update')
    })
    expect(screen.queryByText(/is available/)).not.toBeInTheDocument()
  })

  it('re-appears for a newer version after a previous dismissal', async () => {
    window.localStorage.setItem('appUpdateDismissed', '1.2.0')
    setupAppUpdateMock({ ...UPDATE_AVAILABLE, latest_version: '1.3.0' })
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    expect(
      await screen.findByText(/Yeliztli v1\.3\.0 is available/),
    ).toBeInTheDocument()
  })

  it('hides immediately when the current version equals the dismissed one', async () => {
    window.localStorage.setItem('appUpdateDismissed', '1.2.0')
    setupAppUpdateMock(UPDATE_AVAILABLE)
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/updates/app-update')
    })
    expect(screen.queryByText(/is available/)).not.toBeInTheDocument()
  })
})

// ── Step 23 — LAI degraded-coverage banner (Plan §6.7) ────────────────

const LAI_STATUS_DEGRADED: LAIStatusPayload = {
  bundle_downloaded: true,
  java_available: true,
  lai_available: true,
  message: 'Chromosome painting is available.',
  degraded_coverage: true,
}

describe('AppUpdateBanner — LAI degraded coverage advisory', () => {
  it('renders the LAI degraded-coverage banner when the API flags it', async () => {
    setupAppUpdateMock(UP_TO_DATE, LAI_STATUS_DEGRADED)
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    expect(
      await screen.findByText(
        /LAI coverage degraded for AncestryDNA — update bundle to v2\.0\.0/,
      ),
    ).toBeInTheDocument()
  })

  it('does not render when the API reports degraded_coverage=false', async () => {
    setupAppUpdateMock(UP_TO_DATE, LAI_STATUS_CLEAR)
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/analysis/ancestry/lai/status')
    })
    expect(
      screen.queryByText(/LAI coverage degraded for AncestryDNA/),
    ).not.toBeInTheDocument()
  })

  it('does not advertise a bundle update while production policy is unavailable', async () => {
    setupAppUpdateMock(UP_TO_DATE, {
      bundle_downloaded: true,
      java_available: true,
      lai_available: false,
      coverage_policy_available: false,
      message: 'Chromosome painting is unavailable.',
      insufficient_data_reason: {
        message: 'No final-confirmed minimum-coverage policy exists.',
      },
      degraded_coverage: true,
    })
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/analysis/ancestry/lai/status')
    })
    expect(
      screen.queryByText(/LAI coverage degraded for AncestryDNA/),
    ).not.toBeInTheDocument()
  })

  it('does not render when degraded_coverage is omitted from the payload (23andMe-only install)', async () => {
    // Plan §6.7 negative case — payload mirrors a 23andMe-only install where
    // the field is absent or explicitly false.
    setupAppUpdateMock(UP_TO_DATE, {
      bundle_downloaded: true,
      java_available: true,
      lai_available: true,
      message: 'Chromosome painting is available.',
    })
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/analysis/ancestry/lai/status')
    })
    expect(
      screen.queryByText(/LAI coverage degraded for AncestryDNA/),
    ).not.toBeInTheDocument()
  })

  it('dismissal persists in localStorage and survives remount', async () => {
    setupAppUpdateMock(UP_TO_DATE, LAI_STATUS_DEGRADED)
    const { unmount } = render(<AppUpdateBanner />, { wrapper: createWrapper() })

    const dismissBtn = await screen.findByRole('button', {
      name: /dismiss lai coverage notification/i,
    })
    fireEvent.click(dismissBtn)

    await waitFor(() => {
      expect(window.localStorage.getItem('laiDegradedCoverageDismissed')).toBe('1')
    })
    expect(
      screen.queryByText(/LAI coverage degraded for AncestryDNA/),
    ).not.toBeInTheDocument()

    unmount()
    render(<AppUpdateBanner />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/analysis/ancestry/lai/status')
    })
    expect(
      screen.queryByText(/LAI coverage degraded for AncestryDNA/),
    ).not.toBeInTheDocument()
  })

  it('renders alongside the app-update banner when both flags are set', async () => {
    setupAppUpdateMock(UPDATE_AVAILABLE, LAI_STATUS_DEGRADED)
    render(<AppUpdateBanner />, { wrapper: createWrapper() })

    expect(
      await screen.findByText(/Yeliztli v1\.2\.0 is available/),
    ).toBeInTheDocument()
    expect(
      await screen.findByText(/LAI coverage degraded for AncestryDNA/),
    ).toBeInTheDocument()
  })
})
