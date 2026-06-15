/** API hooks for authentication (P4-21a). */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

// ── Types ────────────────────────────────────────────────────────────

interface AuthStatus {
  auth_enabled: boolean
  has_password: boolean
  authenticated: boolean
}

interface LoginResponse {
  success: boolean
  message: string
}

// ── Query keys ───────────────────────────────────────────────────────

const AUTH_STATUS_KEY = ['auth', 'status'] as const

// ── Fetch functions ──────────────────────────────────────────────────

async function fetchAuthStatus(): Promise<AuthStatus> {
  const res = await fetch('/api/auth/status', { credentials: 'include' })
  if (!res.ok) throw new Error(`Auth status failed: ${res.status}`)
  return res.json()
}

async function postLogin(password: string): Promise<LoginResponse> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ password }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `Login failed: ${res.status}`)
  }
  return res.json()
}

// ── Hooks ────────────────────────────────────────────────────────────

export function useAuthStatus() {
  return useQuery({
    queryKey: AUTH_STATUS_KEY,
    queryFn: fetchAuthStatus,
    staleTime: 0,
    retry: false,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: postLogin,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTH_STATUS_KEY })
    },
  })
}

