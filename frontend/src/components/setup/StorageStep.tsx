/** Step 3: Storage path + disk space check.
 *
 * P1-19c: Lets users configure the storage path (defaults to ~/.yeliztli).
 * Checks disk space and warns/blocks per PRD §2.18:
 *   - Warn if < 10 GB free
 *   - Block if < 5 GB free
 */

import { useCallback, useState } from 'react'
import { useSetStoragePath, useStorageInfo } from '@/api/setup'
import { cn } from '@/lib/utils'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FolderOpen,
  HardDrive,
  XCircle,
} from 'lucide-react'

interface StorageStepProps {
  onNext: () => void
  onBack: () => void
}

export default function StorageStep({ onNext, onBack }: StorageStepProps) {
  const { data: storageInfo, isLoading } = useStorageInfo()
  const setStorageMutation = useSetStoragePath()
  const [customPath, setCustomPath] = useState('')
  const [useCustomPath, setUseCustomPath] = useState(false)

  // Effective path: custom input if user toggled, otherwise default from server
  const effectivePath = useCustomPath
    ? customPath
    : (storageInfo?.data_dir ?? '')

  const handleConfirm = useCallback(async () => {
    try {
      const result = await setStorageMutation.mutateAsync(effectivePath)
      if (result.status !== 'blocked') {
        onNext()
      }
    } catch {
      // Error state handled by mutation
    }
  }, [setStorageMutation, effectivePath, onNext])

  const isBlocked = storageInfo?.status === 'blocked'
  const isWarning = storageInfo?.status === 'warning'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center space-y-2">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <HardDrive className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground">
          Storage Location
        </h2>
        <p className="text-sm text-muted-foreground">
          Choose where Yeliztli stores databases, samples, and configuration.
          Reference databases require approximately 4 GB of disk space.
        </p>
      </div>

      {/* Per-DB size breakdown (Plan §12.1, ADNA-00d) */}
      <div
        data-testid="storage-db-breakdown"
        className="rounded-lg border bg-card p-4 space-y-2"
      >
        <h3 className="text-sm font-medium text-foreground">
          Reference database size breakdown
        </h3>
        <p className="text-xs text-muted-foreground">
          Required core (~4 GB) plus optional bundles. The VEP bundle alone is
          approximately 600 MB on 0.2.0+ to cover both 23andMe v5 and
          AncestryDNA v2.0 rsIDs.
        </p>
        <ul className="text-xs text-muted-foreground space-y-1 pt-1">
          <li className="flex items-center justify-between">
            <span>gnomAD allele frequencies</span>
            <span className="font-medium text-foreground">~2 GB</span>
          </li>
          <li className="flex items-center justify-between">
            <span>dbNSFP pathogenicity scores</span>
            <span className="font-medium text-foreground">~1.5 GB</span>
          </li>
          <li className="flex items-center justify-between">
            <span>VEP bundle (23andMe v5 ∪ AncestryDNA v2.0)</span>
            <span className="font-medium text-foreground">~600 MB</span>
          </li>
          <li className="flex items-center justify-between">
            <span>LAI bundle (chromosome painting, optional)</span>
            <span className="font-medium text-foreground">~500 MB</span>
          </li>
          <li className="flex items-center justify-between">
            <span>ClinVar, CPIC, GWAS, dbSNP, MONDO/HPO, ENCODE cCREs</span>
            <span className="font-medium text-foreground">~420 MB</span>
          </li>
        </ul>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center py-8">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-2 text-sm text-muted-foreground">
            Checking storage...
          </span>
        </div>
      )}

      {storageInfo && (
        <>
          {/* Path selection */}
          <div className="rounded-lg border bg-card p-4 space-y-4">
            <div className="space-y-3">
              {/* Default path option */}
              <label htmlFor="storage-path-default" className="flex items-start gap-3 cursor-pointer" aria-label="Default location">
                <input
                  id="storage-path-default"
                  type="radio"
                  name="storage-path"
                  checked={!useCustomPath}
                  onChange={() => setUseCustomPath(false)}
                  className="mt-1 h-4 w-4 text-primary focus:ring-primary"
                />
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-foreground">
                    Default location
                  </span>
                  <p className="text-xs text-muted-foreground mt-0.5 break-all">
                    <code className="rounded bg-muted px-1 py-0.5 font-mono">
                      {storageInfo.data_dir}
                    </code>
                  </p>
                </div>
              </label>

              {/* Custom path option */}
              <label htmlFor="storage-path-custom" className="flex items-start gap-3 cursor-pointer" aria-label="Custom location">
                <input
                  id="storage-path-custom"
                  type="radio"
                  name="storage-path"
                  checked={useCustomPath}
                  onChange={() => setUseCustomPath(true)}
                  className="mt-1 h-4 w-4 text-primary focus:ring-primary"
                />
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium text-foreground">
                    Custom location
                  </span>
                </div>
              </label>

              {/* Custom path input */}
              {useCustomPath && (
                <div className="ml-7">
                  <div className="relative">
                    <FolderOpen className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <input
                      type="text"
                      value={customPath}
                      onChange={(e) => setCustomPath(e.target.value)}
                      placeholder="/path/to/yeliztli"
                      className={cn(
                        'w-full rounded-lg border bg-background py-2.5 pl-10 pr-3 text-sm font-mono',
                        'text-foreground placeholder:text-muted-foreground',
                        'focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent',
                        'border-border',
                      )}
                      aria-label="Custom storage path"
                    />
                  </div>
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    Use an absolute path. Tilde (~) will be expanded to your home directory.
                    Disk space will be validated when you continue.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Volatile-filesystem warning (independent of disk space; non-blocking) */}
          {storageInfo.volatile && (
            <div
              role="alert"
              className="rounded-lg border border-amber-500/50 bg-amber-50 p-4 dark:bg-amber-950/20"
            >
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-600 dark:text-amber-500" />
                <div className="space-y-1">
                  <span className="text-sm font-medium text-amber-700 dark:text-amber-400">
                    Volatile storage location
                  </span>
                  <p className="text-sm text-amber-700 dark:text-amber-400">
                    {storageInfo.volatile_message ??
                      'This storage location may be erased when the machine restarts.'}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Disk space info */}
          <div
            className={cn(
              'rounded-lg border p-4 space-y-3',
              isBlocked
                ? 'border-destructive/50 bg-destructive/5'
                : isWarning
                  ? 'border-amber-500/50 bg-amber-50 dark:bg-amber-950/20'
                  : 'border-border bg-card',
            )}
          >
            <div className="flex items-center gap-2">
              {isBlocked ? (
                <XCircle className="h-5 w-5 text-destructive flex-shrink-0" />
              ) : isWarning ? (
                <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-500 flex-shrink-0" />
              ) : (
                <CheckCircle2 className="h-5 w-5 text-green-600 flex-shrink-0" />
              )}
              <span
                className={cn(
                  'text-sm font-medium',
                  isBlocked
                    ? 'text-destructive'
                    : isWarning
                      ? 'text-amber-700 dark:text-amber-400'
                      : 'text-foreground',
                )}
              >
                {isBlocked
                  ? 'Insufficient Disk Space'
                  : isWarning
                    ? 'Low Disk Space'
                    : 'Disk Space OK'}
              </span>
            </div>

            <p
              className={cn(
                'text-sm',
                isBlocked
                  ? 'text-destructive/80'
                  : isWarning
                    ? 'text-amber-700 dark:text-amber-400'
                    : 'text-muted-foreground',
              )}
            >
              {storageInfo.message}
            </p>

            {/* Space breakdown */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>Free space</span>
                <span className="font-medium text-foreground">
                  {storageInfo.free_space_gb} GB
                </span>
              </div>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>Total space</span>
                <span className="font-medium text-foreground">
                  {storageInfo.total_space_gb} GB
                </span>
              </div>
              {/* Usage bar */}
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all',
                    isBlocked
                      ? 'bg-destructive'
                      : isWarning
                        ? 'bg-amber-500'
                        : 'bg-primary',
                  )}
                  style={{
                    width: `${storageInfo.total_space_gb > 0 ? Math.min(100, ((storageInfo.total_space_gb - storageInfo.free_space_gb) / storageInfo.total_space_gb) * 100) : 0}%`,
                  }}
                />
              </div>
            </div>

            {/* Path status */}
            <div className="flex items-center justify-between text-xs border-t border-border pt-2 mt-2">
              <span className="text-muted-foreground">Path writable</span>
              <span
                className={cn(
                  'font-medium',
                  storageInfo.path_writable
                    ? 'text-green-700 dark:text-green-400'
                    : 'text-destructive',
                )}
              >
                {storageInfo.path_writable ? 'Yes' : 'No'}
              </span>
            </div>
          </div>

          {/* Error from set-storage-path */}
          {setStorageMutation.isError && (
            <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-center">
              <AlertTriangle className="mx-auto h-5 w-5 text-destructive" />
              <p className="mt-2 text-sm text-destructive">
                {setStorageMutation.error instanceof Error
                  ? setStorageMutation.error.message
                  : 'Failed to set storage path.'}
              </p>
            </div>
          )}

          {/* Blocked result from mutation */}
          {setStorageMutation.isSuccess &&
            setStorageMutation.data?.status === 'blocked' && (
              <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-center">
                <XCircle className="mx-auto h-5 w-5 text-destructive" />
                <p className="mt-2 text-sm text-destructive">
                  {setStorageMutation.data.message}
                </p>
              </div>
            )}
        </>
      )}

      {/* Action buttons */}
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
          Back
        </button>
        <button
          type="button"
          onClick={handleConfirm}
          disabled={
            isLoading ||
            isBlocked ||
            setStorageMutation.isPending ||
            (useCustomPath && !customPath.trim())
          }
          className={cn(
            'flex-1 rounded-lg px-6 py-3 text-sm font-medium transition-all',
            'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            (isLoading || isBlocked || setStorageMutation.isPending) &&
              'opacity-70 cursor-not-allowed',
          )}
        >
          {setStorageMutation.isPending ? (
            <span className="flex items-center justify-center gap-2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
              Saving...
            </span>
          ) : (
            <span className="flex items-center justify-center gap-2">
              <ArrowRight className="h-4 w-4" />
              Continue
            </span>
          )}
        </button>
      </div>
    </div>
  )
}
