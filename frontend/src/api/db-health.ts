/** API hooks for reference-database health, integrity, resume, and cleanup.
 *
 * Backs the Settings → System Health "Database Health" panel, which gives
 * 100% observability of every reference DB: derived state, readability
 * (integrity), resumable partials, and recovery actions.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

// ── Types ────────────────────────────────────────────────────────────

/** Derived lifecycle state for a single reference database. */
export type DatabaseHealthState =
  | 'not_installed'
  | 'downloading'
  | 'building'
  | 'partial'
  | 'corrupt'
  | 'ready'
  | 'failed'

export interface DatabaseHealth {
  name: string
  display_name: string
  build_mode: string
  required: boolean
  state: DatabaseHealthState
  present: boolean
  version: string | null
  downloaded_at: string | null
  file_size_bytes: number | null
  expected_size_bytes: number
  integrity_ok: boolean | null
  integrity_detail: string | null
  resumable: boolean
  download_id: number | null
  downloaded_bytes: number | null
  total_bytes: number | null
  progress_pct: number | null
  active_job_id: string | null
  last_error: string | null
  can_clean: boolean
  can_resume: boolean
  can_verify: boolean
}

interface DatabaseHealthList {
  databases: DatabaseHealth[]
}

export interface ResumeResponse {
  session_id: string
  downloads: { db_name: string; job_id: string }[]
}

export interface VerifyResponse {
  db_name: string
  ok: boolean
  detail: string
  depth: string
}

export interface CleanResponse {
  db_name: string
  removed: string[]
}

// ── Query keys ───────────────────────────────────────────────────────

export const DB_HEALTH_KEY = ['databases', 'health'] as const

// ── Fetchers ─────────────────────────────────────────────────────────

async function fetchDatabaseHealth(): Promise<DatabaseHealthList> {
  const res = await fetch('/api/databases/health')
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Database health fetch failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function resumeDownload(dbName: string): Promise<ResumeResponse> {
  const res = await fetch('/api/databases/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ db_name: dbName }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Resume failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function verifyDatabase(dbName: string): Promise<VerifyResponse> {
  const res = await fetch(`/api/databases/${dbName}/verify`, { method: 'POST' })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Verify failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function cleanDatabase(dbName: string): Promise<CleanResponse> {
  const res = await fetch(`/api/databases/${dbName}/clean`, { method: 'POST' })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Clean failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

// ── Hooks ────────────────────────────────────────────────────────────

/** States that are still moving — while any DB is in one, poll more often. */
const ACTIVE_STATES: ReadonlySet<DatabaseHealthState> = new Set([
  'downloading',
  'building',
])

/**
 * Whether a database is mid-flight (downloading/building, or with an active
 * job linked) — i.e. some process is already provisioning it. The Update
 * Manager uses this as the single source of truth to keep the "Update now"
 * control from racing an in-progress download/build (a wizard download, an
 * auto-update, or a manual update already running).
 */
export function isDatabaseBusy(health: DatabaseHealth | undefined): boolean {
  return (
    !!health && (ACTIVE_STATES.has(health.state) || health.active_job_id != null)
  )
}

/** A short human label for an active database state (for a disabled control). */
export function busyLabel(health: DatabaseHealth | undefined): string {
  if (health?.state === 'downloading') return 'Downloading…'
  if (health?.state === 'building') return 'Building…'
  return 'In progress…'
}

/**
 * Fetch fused health for all reference databases. Polls every 3s while any
 * database is actively downloading/building so progress stays live, then
 * settles to on-demand once everything reaches a terminal state.
 */
export function useDatabaseHealth(enabled = true) {
  return useQuery({
    queryKey: DB_HEALTH_KEY,
    queryFn: fetchDatabaseHealth,
    enabled,
    staleTime: 10 * 1000,
    refetchInterval: (query) => {
      const data = query.state.data as DatabaseHealthList | undefined
      const active = data?.databases.some((d) => ACTIVE_STATES.has(d.state))
      return active ? 3000 : false
    },
  })
}

/** Resume an interrupted download. Invalidates health on success. */
export function useResumeDownload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dbName: string) => resumeDownload(dbName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: DB_HEALTH_KEY })
    },
  })
}

/** Run a deep integrity check. Invalidates health on success. */
export function useVerifyDatabase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dbName: string) => verifyDatabase(dbName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: DB_HEALTH_KEY })
    },
  })
}

/** Remove a partial/corrupt artifact. Invalidates health on success. */
export function useCleanDatabase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (dbName: string) => cleanDatabase(dbName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: DB_HEALTH_KEY })
    },
  })
}
