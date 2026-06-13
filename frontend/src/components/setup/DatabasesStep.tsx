/** Setup wizard Step 5 — Download databases with parallel per-DB progress (P1-19f).
 *
 * Shows all reference databases, their status, and allows the user to trigger
 * parallel downloads. Progress is streamed via SSE from the backend.
 *
 * Databases with build_mode="manual" show an amber "Manual Build" badge.
 * Databases with build_mode="bundled" show a green "Included" badge.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  DATABASE_LIST_KEY,
  SETUP_STATUS_KEY,
  useDatabaseList,
  useSetupStatus,
  useTriggerDownload,
} from '@/api/setup'
import {
  DB_HEALTH_KEY,
  useDatabaseHealth,
  useResumeDownload,
  type DatabaseHealth,
} from '@/api/db-health'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { DatabaseProgressEvent, DownloadProgressData } from '@/types/setup'
import { cn } from '@/lib/utils'
import {
  AlertCircle,
  CheckCircle2,
  Database,
  Download,
  HardDrive,
  Loader2,
  PackageCheck,
  RefreshCw,
  Wrench,
} from 'lucide-react'

const DEFAULT_OPTIONAL_DBS = new Set(['lai_bundle', 'encode_ccres'])

function isSelectable(db: { build_mode: string; downloaded: boolean }) {
  return (
    !db.downloaded &&
    db.build_mode !== 'bundled' &&
    db.build_mode !== 'manual'
  )
}

interface DatabasesStepProps {
  onNext: () => void
  onBack: () => void
}

/** Format bytes to a human-readable string (e.g. "1.5 GB"). */
function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(1)} KB`
  return `${bytes} B`
}

export default function DatabasesStep({ onNext, onBack }: DatabasesStepProps) {
  const queryClient = useQueryClient()
  const { data: dbList, isLoading, isError, error } = useDatabaseList()
  const { data: setupStatus } = useSetupStatus()
  const { data: health } = useDatabaseHealth()
  const triggerDownload = useTriggerDownload()
  const resumeDownload = useResumeDownload()

  // Map db_name → health record so we can surface resumable partials and
  // integrity problems mid-setup (the most common crash/disconnect point).
  const healthMap = useMemo(() => {
    const map: Record<string, DatabaseHealth> = {}
    for (const h of health?.databases ?? []) map[h.name] = h
    return map
  }, [health])

  // Per-DB progress from SSE
  const [dbProgress, setDbProgress] = useState<
    Record<string, DatabaseProgressEvent>
  >({})
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)

  // Per-DB selection. Required DBs are always selected (locked). lai_bundle
  // and encode_ccres default selected (toggleable). Other optional DBs default
  // off. We track user overrides separately so we can recompute the effective
  // selection whenever the database list refreshes (e.g. after a download
  // completes) without overwriting user choices.
  const [userOverrides, setUserOverrides] = useState<Record<string, boolean>>(
    {},
  )

  const selectedDbs = useMemo(() => {
    const sel = new Set<string>()
    if (!dbList) return sel
    for (const db of dbList.databases) {
      if (!isSelectable(db)) continue
      if (db.required) {
        sel.add(db.name)
        continue
      }
      if (Object.prototype.hasOwnProperty.call(userOverrides, db.name)) {
        if (userOverrides[db.name]) sel.add(db.name)
        continue
      }
      if (DEFAULT_OPTIONAL_DBS.has(db.name)) sel.add(db.name)
    }
    return sel
  }, [dbList, userOverrides])

  const toggleDb = useCallback((name: string) => {
    setUserOverrides((prev) => {
      const previouslySet = Object.prototype.hasOwnProperty.call(prev, name)
      const previousValue = previouslySet
        ? prev[name]
        : DEFAULT_OPTIONAL_DBS.has(name)
      return { ...prev, [name]: !previousValue }
    })
  }, [])

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close()
    }
  }, [])

  // Shared SSE connector: drives per-DB progress for both a fresh download and
  // a resume (the backend returns the same session shape for both).
  const connectToSession = useCallback(
    (
      downloads: { db_name: string; job_id: string }[],
      sessionId: string,
    ) => {
      const initial: Record<string, DatabaseProgressEvent> = {}
      for (const dl of downloads) {
        initial[dl.db_name] = {
          db_name: dl.db_name,
          job_id: dl.job_id,
          status: 'pending',
          progress_pct: 0,
          message: 'Queued...',
          error: null,
        }
      }
      setDbProgress((prev) => ({ ...prev, ...initial }))

      // Defensively close any prior stream so a second connect can never leak
      // an EventSource (e.g. resume invoked while one is already open).
      eventSourceRef.current?.close()
      const es = new EventSource(`/api/databases/progress/${sessionId}`)
      eventSourceRef.current = es

      const refreshCaches = () => {
        queryClient.invalidateQueries({ queryKey: DATABASE_LIST_KEY })
        queryClient.invalidateQueries({ queryKey: DB_HEALTH_KEY })
        // Re-evaluate the dashboard gate (needs_setup) against fresh health the
        // moment a download finishes or fails — keeps the wizard's single
        // source of truth in sync with the backend.
        queryClient.invalidateQueries({ queryKey: SETUP_STATUS_KEY })
      }

      es.addEventListener('progress', (event: MessageEvent) => {
        const data: DownloadProgressData = JSON.parse(event.data)
        setDbProgress((prev) => {
          const updated = { ...prev }
          for (const db of data.databases) updated[db.db_name] = db
          return updated
        })

        const allTerminal = data.databases.every(
          (db) => db.status === 'complete' || db.status === 'failed',
        )
        if (allTerminal) {
          es.close()
          eventSourceRef.current = null
          setIsDownloading(false)
          refreshCaches()
          const failed = data.databases.filter((db) => db.status === 'failed')
          if (failed.length > 0) {
            setDownloadError(
              `${failed.length} database(s) failed. You can retry or resume.`,
            )
          }
        }
      })

      es.addEventListener('error', () => {
        es.close()
        eventSourceRef.current = null
        setIsDownloading(false)
        setDownloadError(
          'Lost connection to download progress stream. Check your downloads and retry if needed.',
        )
        refreshCaches()
      })
    },
    [queryClient],
  )

  const handleStartDownload = useCallback(() => {
    if (!dbList) return

    const toDownload = [...selectedDbs]
    if (toDownload.length === 0) return

    setIsDownloading(true)
    setDownloadError(null)
    setDbProgress({})

    triggerDownload.mutate(toDownload, {
      onSuccess: (result) => connectToSession(result.downloads, result.session_id),
      onError: (err) => {
        setIsDownloading(false)
        setDownloadError(
          err instanceof Error ? err.message : 'Failed to start downloads',
        )
      },
    })
  }, [dbList, selectedDbs, triggerDownload, connectToSession])

  const handleResume = useCallback(
    (dbName: string) => {
      setIsDownloading(true)
      setDownloadError(null)
      resumeDownload.mutate(dbName, {
        onSuccess: (result) => connectToSession(result.downloads, result.session_id),
        onError: (err) => {
          setIsDownloading(false)
          setDownloadError(
            err instanceof Error ? err.message : 'Failed to resume download',
          )
        },
      })
    },
    [resumeDownload, connectToSession],
  )

  const handleRetry = useCallback(() => {
    setDownloadError(null)
    handleStartDownload()
  }, [handleStartDownload])

  // Whether the user may proceed past the Databases step. This is the SAME
  // health-verified gate the backend uses for needs_setup (required_dbs_ready):
  // every required, downloadable DB must be integrity-`ready`, with bundled and
  // manual modes exempt. Using the backend truth — instead of the old
  // presence/SSE-`complete` heuristic — means Continue can never enable on a DB
  // that merely downloaded but is empty/partial/corrupt, and the wizard cannot
  // disagree with AuthGuard. SETUP_STATUS_KEY is invalidated when a download
  // batch finishes (see refreshCaches), so this re-evaluates promptly.
  const allRequiredReady = setupStatus?.required_dbs_ready ?? false

  const needsDownload = selectedDbs.size > 0

  const selectedTotalBytes = useMemo(() => {
    if (!dbList) return 0
    let total = 0
    for (const db of dbList.databases) {
      if (selectedDbs.has(db.name)) total += db.expected_size_bytes
    }
    return total
  }, [dbList, selectedDbs])

  const handleContinue = useCallback(() => {
    if (dbList) {
      const skipped = dbList.databases.filter(
        (db) => isSelectable(db) && !db.required && !selectedDbs.has(db.name),
      )
      if (skipped.length > 0) {
        const names = skipped.map((db) => db.display_name).join(', ')
        toast.info(`You skipped ${names}`, {
          description:
            'Download later from Settings > Update Manager.',
        })
      }
    }
    onNext()
  }, [dbList, selectedDbs, onNext])

  // ── Loading state ──────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex flex-col items-center gap-4 py-12">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <p className="text-sm text-muted-foreground">
          Loading database information...
        </p>
      </div>
    )
  }

  // ── Error state ────────────────────────────────────────────

  if (isError) {
    return (
      <div className="space-y-6">
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-6 text-center">
          <AlertCircle className="mx-auto h-8 w-8 text-destructive" />
          <p className="mt-3 text-sm font-medium text-destructive">
            Failed to load database information
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </div>
        <div className="flex justify-center">
          <button
            type="button"
            onClick={onBack}
            className={cn(
              'rounded-lg border border-border px-5 py-2.5 text-sm font-medium',
              'text-foreground hover:bg-accent transition-colors',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            )}
          >
            Back
          </button>
        </div>
      </div>
    )
  }

  // ── Main content ───────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
          <Database className="h-6 w-6 text-primary" />
        </div>
        <h2 className="mt-3 text-xl font-semibold text-foreground">
          Reference Databases
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Yeliztli needs reference databases for variant annotation.
          {dbList && (
            <span className="block mt-1">
              Total size: {formatBytes(dbList.total_size_bytes)} &middot;{' '}
              {dbList.downloaded_count} of {dbList.total_count} downloaded
            </span>
          )}
        </p>
      </div>

      {/* Database list */}
      {dbList && (
        <div className="space-y-3">
          {dbList.databases.map((db) => {
            const progress = dbProgress[db.name]
            const isBundled = db.build_mode === 'bundled'
            const isManual = db.build_mode === 'manual'
            const isComplete = isBundled
              ? true
              : progress
                ? progress.status === 'complete'
                : db.downloaded
            const isFailed = progress?.status === 'failed'
            const isRunning = progress?.status === 'running'
            const isPending = progress?.status === 'pending'
            const showCheckbox = isSelectable(db)
            const isSelected = selectedDbs.has(db.name)
            const checkboxId = `db-select-${db.name}`
            const dbHealth = healthMap[db.name]
            // A resumable partial exists when a prior download was interrupted.
            // Surface it (and an explicit Resume) only when not already running.
            const showResume =
              !!dbHealth?.resumable &&
              !isDownloading &&
              !isRunning &&
              !isPending &&
              !isComplete

            return (
              <div
                key={db.name}
                className={cn(
                  'rounded-lg border p-4 transition-colors',
                  isComplete && 'border-green-500/30 bg-green-500/5',
                  isFailed && 'border-destructive/30 bg-destructive/5',
                  !isComplete && !isFailed && 'border-border bg-card',
                )}
              >
                <div className="flex items-start gap-3">
                  {/* Selection checkbox */}
                  {showCheckbox && (
                    <input
                      id={checkboxId}
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleDb(db.name)}
                      disabled={db.required || isDownloading}
                      className="mt-1 h-4 w-4 flex-shrink-0 rounded border-border text-primary focus:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
                      aria-label={`Include ${db.display_name} in download`}
                      data-testid={`db-checkbox-${db.name}`}
                    />
                  )}

                  {/* Status icon */}
                  <div className="mt-0.5 flex-shrink-0">
                    {isBundled && (
                      <PackageCheck className="h-5 w-5 text-green-500" />
                    )}
                    {!isBundled && isComplete && (
                      <CheckCircle2 className="h-5 w-5 text-green-500" />
                    )}
                    {!isBundled && isFailed && (
                      <AlertCircle className="h-5 w-5 text-destructive" />
                    )}
                    {!isBundled && isRunning && (
                      <Loader2 className="h-5 w-5 animate-spin text-primary" />
                    )}
                    {!isBundled && isPending && (
                      <Download className="h-5 w-5 text-muted-foreground" />
                    )}
                    {!isBundled && isManual && !isComplete && !isFailed && !isRunning && !isPending && (
                      <Wrench className="h-5 w-5 text-amber-500" />
                    )}
                    {!isBundled && !isManual && !progress && !db.downloaded && (
                      <HardDrive className="h-5 w-5 text-muted-foreground" />
                    )}
                  </div>

                  {/* Info */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground">
                        {db.display_name}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {formatBytes(db.expected_size_bytes)}
                      </span>
                      {isBundled && (
                        <span className="rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-medium text-green-600 dark:text-green-400">
                          Included
                        </span>
                      )}
                      {isManual && (
                        <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                          Manual Build
                        </span>
                      )}
                      {!isBundled && !isManual && db.required && (
                        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                          Required
                        </span>
                      )}
                      {!isBundled && !isManual && !db.required && (
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                          Optional
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {db.description}
                    </p>

                    {/* Progress bar */}
                    {(isRunning || isPending) && (
                      <div className="mt-2">
                        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                          <div
                            className={cn(
                              'h-full rounded-full transition-all duration-300',
                              isRunning
                                ? 'bg-primary'
                                : 'bg-muted-foreground/30',
                            )}
                            style={{
                              width: `${Math.max(progress?.progress_pct ?? 0, isRunning ? 2 : 0)}%`,
                            }}
                            role="progressbar"
                            aria-valuenow={Math.round(
                              progress?.progress_pct ?? 0,
                            )}
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-label={`${db.display_name} download progress`}
                          />
                        </div>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {progress?.message || 'Waiting...'}
                        </p>
                      </div>
                    )}

                    {/* Error message */}
                    {isFailed && progress?.error && (
                      <p className="mt-1 text-xs text-destructive">
                        {progress.error}
                      </p>
                    )}

                    {/* Resumable partial — surface progress + explicit resume */}
                    {showResume && (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span className="text-xs text-amber-600 dark:text-amber-400">
                          Partial download
                          {dbHealth?.progress_pct != null &&
                            ` — ${dbHealth.progress_pct}% saved`}
                          . Resume to continue where it stopped.
                        </span>
                        <button
                          type="button"
                          onClick={() => handleResume(db.name)}
                          className={cn(
                            'inline-flex items-center gap-1 rounded-md border border-amber-400 px-2 py-1 text-xs font-medium',
                            'text-amber-700 hover:bg-amber-50 dark:border-amber-600 dark:text-amber-300 dark:hover:bg-amber-950/30',
                            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
                          )}
                          data-testid={`db-resume-${db.name}`}
                        >
                          <RefreshCw className="h-3 w-3" />
                          Resume
                        </button>
                      </div>
                    )}

                    {/* Downloaded status */}
                    {isComplete && !isBundled && (
                      <p className="mt-0.5 text-xs text-green-600 dark:text-green-400">
                        Downloaded
                        {db.file_size_bytes != null &&
                          ` (${formatBytes(db.file_size_bytes)})`}
                      </p>
                    )}

                    {/* Bundled status */}
                    {isBundled && (
                      <p className="mt-0.5 text-xs text-green-600 dark:text-green-400">
                        Ships with Yeliztli
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Download error banner */}
      {downloadError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3">
          <div className="flex items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-destructive" />
            <p className="text-sm text-destructive">{downloadError}</p>
          </div>
        </div>
      )}

      {/* Running total */}
      {dbList && needsDownload && (
        <div
          className="flex items-center justify-end text-xs text-muted-foreground"
          data-testid="selected-total"
        >
          Total:{' '}
          <span className="ml-1 font-medium text-foreground">
            {formatBytes(selectedTotalBytes)} selected
          </span>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          disabled={isDownloading}
          className={cn(
            'rounded-lg border border-border px-5 py-2.5 text-sm font-medium',
            'text-foreground hover:bg-accent transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            'disabled:opacity-50 disabled:cursor-not-allowed',
          )}
        >
          Back
        </button>

        <div className="flex items-center gap-3">
          {/* Download / Retry button */}
          {needsDownload && !isDownloading && (
            <button
              type="button"
              onClick={downloadError ? handleRetry : handleStartDownload}
              className={cn(
                'inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium',
                'bg-primary text-primary-foreground hover:bg-primary/90 transition-colors',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              )}
            >
              {downloadError ? (
                <>
                  <RefreshCw className="h-4 w-4" />
                  Retry Download
                </>
              ) : (
                <>
                  <Download className="h-4 w-4" />
                  Download Selected
                </>
              )}
            </button>
          )}

          {/* Downloading indicator */}
          {isDownloading && (
            <div className="inline-flex items-center gap-2 rounded-lg bg-muted px-5 py-2.5 text-sm font-medium text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Downloading...
            </div>
          )}

          {/* Continue button */}
          <button
            type="button"
            onClick={handleContinue}
            disabled={!allRequiredReady || isDownloading}
            className={cn(
              'rounded-lg px-5 py-2.5 text-sm font-medium transition-colors',
              'bg-primary text-primary-foreground hover:bg-primary/90',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  )
}
