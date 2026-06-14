/** Setup wizard types. */

/** How a database is provisioned (matches backend DatabaseInfo.build_mode). */
export type BuildMode = 'pipeline' | 'download' | 'manual' | 'bundled'

/** Health/readiness of one database that gates the dashboard. */
export interface DbReadiness {
  name: string
  /** Mirrors backend DatabaseHealth.state (ready | partial | corrupt | …). */
  state: string
  ready: boolean
  build_mode: BuildMode
}

export interface SetupStatus {
  needs_setup: boolean
  disclaimer_accepted: boolean
  has_databases: boolean
  /** True only when every required, downloadable DB is integrity-`ready`. */
  required_dbs_ready: boolean
  /** Per-DB readiness for the required, downloadable gate set. */
  db_readiness: DbReadiness[]
  has_samples: boolean
  data_dir: string
}

export interface DisclaimerData {
  title: string
  text: string
  accept_label: string
}

export interface AcceptDisclaimerResult {
  accepted: boolean
  accepted_at: string
}

export interface DetectExistingResult {
  existing_found: boolean
  has_config: boolean
  has_samples: boolean
  has_databases: boolean
  data_dir: string
}

export interface ImportBackupResult {
  success: boolean
  samples_restored: number
  config_restored: boolean
  message: string
}

// ── P1-19c: Storage path + disk space ──────────────────────────

export interface StorageInfoResult {
  data_dir: string
  free_space_bytes: number
  free_space_gb: number
  total_space_bytes: number
  total_space_gb: number
  status: 'ok' | 'warning' | 'blocked'
  message: string
  path_exists: boolean
  path_writable: boolean
  // Independent of disk-space `status`: true when the path is on a volatile
  // filesystem (e.g. /tmp) that may be erased on reboot.
  volatile: boolean
  volatile_message: string | null
}

export interface SetStoragePathResult {
  success: boolean
  data_dir: string
  free_space_gb: number
  status: 'ok' | 'warning' | 'blocked'
  message: string
}

// ── P1-19e: External service credentials ────────────────────────

export interface CredentialsData {
  pubmed_email: string
  ncbi_api_key: string
  omim_api_key: string
}

export interface SaveCredentialsResult {
  success: boolean
  message: string
}

// ── P1-19f: Download databases ──────────────────────────────────

export interface DatabaseStatus {
  name: string
  display_name: string
  description: string
  filename: string
  expected_size_bytes: number
  required: boolean
  phase: number
  downloaded: boolean
  file_size_bytes: number | null
  build_mode: BuildMode
}

export interface DatabaseListResult {
  databases: DatabaseStatus[]
  total_size_bytes: number
  downloaded_count: number
  total_count: number
}

export interface DownloadJobInfo {
  db_name: string
  job_id: string
}

export interface TriggerDownloadResult {
  session_id: string
  downloads: DownloadJobInfo[]
}

export interface DatabaseProgressEvent {
  db_name: string
  job_id: string
  status: 'pending' | 'running' | 'complete' | 'failed' | 'unknown'
  progress_pct: number
  message: string
  error: string | null
  /** Known artifact size in bytes, or null when the size is unknown. */
  total_bytes: number | null
  /** Bytes transferred so far (derived from progress_pct × total_bytes). */
  downloaded_bytes: number | null
  /** Smoothed (EWMA) transfer rate in bytes/sec; null when not progressing. */
  speed_bps: number | null
  /** Estimated seconds remaining for this DB; null when unknown. */
  eta_seconds: number | null
}

export interface DownloadAggregateProgress {
  /** Sum of known total_bytes across the session, or null if none are known. */
  total_bytes: number | null
  downloaded_bytes: number
  remaining_bytes: number
  /** Overall percent across sized DBs, or null while no total is known. */
  overall_pct: number | null
  /** Combined transfer rate in bytes/sec; null when nothing is in flight. */
  speed_bps: number | null
  /** Estimated seconds until the whole session completes; null when unknown. */
  eta_seconds: number | null
  /** Number of DBs whose size is unknown (excluded from the byte totals). */
  size_unknown_count: number
}

export interface DownloadProgressData {
  session_id: string
  databases: DatabaseProgressEvent[]
  aggregate: DownloadAggregateProgress
}

// ── P1-19g: Upload sample file ──────────────────────────────────

export interface IngestResult {
  sample_id: number
  job_id: string
  variant_count: number
  nocall_count: number
  file_format: string
}

/**
 * HTTP 409 payload returned when an AncestryDNA upload arrives and the
 * installed VEP bundle is below v2.0.0 (Plan §5.4, ADNA-00d).
 */
export interface BundleGatePayload {
  error: 'bundle_version_too_old'
  installed_version: string
  required_version: string
  vendor: 'ancestrydna'
  update_url: string
  size_bytes: number
  checksum_sha256: string | null
}

/**
 * HTTP 409 payload returned by ``POST /api/setup/import-backup`` when the
 * backup's recorded VEP bundle major doesn't match the installed bundle's
 * major (Plan §7.6, ADNA-00f). Either direction blocks; the restore is
 * transactional with respect to ``data_dir`` extraction — no files are
 * written when this fires.
 */
export interface BundleVersionMismatchPayload {
  error: 'bundle_version_mismatch'
  installed_version: string
  backup_version: string
  direction: 'backup_below_installed' | 'backup_above_installed'
  sample_member: string
}
