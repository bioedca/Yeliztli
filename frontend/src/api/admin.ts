/** API hooks for admin panel / System Health (P4-21b). */

import { useQuery } from '@tanstack/react-query'

// ── Types ────────────────────────────────────────────────────────────

export interface LogEntry {
  id: number
  timestamp: string | null
  level: string
  logger: string | null
  message: string | null
  event_data: string | null
}

export interface LogResponse {
  entries: LogEntry[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface DatabaseStat {
  name: string
  display_name: string
  file_path: string | null
  file_size_bytes: number | null
  exists: boolean
  row_count: number | null
  last_updated: string | null
  version: string | null
}

interface SampleStat {
  sample_id: number
  name: string
  db_path: string
  file_size_bytes: number | null
  exists: boolean
}

interface DiskUsage {
  data_dir: string
  total_bytes: number
  free_bytes: number
  used_bytes: number
  reference_dbs_bytes: number
  sample_dbs_bytes: number
  logs_bytes: number
  other_bytes: number
}

interface ActiveJob {
  job_id: string
  job_type: string
  status: string
  progress_pct: number | null
  message: string | null
  created_at: string | null
}

interface SystemStatus {
  version: string
  uptime_seconds: number
  data_dir: string
  active_jobs: ActiveJob[]
  total_samples: number
  auth_enabled: boolean
  log_level: string
}

export interface LogFilters {
  page?: number
  page_size?: number
  level?: string
  component?: string
  since?: string
  until?: string
  search?: string
}

// ── Query keys ───────────────────────────────────────────────────────

const ADMIN_LOGS_KEY = ['admin', 'logs'] as const
const ADMIN_DB_STATS_KEY = ['admin', 'db-stats'] as const
const ADMIN_SAMPLE_STATS_KEY = ['admin', 'sample-stats'] as const
const ADMIN_DISK_USAGE_KEY = ['admin', 'disk-usage'] as const
const ADMIN_STATUS_KEY = ['admin', 'status'] as const

// ── Fetch functions ─────────────────────────────────────────────────

async function fetchLogs(filters: LogFilters): Promise<LogResponse> {
  const params = new URLSearchParams()
  if (filters.page) params.set('page', String(filters.page))
  if (filters.page_size) params.set('page_size', String(filters.page_size))
  if (filters.level) params.set('level', filters.level)
  if (filters.component) params.set('component', filters.component)
  if (filters.since) params.set('since', filters.since)
  if (filters.until) params.set('until', filters.until)
  if (filters.search) params.set('search', filters.search)
  const res = await fetch(`/api/admin/logs?${params}`)
  if (!res.ok) throw new Error(`Failed to fetch logs: ${res.status}`)
  return res.json()
}

async function fetchDbStats(): Promise<DatabaseStat[]> {
  const res = await fetch('/api/admin/db-stats')
  if (!res.ok) throw new Error(`Failed to fetch DB stats: ${res.status}`)
  return res.json()
}

async function fetchSampleStats(): Promise<SampleStat[]> {
  const res = await fetch('/api/admin/sample-stats')
  if (!res.ok) throw new Error(`Failed to fetch sample stats: ${res.status}`)
  return res.json()
}

async function fetchDiskUsage(): Promise<DiskUsage> {
  const res = await fetch('/api/admin/disk-usage')
  if (!res.ok) throw new Error(`Failed to fetch disk usage: ${res.status}`)
  return res.json()
}

async function fetchSystemStatus(): Promise<SystemStatus> {
  const res = await fetch('/api/admin/status')
  if (!res.ok) throw new Error(`Failed to fetch system status: ${res.status}`)
  return res.json()
}

// ── Hooks ───────────────────────────────────────────────────────────

export function useLogs(filters: LogFilters) {
  return useQuery({
    queryKey: [...ADMIN_LOGS_KEY, filters] as const,
    queryFn: () => fetchLogs(filters),
    staleTime: 0,
  })
}

export function useDbStats() {
  return useQuery({
    queryKey: ADMIN_DB_STATS_KEY,
    queryFn: fetchDbStats,
    staleTime: 60 * 1000,
  })
}

export function useSampleStats() {
  return useQuery({
    queryKey: ADMIN_SAMPLE_STATS_KEY,
    queryFn: fetchSampleStats,
    staleTime: 60 * 1000,
  })
}

export function useDiskUsage() {
  return useQuery({
    queryKey: ADMIN_DISK_USAGE_KEY,
    queryFn: fetchDiskUsage,
    staleTime: 60 * 1000,
  })
}

export function useSystemStatus() {
  return useQuery({
    queryKey: ADMIN_STATUS_KEY,
    queryFn: fetchSystemStatus,
    staleTime: 0,
  })
}
