/** API hooks for user preferences (P4-26a). */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

export type Theme = 'light' | 'dark' | 'system'

interface ThemeResponse {
  theme: Theme
}

/** Persist theme preference to backend config.toml. */
export function useSetThemePreference() {
  const qc = useQueryClient()
  return useMutation<ThemeResponse, Error, Theme>({
    mutationFn: async (theme) => {
      const res = await fetch('/api/preferences/theme', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme }),
      })
      if (!res.ok) throw new Error('Failed to set theme')
      return res.json()
    },
    onSuccess: (data) => {
      qc.setQueryData(['preferences', 'theme'], data)
    },
  })
}

// ── Automatic update-check interval (#1287) ──────────────────────────

export type UpdateCheckInterval = 'off' | 'startup' | 'daily' | 'weekly'

interface UpdateCheckIntervalResponse {
  update_check_interval: UpdateCheckInterval
}

const UPDATE_CHECK_INTERVAL_KEY = ['preferences', 'update-check-interval'] as const

/** Read the automatic-update-check interval. ``"off"`` means the dashboard's
 * update/version hooks should be disabled (#1287). */
export function useUpdateCheckInterval() {
  return useQuery<UpdateCheckIntervalResponse>({
    queryKey: UPDATE_CHECK_INTERVAL_KEY,
    queryFn: async () => {
      const res = await fetch('/api/preferences/update-check-interval')
      if (!res.ok) throw new Error('Failed to fetch update-check interval')
      return res.json()
    },
    staleTime: 60 * 60 * 1000, // 1 hour
  })
}

/** Persist the automatic-update-check interval to backend config.toml. */
export function useSetUpdateCheckInterval() {
  const qc = useQueryClient()
  return useMutation<UpdateCheckIntervalResponse, Error, UpdateCheckInterval>({
    mutationFn: async (interval) => {
      const res = await fetch('/api/preferences/update-check-interval', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ update_check_interval: interval }),
      })
      if (!res.ok) throw new Error('Failed to set update-check interval')
      return res.json()
    },
    onSuccess: (data) => {
      qc.setQueryData(UPDATE_CHECK_INTERVAL_KEY, data)
    },
  })
}
