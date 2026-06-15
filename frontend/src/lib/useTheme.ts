/** Theme hook — manages Light/Dark/System toggle with backend persistence (P4-26a).
 *
 * Reads initial value from localStorage (instant) and syncs with backend config.toml.
 * Applies .dark class on <html> element. Listens for OS preference changes in system mode.
 */

import { useState, useEffect, useCallback } from 'react'
import type { Theme } from '@/api/preferences'
import { useSetThemePreference } from '@/api/preferences'

const STORAGE_KEY = 'gi-theme'

function getSystemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function resolveTheme(theme: Theme): 'light' | 'dark' {
  return theme === 'system' ? getSystemTheme() : theme
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', resolveTheme(theme) === 'dark')
}

function getStoredTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getStoredTheme)
  const setThemeMutation = useSetThemePreference()

  // Apply theme to DOM and localStorage whenever it changes
  useEffect(() => {
    applyTheme(theme)
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  // Listen for OS preference changes when in system mode
  useEffect(() => {
    if (theme !== 'system') return
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = () => applyTheme('system')
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [theme])

  const setTheme = useCallback(
    (newTheme: Theme) => {
      setThemeState(newTheme)
      // Fire-and-forget persist to backend
      setThemeMutation.mutate(newTheme)
    },
    [setThemeMutation],
  )

  const cycleTheme = useCallback(() => {
    const order: Theme[] = ['light', 'dark', 'system']
    setTheme(order[(order.indexOf(theme) + 1) % 3])
  }, [theme, setTheme])

  /** The resolved theme (never 'system', always 'light' or 'dark'). */
  const resolvedTheme = resolveTheme(theme)

  return { theme, resolvedTheme, setTheme, cycleTheme }
}
