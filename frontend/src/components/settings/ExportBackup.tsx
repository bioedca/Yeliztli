/** Export Backup UI for Settings > General (P4-21c).
 *
 * Shows estimated archive size, optional reference DB inclusion,
 * and triggers background export with progress polling.
 */

import { useState } from 'react'
import { Download, Archive, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import { useBackupEstimate, useStartBackupExport, useBackupStatus } from '@/api/backup'

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

export default function ExportBackup() {
  const [includeRefDbs, setIncludeRefDbs] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)

  const estimate = useBackupEstimate()
  const startExport = useStartBackupExport()
  const jobStatus = useBackupStatus(activeJobId)

  const isExporting = activeJobId !== null &&
    jobStatus.data?.status !== 'complete' &&
    jobStatus.data?.status !== 'failed'

  function handleExport() {
    setActiveJobId(null)
    startExport.mutate(includeRefDbs, {
      onSuccess: (data) => {
        setActiveJobId(data.job_id)
      },
    })
  }

  function handleDownload() {
    if (jobStatus.data?.download_filename) {
      window.open(`/api/backup/download/${jobStatus.data.download_filename}`, '_blank')
    }
  }

  const estimatedSize = estimate.data
    ? includeRefDbs
      ? estimate.data.total_with_ref_bytes
      : estimate.data.total_without_ref_bytes
    : 0

  return (
    <div className="space-y-4" data-testid="export-backup">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Export Backup</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Create a .tar.gz archive of your data for safekeeping or migrating to
          another machine. Includes sample databases and configuration.
        </p>
      </div>

      {/* Size estimate */}
      {estimate.data && (
        <div className="rounded-lg border border-border bg-muted/30 p-4 space-y-2">
          <div className="flex items-center gap-2 text-sm">
            <Archive className="h-4 w-4 text-muted-foreground" />
            <span className="text-muted-foreground">
              {estimate.data.sample_count} sample(s)
              {estimate.data.reference_db_count > 0 &&
                `, ${estimate.data.reference_db_count} reference database(s) available`}
            </span>
          </div>
          <div className="text-sm text-muted-foreground">
            Estimated archive size (uncompressed):{' '}
            <span className="font-medium text-foreground">
              {formatBytes(estimatedSize)}
            </span>
          </div>
        </div>
      )}

      {/* Include reference DBs checkbox */}
      {estimate.data && estimate.data.reference_db_count > 0 && (
        <label htmlFor="include-ref-dbs" className="flex items-start gap-3 cursor-pointer">
          <input
            id="include-ref-dbs"
            type="checkbox"
            checked={includeRefDbs}
            onChange={(e) => setIncludeRefDbs(e.target.checked)}
            disabled={isExporting}
            className="mt-0.5 h-4 w-4 rounded border-border text-primary focus:ring-primary"
            data-testid="include-ref-dbs-checkbox"
            aria-label="Include reference databases"
          />
          <div>
            <span className="text-sm font-medium text-foreground">
              Include reference databases
            </span>
            <p className="text-xs text-muted-foreground mt-0.5">
              ClinVar, gnomAD, dbNSFP, VEP bundle, etc. ({formatBytes(estimate.data.reference_bytes)}).
              Not needed if you plan to re-download on the target machine.
            </p>
          </div>
        </label>
      )}

      {/* Export button */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          data-testid="export-backup-btn"
          disabled={isExporting || startExport.isPending}
          onClick={handleExport}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          {isExporting || startExport.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Exporting...
            </>
          ) : (
            <>
              <Download className="h-4 w-4" />
              Export All Data
            </>
          )}
        </button>
      </div>

      {/* Progress indicator */}
      {isExporting && jobStatus.data && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {jobStatus.data.message}
          </div>
          <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-300"
              style={{ width: `${jobStatus.data.progress_pct}%` }}
            />
          </div>
        </div>
      )}

      {/* Complete state */}
      {jobStatus.data?.status === 'complete' && jobStatus.data.download_filename && (
        <div className="flex items-center gap-3 rounded-lg border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30 p-4">
          <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400 shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-medium text-green-800 dark:text-green-300">
              Backup export complete
            </p>
            <p className="text-xs text-green-700 dark:text-green-400 mt-0.5">
              {jobStatus.data.download_filename}
            </p>
          </div>
          <button
            type="button"
            data-testid="download-backup-btn"
            onClick={handleDownload}
            className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-green-500"
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </button>
        </div>
      )}

      {/* Error state */}
      {jobStatus.data?.status === 'failed' && (
        <div className="flex items-start gap-3 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-4">
          <XCircle className="h-5 w-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-red-800 dark:text-red-300">
              Backup export failed
            </p>
            {jobStatus.data.error && (
              <p className="text-xs text-red-700 dark:text-red-400 mt-0.5">
                {jobStatus.data.error}
              </p>
            )}
          </div>
        </div>
      )}

      {startExport.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          Failed to start export: {startExport.error.message}
        </p>
      )}
    </div>
  )
}
