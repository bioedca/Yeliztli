/** API hooks for backup/restore (P4-21c). */

import { useQuery, useMutation } from '@tanstack/react-query'
import { throwApiError } from '@/api/errors'

// ── Types ────────────────────────────────────────────────────────────

interface BackupEstimate {
  sample_bytes: number
  config_bytes: number
  reference_bytes: number
  total_without_ref_bytes: number
  total_with_ref_bytes: number
  total_without_ref_mb: number
  total_with_ref_mb: number
  sample_count: number
  reference_db_count: number
}

export interface BackupExportResult {
  job_id: string
  message: string
}

export interface BackupStatus {
  job_id: string
  status: 'pending' | 'running' | 'complete' | 'failed'
  progress_pct: number
  message: string
  error: string | null
  download_filename: string | null
}

// ── Query keys ───────────────────────────────────────────────────────

const BACKUP_ESTIMATE_KEY = ['backup', 'estimate'] as const

// ── Fetch functions ─────────────────────────────────────────────────

async function fetchBackupEstimate(): Promise<BackupEstimate> {
  const res = await fetch('/api/backup/estimate')
  if (!res.ok) await throwApiError(res, 'Unable to load the backup estimate. Please try again.')
  return res.json()
}

async function startBackupExport(includeReferenceDbs: boolean): Promise<BackupExportResult> {
  const res = await fetch('/api/backup/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ include_reference_dbs: includeReferenceDbs }),
  })
  if (!res.ok) await throwApiError(res, 'Unable to start the backup. Please try again.')
  return res.json()
}

async function fetchBackupStatus(jobId: string): Promise<BackupStatus> {
  const res = await fetch(`/api/backup/status/${jobId}`)
  if (!res.ok) await throwApiError(res, 'Unable to load backup status. Please try again.')
  return res.json()
}

// ── Hooks ───────────────────────────────────────────────────────────

export function useBackupEstimate() {
  return useQuery({
    queryKey: BACKUP_ESTIMATE_KEY,
    queryFn: fetchBackupEstimate,
    staleTime: 30 * 1000,
  })
}

export function useStartBackupExport() {
  return useMutation({
    mutationFn: (includeReferenceDbs: boolean) => startBackupExport(includeReferenceDbs),
  })
}

export function useBackupStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['backup', 'status', jobId] as const,
    queryFn: () => fetchBackupStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === 'complete' || status === 'failed') return false
      return 1000
    },
  })
}
