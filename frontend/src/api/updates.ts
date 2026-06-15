/** API hooks for database update status, history, and triggers (P4-17, P4-18). */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

// ── Types ────────────────────────────────────────────────────────────

export interface DatabaseStatus {
  db_name: string
  display_name: string
  current_version: string | null
  version_display: string | null
  downloaded_at: string | null
  file_size_bytes: number | null
  auto_update: boolean
  update_available: boolean
  /**
   * Bandwidth window for large updates (e.g. `"02:00-06:00"`) or `null`
   * when no window is configured. Mirrors `settings.update_download_window`
   * server-side; surfaced so the Update Manager can show an outside-window
   * tooltip on the "Update now" button. Optional — older backends omit it.
   */
  update_download_window?: string | null
}

export interface UpdateAvailable {
  db_name: string
  latest_version: string
  download_size_bytes: number
  release_date: string | null
}

interface UpdateCheckResult {
  available: UpdateAvailable[]
  up_to_date: string[]
  errors: string[]
  checked_at: string
}

export interface UpdateHistoryEntry {
  id: number
  db_name: string
  previous_version: string | null
  new_version: string
  updated_at: string | null
  variants_added: number | null
  variants_reclassified: number | null
  download_size_bytes: number | null
  duration_seconds: number | null
}

export interface ReannotationPrompt {
  id: number
  sample_id: number
  db_name: string
  db_version: string
  candidate_count: number
  created_at: string | null
}

// ── Finding-level change diff (SW-A4b) ───────────────────────────────

interface FindingFieldChange {
  field: string
  before: string | null
  after: string | null
}

interface ReleaseDelta {
  db_name: string
  before: string | null
  after: string | null
}

interface ChangedFinding {
  module: string
  category: string | null
  gene_symbol: string | null
  rsid: string | null
  drug: string | null
  diplotype: string | null
  finding_text: string
  changes: FindingFieldChange[]
}

interface DiffFinding {
  module: string
  category: string | null
  gene_symbol: string | null
  rsid: string | null
  drug: string | null
  diplotype: string | null
  finding_text: string
  clinvar_significance: string | null
  evidence_level: number | null
  metabolizer_status: string | null
  pathway_level: string | null
}

export interface FindingChanges {
  available: boolean
  generated_at: string | null
  release_deltas: ReleaseDelta[]
  changed: ChangedFinding[]
  added: DiffFinding[]
  removed: DiffFinding[]
  counts: Record<string, number>
}

export interface TriggerUpdateResponse {
  job_id: string
  db_name: string
  message: string
}

export interface JobStatus {
  job_id: string
  status: string
  progress_pct: number
  message: string
  error: string | null
}

interface AppUpdateInfo {
  update_available: boolean
  current_version: string
  latest_version: string | null
  release_url: string | null
  release_notes: string | null
  error: string | null
}

// ── Query keys ───────────────────────────────────────────────────────

export const DB_STATUS_KEY = ['updates', 'status'] as const
const UPDATE_CHECK_KEY = ['updates', 'check'] as const
const UPDATE_HISTORY_KEY = ['updates', 'history'] as const
const REANNOTATION_PROMPTS_KEY = ['updates', 'prompts'] as const
const APP_UPDATE_KEY = ['updates', 'app'] as const
const FINDING_CHANGES_KEY = ['updates', 'finding-changes'] as const

// ── Fetchers ─────────────────────────────────────────────────────────

async function fetchDatabaseStatuses(): Promise<DatabaseStatus[]> {
  const res = await fetch('/api/updates/status')
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Database status fetch failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function fetchUpdateCheck(): Promise<UpdateCheckResult> {
  const res = await fetch('/api/updates/check')
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Update check failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function fetchUpdateHistory(dbName?: string, limit = 50): Promise<UpdateHistoryEntry[]> {
  const params = new URLSearchParams()
  if (dbName) params.set('db_name', dbName)
  params.set('limit', String(limit))
  const res = await fetch(`/api/updates/history?${params}`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Update history fetch failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function fetchReannotationPrompts(sampleId?: number): Promise<ReannotationPrompt[]> {
  const params = new URLSearchParams()
  if (sampleId != null) params.set('sample_id', String(sampleId))
  const res = await fetch(`/api/updates/prompts?${params}`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Prompts fetch failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function triggerUpdate(
  dbName: string,
  force = false,
): Promise<TriggerUpdateResponse> {
  // Only include `force` when truthy so legacy backends that don't know
  // the field still see the original payload shape.
  const body: Record<string, unknown> = { db_name: dbName }
  if (force) body.force = true
  const res = await fetch('/api/updates/trigger', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Trigger update failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function dismissPrompt(promptId: number): Promise<void> {
  const res = await fetch(`/api/updates/prompts/${promptId}/dismiss`, { method: 'POST' })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Dismiss prompt failed: ${res.status} ${text}`.trim())
  }
}

async function fetchFindingChanges(sampleId: number): Promise<FindingChanges> {
  const res = await fetch(`/api/updates/finding-changes?sample_id=${sampleId}`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Finding-changes fetch failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function dismissFindingChanges(sampleId: number): Promise<void> {
  const res = await fetch(
    `/api/updates/finding-changes/dismiss?sample_id=${sampleId}`,
    { method: 'POST' },
  )
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Dismiss finding-changes failed: ${res.status} ${text}`.trim())
  }
}

async function toggleAutoUpdate(dbName: string, enabled: boolean): Promise<void> {
  const res = await fetch('/api/updates/auto-update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ db_name: dbName, enabled }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Toggle auto-update failed: ${res.status} ${text}`.trim())
  }
}

async function fetchJobStatus(jobId: string): Promise<JobStatus> {
  const res = await fetch(`/api/updates/job/${jobId}`)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`Job status fetch failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

async function pollJobUntilDone(
  jobId: string,
  intervalMs = 2000,
  maxDurationMs = 10 * 60 * 1000, // 10 minutes
  signal?: AbortSignal,
): Promise<JobStatus> {
  const startTime = Date.now()
  while (true) {
    if (signal?.aborted) {
      throw new DOMException('Polling aborted', 'AbortError')
    }
    if (Date.now() - startTime > maxDurationMs) {
      throw new Error(`Job ${jobId} did not complete within ${maxDurationMs / 1000}s`)
    }
    const status = await fetchJobStatus(jobId)
    if (status.status === 'complete' || status.status === 'failed' || status.status === 'cancelled') {
      return status
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

async function fetchAppUpdate(): Promise<AppUpdateInfo> {
  const res = await fetch('/api/updates/app-update')
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`App update check failed: ${res.status} ${text}`.trim())
  }
  return res.json()
}

// ── Hooks ────────────────────────────────────────────────────────────

/** Fetch per-DB version stamps and auto-update status.
 *
 * staleTime is short (30s): the previous 1-hour cache let the Update Manager
 * keep showing a stale version/"update available" long after a download/build
 * changed it. The Update Manager also invalidates DB_STATUS_KEY when a DB's
 * health transitions out of an active state, so the stamp refreshes promptly
 * once a build/download finishes. */
export function useDatabaseStatuses() {
  return useQuery({
    queryKey: DB_STATUS_KEY,
    queryFn: fetchDatabaseStatuses,
    staleTime: 30 * 1000, // 30 seconds
  })
}

/** Check for app updates via GitHub Releases. staleTime=1h. */
export function useAppUpdate(enabled = true) {
  return useQuery({
    queryKey: APP_UPDATE_KEY,
    queryFn: fetchAppUpdate,
    staleTime: 60 * 60 * 1000, // 1 hour
    enabled,
    retry: false, // Don't retry on network failure — non-critical
  })
}

/** Check for available updates (hits remote). staleTime=1h. */
export function useUpdateCheck(enabled = false) {
  return useQuery({
    queryKey: UPDATE_CHECK_KEY,
    queryFn: fetchUpdateCheck,
    staleTime: 60 * 60 * 1000,
    enabled,
  })
}

/** Fetch update history, optionally filtered by db_name. */
export function useUpdateHistory(dbName?: string) {
  return useQuery({
    queryKey: [...UPDATE_HISTORY_KEY, dbName ?? 'all'],
    queryFn: () => fetchUpdateHistory(dbName),
    staleTime: 60 * 60 * 1000,
  })
}

/** Fetch active re-annotation prompts. */
export function useReannotationPrompts(sampleId?: number) {
  return useQuery({
    queryKey: [...REANNOTATION_PROMPTS_KEY, sampleId ?? 'all'],
    queryFn: () => fetchReannotationPrompts(sampleId),
    staleTime: 5 * 60 * 1000, // 5 min
  })
}

export interface TriggerUpdateVariables {
  dbName: string
  /** Bypass the bandwidth-window check (Force update). Off by default. */
  force?: boolean
}

/** Trigger a database update. Polls for job completion, then invalidates caches. */
export function useTriggerUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ dbName, force = false }: TriggerUpdateVariables) => {
      const resp = await triggerUpdate(dbName, force)
      // Poll until the background Huey task finishes
      await pollJobUntilDone(resp.job_id)
      return resp
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: DB_STATUS_KEY })
      qc.invalidateQueries({ queryKey: UPDATE_CHECK_KEY })
      qc.invalidateQueries({ queryKey: UPDATE_HISTORY_KEY })
    },
  })
}

/** Dismiss a re-annotation prompt. Invalidates prompts cache on success. */
export function useDismissPrompt() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (promptId: number) => dismissPrompt(promptId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: REANNOTATION_PROMPTS_KEY })
    },
  })
}

/** Fetch the finding-level change diff for a sample (SW-A4b). Disabled until a
 * sample id is known. */
export function useFindingChanges(sampleId: number | null | undefined) {
  return useQuery({
    queryKey: [...FINDING_CHANGES_KEY, sampleId ?? 'none'],
    queryFn: () => fetchFindingChanges(sampleId as number),
    enabled: sampleId != null,
    staleTime: 5 * 60 * 1000, // 5 min
  })
}

/** Dismiss a sample's stored finding-change diff. Invalidates the diff cache. */
export function useDismissFindingChanges() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (sampleId: number) => dismissFindingChanges(sampleId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FINDING_CHANGES_KEY })
    },
  })
}

/** Toggle auto-update for a database. Invalidates status cache on success. */
export function useToggleAutoUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ dbName, enabled }: { dbName: string; enabled: boolean }) =>
      toggleAutoUpdate(dbName, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: DB_STATUS_KEY })
    },
  })
}
