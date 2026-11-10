/** RestoreStep — bundle-version-mismatch banner for the setup wizard.
 *
 * Plan §7.6: when ``POST /api/setup/import-backup`` returns HTTP 409 with
 * an ``error: 'bundle_version_mismatch'`` payload, the wizard halts the
 * import path and renders this banner. Restore is blocked in BOTH
 * directions (backup older than installed; backup newer than installed)
 * — equal major is the only OK state. The user resolves by upgrading or
 * downgrading the installed bundle in the database-download step, then
 * retries the import.
 */

import { AlertTriangle, ArrowLeft, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { BundleVersionMismatchPayload } from '@/types/setup'

interface RestoreStepProps {
  payload: BundleVersionMismatchPayload
  onRetry: () => void
  onBack: () => void
}

export default function RestoreStep({
  payload,
  onRetry,
  onBack,
}: RestoreStepProps) {
  const backupBelow = payload.direction === 'backup_below_installed'
  const headline = backupBelow
    ? 'Backup is older than the installed bundle'
    : 'Backup is newer than the installed bundle'
  const guidance = backupBelow
    ? 'Downgrade the installed VEP bundle to the backup version, or restore an updated backup.'
    : 'Upgrade the installed VEP bundle to at least the backup version, then retry the import.'

  return (
    <div
      className="space-y-6"
      data-testid="restore-bundle-mismatch"
      role="alert"
      aria-live="polite"
    >
      <div className="text-center space-y-2">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-destructive/10">
          <AlertTriangle className="h-7 w-7 text-destructive-text" />
        </div>
        <h2 className="text-xl font-semibold text-foreground">{headline}</h2>
        <p className="text-sm text-muted-foreground">
          Backup was taken against bundle{' '}
          <strong className="text-foreground">{payload.backup_version}</strong>
          ; installed bundle is{' '}
          <strong className="text-foreground">
            {payload.installed_version}
          </strong>
          . Restore requires the installed bundle's major version to match the
          backup's.
        </p>
      </div>

      <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 space-y-2 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Backup version</span>
          <span className="font-mono font-medium text-foreground">
            {payload.backup_version}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Installed version</span>
          <span className="font-mono font-medium text-foreground">
            {payload.installed_version}
          </span>
        </div>
        <div className="pt-2 text-muted-foreground">{guidance}</div>
      </div>

      <div className="flex gap-3">
        <button
          type="button"
          onClick={onBack}
          className={cn(
            'rounded-lg border border-border px-5 py-2.5 text-sm font-medium',
            'text-foreground hover:bg-accent transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          )}
        >
          <span className="flex items-center gap-2">
            <ArrowLeft className="h-4 w-4" />
            Back
          </span>
        </button>
        <button
          type="button"
          onClick={onRetry}
          className={cn(
            'flex-1 rounded-lg px-5 py-2.5 text-sm font-medium',
            'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm transition-all',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          )}
        >
          <span className="flex items-center justify-center gap-2">
            <RefreshCw className="h-4 w-4" />
            Choose a different backup
          </span>
        </button>
      </div>
    </div>
  )
}
