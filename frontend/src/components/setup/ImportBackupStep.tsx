/** Step 2: Import from backup — optional restore from .tar.gz archive.
 *
 * P1-19b: Also auto-detects existing installation. If found, offers to
 * skip the wizard entirely.
 */

import { useCallback, useRef, useState } from 'react'
import {
  BundleVersionMismatchError,
  useDetectExisting,
  useImportBackup,
} from '@/api/setup'
import { cn } from '@/lib/utils'
import {
  AlertTriangle,
  Archive,
  ArrowRight,
  CheckCircle2,
  FolderSearch,
  Upload,
} from 'lucide-react'
import RestoreStep from './RestoreStep'

interface ImportBackupStepProps {
  onNext: () => void
  onBack: () => void
  onSkipToEnd?: () => void
}

export default function ImportBackupStep({
  onNext,
  onBack,
  onSkipToEnd,
}: ImportBackupStepProps) {
  const { data: existing, isLoading: detectLoading } = useDetectExisting()
  const importMutation = useImportBackup()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)

  const handleFileSelect = useCallback((file: File) => {
    if (file.name.endsWith('.tar.gz') || file.name.endsWith('.tgz')) {
      setSelectedFile(file)
      setFileError(null)
    } else {
      setFileError('Please select a .tar.gz or .tgz file')
    }
  }, [])

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFileSelect(file)
    },
    [handleFileSelect],
  )

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragActive(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFileSelect(file)
    },
    [handleFileSelect],
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    setDragActive(false)
  }, [])

  async function handleImport() {
    if (!selectedFile) return
    try {
      await importMutation.mutateAsync(selectedFile)
    } catch {
      // Error state handled by importMutation.isError (covers both the
      // generic error path and the §7.6 bundle-version mismatch banner).
    }
  }

  function handleMismatchRetry() {
    setSelectedFile(null)
    setFileError(null)
    importMutation.reset()
    fileInputRef.current?.click()
  }

  // §7.6 bundle-version mismatch — short-circuits the rest of the step
  // because no files were written to data_dir.
  if (
    importMutation.isError &&
    importMutation.error instanceof BundleVersionMismatchError
  ) {
    return (
      <RestoreStep
        payload={importMutation.error.payload}
        onRetry={handleMismatchRetry}
        onBack={onBack}
      />
    )
  }

  // Auto-detected existing installation
  if (!detectLoading && existing?.existing_found && !importMutation.isSuccess) {
    return (
      <div className="space-y-6">
        <div className="text-center space-y-2">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
            <FolderSearch className="h-7 w-7 text-primary" />
          </div>
          <h2 className="text-xl font-semibold text-foreground">
            Existing Installation Detected
          </h2>
          <p className="text-sm text-muted-foreground">
            Found existing Yeliztli data at{' '}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
              {existing.data_dir}
            </code>
          </p>
        </div>

        {/* Detection summary */}
        <div className="rounded-lg border bg-card p-4 space-y-2">
          <DetailRow label="Configuration" found={existing.has_config} />
          <DetailRow label="Sample databases" found={existing.has_samples} />
          <DetailRow label="Reference databases" found={existing.has_databases} />
        </div>

        {existing.has_databases && existing.has_samples ? (
          <p className="text-center text-sm text-muted-foreground">
            Your installation appears complete. You can skip setup and go
            directly to the dashboard.
          </p>
        ) : (
          <p className="text-center text-sm text-muted-foreground">
            {!existing.has_databases
              ? 'Reference databases need to be downloaded. Continue setup to complete the installation.'
              : 'Continue setup to finish configuring your installation.'}
          </p>
        )}

        <div className="flex gap-3">
          {existing.has_databases && existing.has_samples && onSkipToEnd && (
            <button
              type="button"
              onClick={onSkipToEnd}
              className={cn(
                'flex-1 rounded-lg px-6 py-3 text-sm font-medium transition-all',
                'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              )}
            >
              <span className="flex items-center justify-center gap-2">
                <ArrowRight className="h-4 w-4" />
                Go to Dashboard
              </span>
            </button>
          )}
          <button
            type="button"
            onClick={onNext}
            className={cn(
              'flex-1 rounded-lg px-6 py-3 text-sm font-medium transition-all',
              'border border-border text-foreground hover:bg-accent',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            )}
          >
            Continue Setup
          </button>
        </div>

        <button
          type="button"
          onClick={onBack}
          className="mx-auto block text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          Back
        </button>
      </div>
    )
  }

  // Import success state
  if (importMutation.isSuccess && importMutation.data) {
    return (
      <div className="space-y-6">
        <div className="text-center space-y-2">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-green-500/10">
            <CheckCircle2 className="h-7 w-7 text-green-600" />
          </div>
          <h2 className="text-xl font-semibold text-foreground">
            Backup Imported
          </h2>
          <p className="text-sm text-muted-foreground">
            {importMutation.data.message}
          </p>
        </div>

        <div className="rounded-lg border bg-card p-4 space-y-2">
          <DetailRow
            label="Samples restored"
            value={String(importMutation.data.samples_restored)}
          />
          <DetailRow
            label="Configuration restored"
            found={importMutation.data.config_restored}
          />
        </div>

        <p className="text-center text-sm text-muted-foreground">
          Reference databases will be downloaded in a later step.
        </p>

        <button
          type="button"
          onClick={onNext}
          className={cn(
            'w-full rounded-lg px-6 py-3 text-sm font-medium transition-all',
            'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          )}
        >
          <span className="flex items-center justify-center gap-2">
            <ArrowRight className="h-4 w-4" />
            Continue
          </span>
        </button>
      </div>
    )
  }

  // Default: import or skip
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center space-y-2">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <Archive className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground">
          Import from Backup
        </h2>
        <p className="text-sm text-muted-foreground">
          Restore a previous Yeliztli backup, or skip to start fresh.
        </p>
      </div>

      {/* Loading detection */}
      {detectLoading && (
        <div className="flex items-center justify-center py-4">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-2 text-sm text-muted-foreground">
            Checking for existing installation...
          </span>
        </div>
      )}

      {/* Drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => fileInputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            fileInputRef.current?.click()
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Select backup archive to import"
        className={cn(
          'rounded-lg border-2 border-dashed p-8 text-center cursor-pointer transition-colors',
          dragActive
            ? 'border-primary bg-primary/5'
            : 'border-border hover:border-primary/50 hover:bg-accent/30',
        )}
      >
        <Upload className="mx-auto h-8 w-8 text-muted-foreground" />
        <p className="mt-3 text-sm font-medium text-foreground">
          {selectedFile
            ? selectedFile.name
            : 'Drop a .tar.gz backup file here'}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {selectedFile
            ? formatFileSize(selectedFile.size)
            : 'or click to browse'}
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".tar.gz,.tgz"
          onChange={handleInputChange}
          className="hidden"
          aria-hidden="true"
        />
      </div>

      {/* File type error */}
      {fileError && (
        <p className="text-center text-sm text-destructive">{fileError}</p>
      )}

      {/* Import button */}
      {selectedFile && (
        <button
          type="button"
          onClick={handleImport}
          disabled={importMutation.isPending}
          className={cn(
            'w-full rounded-lg px-6 py-3 text-sm font-medium transition-all',
            'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            importMutation.isPending && 'opacity-70 cursor-not-allowed',
          )}
        >
          {importMutation.isPending ? (
            <span className="flex items-center justify-center gap-2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
              Importing...
            </span>
          ) : (
            <span className="flex items-center justify-center gap-2">
              <Archive className="h-4 w-4" />
              Import Backup
            </span>
          )}
        </button>
      )}

      {/* Error state */}
      {importMutation.isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-center">
          <AlertTriangle className="mx-auto h-5 w-5 text-destructive" />
          <p className="mt-2 text-sm text-destructive">
            {importMutation.error instanceof Error
              ? importMutation.error.message
              : 'Failed to import backup. Please check the file and try again.'}
          </p>
        </div>
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
          onClick={onNext}
          className={cn(
            'flex-1 rounded-lg border border-border px-5 py-2.5 text-sm font-medium',
            'text-foreground hover:bg-accent transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          )}
        >
          Skip — Start Fresh
        </button>
      </div>
    </div>
  )
}

function DetailRow({
  label,
  found,
  value,
}: {
  label: string
  found?: boolean
  value?: string
}) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      {value !== undefined ? (
        <span className="font-medium text-foreground">{value}</span>
      ) : (
        <span
          className={cn(
            'font-medium',
            found ? 'text-green-700 dark:text-green-400' : 'text-muted-foreground',
          )}
        >
          {found ? 'Found' : 'Not found'}
        </span>
      )}
    </div>
  )
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}
