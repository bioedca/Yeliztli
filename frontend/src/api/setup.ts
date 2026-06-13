/** API hooks for the setup wizard. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type {
  AcceptDisclaimerResult,
  BundleGatePayload,
  BundleVersionMismatchPayload,
  CredentialsData,
  DatabaseListResult,
  DetectExistingResult,
  DisclaimerData,
  ImportBackupResult,
  IngestResult,
  SaveCredentialsResult,
  SetStoragePathResult,
  SetupStatus,
  StorageInfoResult,
  TriggerDownloadResult,
} from '@/types/setup'

/**
 * Error raised when `/api/ingest` returns HTTP 409 with the §5.4 bundle-gate
 * payload (AncestryDNA upload + installed VEP bundle < v2.0.0). Carries the
 * structured payload so the Upload step can render its one-click update CTA.
 */
export class BundleGateError extends Error {
  readonly status = 409
  readonly payload: BundleGatePayload

  constructor(payload: BundleGatePayload) {
    super('bundle_version_too_old')
    this.name = 'BundleGateError'
    this.payload = payload
  }
}

export function isBundleGatePayload(value: unknown): value is BundleGatePayload {
  if (!value || typeof value !== 'object') return false
  const obj = value as Record<string, unknown>
  return (
    obj.error === 'bundle_version_too_old' &&
    typeof obj.installed_version === 'string' &&
    typeof obj.required_version === 'string' &&
    obj.vendor === 'ancestrydna' &&
    typeof obj.update_url === 'string'
  )
}

/**
 * Error raised when `/api/setup/import-backup` returns HTTP 409 with the
 * §7.6 mismatch payload. Carries enough context for the wizard's
 * RestoreStep banner to render both versions.
 */
export class BundleVersionMismatchError extends Error {
  readonly status = 409
  readonly payload: BundleVersionMismatchPayload

  constructor(payload: BundleVersionMismatchPayload) {
    super('bundle_version_mismatch')
    this.name = 'BundleVersionMismatchError'
    this.payload = payload
  }
}

function isBundleVersionMismatchPayload(
  value: unknown,
): value is BundleVersionMismatchPayload {
  if (!value || typeof value !== 'object') return false
  const obj = value as Record<string, unknown>
  return (
    obj.error === 'bundle_version_mismatch' &&
    typeof obj.installed_version === 'string' &&
    typeof obj.backup_version === 'string' &&
    (obj.direction === 'backup_below_installed' ||
      obj.direction === 'backup_above_installed')
  )
}

export const SETUP_STATUS_KEY = ['setup', 'status'] as const
const DISCLAIMER_KEY = ['setup', 'disclaimer'] as const
const DETECT_EXISTING_KEY = ['setup', 'detect-existing'] as const

async function fetchSetupStatus(): Promise<SetupStatus> {
  const res = await fetch('/api/setup/status')
  if (!res.ok) throw new Error(`Setup status failed: ${res.status}`)
  return res.json()
}

async function fetchDisclaimer(): Promise<DisclaimerData> {
  const res = await fetch('/api/setup/disclaimer')
  if (!res.ok) throw new Error(`Disclaimer fetch failed: ${res.status}`)
  return res.json()
}

async function postAcceptDisclaimer(): Promise<AcceptDisclaimerResult> {
  const res = await fetch('/api/setup/accept-disclaimer', { method: 'POST' })
  if (!res.ok) throw new Error(`Accept disclaimer failed: ${res.status}`)
  return res.json()
}

export function useSetupStatus() {
  return useQuery({
    queryKey: SETUP_STATUS_KEY,
    queryFn: fetchSetupStatus,
    // Short staleTime so AuthGuard reflects live DB health quickly — a DB that
    // goes corrupt/partial (or finishes downloading) flips needs_setup within
    // seconds rather than admitting/trapping the user for up to a minute.
    staleTime: 5_000,
  })
}

export function useDisclaimer() {
  return useQuery({
    queryKey: DISCLAIMER_KEY,
    queryFn: fetchDisclaimer,
    staleTime: Infinity,
  })
}

export function useAcceptDisclaimer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: postAcceptDisclaimer,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SETUP_STATUS_KEY })
    },
  })
}

// ── P1-19b: Import from backup ──────────────────────────────────

async function fetchDetectExisting(): Promise<DetectExistingResult> {
  const res = await fetch('/api/setup/detect-existing')
  if (!res.ok) throw new Error(`Detect existing failed: ${res.status}`)
  return res.json()
}

async function postImportBackup(file: File): Promise<ImportBackupResult> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/setup/import-backup', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    if (res.status === 409 && isBundleVersionMismatchPayload(body?.detail)) {
      throw new BundleVersionMismatchError(
        body.detail as BundleVersionMismatchPayload,
      )
    }
    const detail =
      (typeof body?.detail === 'string' ? body.detail : null) ||
      `Import failed: ${res.status}`
    throw new Error(detail)
  }
  return res.json()
}

export function useDetectExisting() {
  return useQuery({
    queryKey: DETECT_EXISTING_KEY,
    queryFn: fetchDetectExisting,
    staleTime: 0,
  })
}

export function useImportBackup() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: postImportBackup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SETUP_STATUS_KEY })
      queryClient.invalidateQueries({ queryKey: DETECT_EXISTING_KEY })
    },
  })
}

// ── P1-19c: Storage path + disk space ──────────────────────────

const STORAGE_INFO_KEY = ['setup', 'storage-info'] as const

async function fetchStorageInfo(): Promise<StorageInfoResult> {
  const res = await fetch('/api/setup/storage-info')
  if (!res.ok) throw new Error(`Storage info failed: ${res.status}`)
  return res.json()
}

async function postSetStoragePath(path: string): Promise<SetStoragePathResult> {
  const res = await fetch('/api/setup/set-storage-path', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail || `Set storage path failed: ${res.status}`
    throw new Error(detail)
  }
  return res.json()
}

export function useStorageInfo() {
  return useQuery({
    queryKey: STORAGE_INFO_KEY,
    queryFn: fetchStorageInfo,
    staleTime: 0,
  })
}

export function useSetStoragePath() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: postSetStoragePath,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: STORAGE_INFO_KEY })
      queryClient.invalidateQueries({ queryKey: SETUP_STATUS_KEY })
    },
  })
}

// ── P1-19e: External service credentials ────────────────────────

const CREDENTIALS_KEY = ['setup', 'credentials'] as const

async function fetchCredentials(): Promise<CredentialsData> {
  const res = await fetch('/api/setup/credentials')
  if (!res.ok) throw new Error(`Credentials fetch failed: ${res.status}`)
  return res.json()
}

async function postSaveCredentials(data: CredentialsData): Promise<SaveCredentialsResult> {
  const res = await fetch('/api/setup/credentials', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail || `Save credentials failed: ${res.status}`
    throw new Error(detail)
  }
  return res.json()
}

export function useCredentials() {
  return useQuery({
    queryKey: CREDENTIALS_KEY,
    queryFn: fetchCredentials,
    staleTime: 0,
  })
}

export function useSaveCredentials() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: postSaveCredentials,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CREDENTIALS_KEY })
    },
  })
}

// ── P1-19f: Download databases ──────────────────────────────────

export const DATABASE_LIST_KEY = ['setup', 'databases'] as const

async function fetchDatabaseList(): Promise<DatabaseListResult> {
  const res = await fetch('/api/databases')
  if (!res.ok) throw new Error(`Database list failed: ${res.status}`)
  return res.json()
}

async function postTriggerDownload(
  databases?: string[],
): Promise<TriggerDownloadResult> {
  const res = await fetch('/api/databases/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ databases: databases ?? null }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail || `Download trigger failed: ${res.status}`
    throw new Error(detail)
  }
  return res.json()
}

export function useDatabaseList() {
  return useQuery({
    queryKey: DATABASE_LIST_KEY,
    queryFn: fetchDatabaseList,
    staleTime: 0,
  })
}

// Note: Query invalidation is handled by DatabasesStep after SSE progress
// completes, rather than on mutation success, to reflect actual download state.
export function useTriggerDownload() {
  return useMutation({
    mutationFn: postTriggerDownload,
  })
}

// ── P1-19g: Upload sample file ──────────────────────────────────

async function postIngestFile(file: File): Promise<IngestResult> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/ingest', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    if (res.status === 409 && isBundleGatePayload(body?.detail)) {
      throw new BundleGateError(body.detail as BundleGatePayload)
    }
    const detail =
      (typeof body?.detail === 'string' ? body.detail : null) ||
      `Upload failed: ${res.status}`
    throw new Error(detail)
  }
  return res.json()
}

export function useIngestFile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: postIngestFile,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SETUP_STATUS_KEY })
    },
  })
}
