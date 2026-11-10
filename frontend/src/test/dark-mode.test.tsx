/** Dark mode toggle tests (P4-26a / T4-30).
 *
 * Verifies:
 * - Three-way toggle cycles Light → Dark → System
 * - .dark class applied/removed correctly on <html>
 * - Theme persisted to localStorage
 * - System mode respects OS preference
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from './test-utils'
import TopNav from '@/components/layout/TopNav'

// Mock fetch for the preferences API
const fetchMock = vi.fn((url: RequestInfo | URL) =>
  Promise.resolve({
    ok: true,
    json: () => Promise.resolve(
      url === '/api/individuals' || url === '/api/samples' ? [] : { theme: 'system' },
    ),
  }),
) as unknown as typeof fetch

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  ;(fetchMock as ReturnType<typeof vi.fn>).mockClear()
  localStorage.clear()
  document.documentElement.classList.remove('dark')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function stubStorageMethodFailure(method: 'getItem' | 'setItem', name: string) {
  const storage = localStorage
  const unavailable = vi.fn(() => {
    throw new DOMException('Theme storage is unavailable', name)
  })
  vi.stubGlobal('localStorage', {
    getItem: storage.getItem.bind(storage),
    setItem: storage.setItem.bind(storage),
    [method]: unavailable,
  })
  return unavailable
}

describe('Dark mode toggle (T4-30)', () => {
  it('carries the active sample through the logo link', () => {
    render(<TopNav />, { route: '/cancer?sample_id=7&profile=clinical' })
    expect(screen.getByRole('link', { name: 'Yeliztli' })).toHaveAttribute(
      'href',
      '/?sample_id=7',
    )
  })

  it('renders theme toggle button', () => {
    render(<TopNav />)
    expect(screen.getByTestId('theme-toggle')).toBeInTheDocument()
  })

  it('defaults to system theme', () => {
    render(<TopNav />)
    const btn = screen.getByTestId('theme-toggle')
    expect(btn).toHaveAttribute('aria-label', 'Theme: system')
  })

  it('falls back to system for an invalid stored theme', () => {
    localStorage.setItem('gi-theme', 'invalid')
    render(<TopNav />)
    expect(screen.getByTestId('theme-toggle')).toHaveAttribute('aria-label', 'Theme: system')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(localStorage.getItem('gi-theme')).toBe('system')
  })

  it.each(['SecurityError', 'NS_ERROR_FAILURE'])(
    'renders with the system theme when reading storage throws %s',
    (name) => {
      const getItem = stubStorageMethodFailure('getItem', name)
      const originalMatchMedia = window.matchMedia
      vi.spyOn(window, 'matchMedia').mockImplementation((query) => ({
        ...originalMatchMedia(query),
        matches: true,
      }))

      render(<TopNav />)

      expect(getItem).toHaveBeenCalledWith('gi-theme')
      expect(screen.getByTestId('theme-toggle')).toHaveAttribute('aria-label', 'Theme: system')
      expect(document.documentElement.classList.contains('dark')).toBe(true)
    },
  )

  it.each(['QuotaExceededError', 'SecurityError'])(
    'keeps theme changes and backend persistence when writing storage throws %s',
    async (name) => {
      localStorage.setItem('gi-theme', 'light')
      const setItem = stubStorageMethodFailure('setItem', name)

      render(<TopNav />)
      const btn = screen.getByTestId('theme-toggle')
      expect(btn).toHaveAttribute('aria-label', 'Theme: light')
      expect(setItem).toHaveBeenCalledWith('gi-theme', 'light')

      fireEvent.click(btn)
      expect(btn).toHaveAttribute('aria-label', 'Theme: dark')
      expect(document.documentElement.classList.contains('dark')).toBe(true)
      expect(localStorage.getItem('gi-theme')).toBe('light')
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          '/api/preferences/theme',
          expect.objectContaining({ method: 'PUT', body: JSON.stringify({ theme: 'dark' }) }),
        )
      })

      fireEvent.click(btn)
      expect(btn).toHaveAttribute('aria-label', 'Theme: system')
      expect(document.documentElement.classList.contains('dark')).toBe(false)
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          '/api/preferences/theme',
          expect.objectContaining({ method: 'PUT', body: JSON.stringify({ theme: 'system' }) }),
        )
      })
    },
  )

  it('renders and persists theme changes when accessing localStorage itself is denied', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
    if (!descriptor) throw new Error('Test environment must expose localStorage')
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('Storage access is denied', 'SecurityError')
      },
    })
    try {
      render(<TopNav />)
      const btn = screen.getByTestId('theme-toggle')
      expect(btn).toHaveAttribute('aria-label', 'Theme: system')

      fireEvent.click(btn)
      expect(btn).toHaveAttribute('aria-label', 'Theme: light')
      expect(document.documentElement.classList.contains('dark')).toBe(false)
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          '/api/preferences/theme',
          expect.objectContaining({ method: 'PUT', body: JSON.stringify({ theme: 'light' }) }),
        )
      })
    } finally {
      Object.defineProperty(globalThis, 'localStorage', descriptor)
    }
  })

  it('cycles light → dark → system on click', () => {
    localStorage.setItem('gi-theme', 'light')
    render(<TopNav />)
    const btn = screen.getByTestId('theme-toggle')

    // light → dark
    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-label', 'Theme: dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('gi-theme')).toBe('dark')

    // dark → system
    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-label', 'Theme: system')
    expect(localStorage.getItem('gi-theme')).toBe('system')

    // system → light
    fireEvent.click(btn)
    expect(btn).toHaveAttribute('aria-label', 'Theme: light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(localStorage.getItem('gi-theme')).toBe('light')
  })

  it('applies dark class when set to dark', () => {
    localStorage.setItem('gi-theme', 'dark')
    render(<TopNav />)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('system mode applies .dark when the OS prefers dark', () => {
    const original = window.matchMedia
    window.matchMedia = ((query: string) => ({
      matches: query.includes('dark'), // OS prefers dark
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
    try {
      localStorage.setItem('gi-theme', 'system')
      render(<TopNav />)
      expect(screen.getByTestId('theme-toggle')).toHaveAttribute('aria-label', 'Theme: system')
      expect(document.documentElement.classList.contains('dark')).toBe(true)
    } finally {
      window.matchMedia = original
    }
  })

  it('system mode removes .dark when the OS prefers light', () => {
    const original = window.matchMedia
    window.matchMedia = ((query: string) => ({
      matches: false, // OS prefers light
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
    try {
      document.documentElement.classList.add('dark') // start dirty
      localStorage.setItem('gi-theme', 'system')
      render(<TopNav />)
      expect(screen.getByTestId('theme-toggle')).toHaveAttribute('aria-label', 'Theme: system')
      expect(document.documentElement.classList.contains('dark')).toBe(false)
    } finally {
      window.matchMedia = original
    }
  })

  it('removes dark class when set to light', () => {
    document.documentElement.classList.add('dark')
    localStorage.setItem('gi-theme', 'light')
    render(<TopNav />)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('persists theme to backend via PUT', async () => {
    localStorage.setItem('gi-theme', 'light')
    render(<TopNav />)
    const btn = screen.getByTestId('theme-toggle')

    fireEvent.click(btn)

    // Mutation is fire-and-forget, wait for it to be called
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/preferences/theme',
        expect.objectContaining({
          method: 'PUT',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
          body: JSON.stringify({ theme: 'dark' }),
        }),
      )
    })
  })
})
