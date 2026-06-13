/** Setup wizard Step 6 — Upload sample file + redirect to dashboard (P1-19g).
 *
 * Accepts a 23andMe or AncestryDNA raw data file via drag-and-drop or file
 * picker. On successful parse, shows variant count and offers to go to the
 * dashboard. Upload is optional — users can skip and upload later from the
 * main UI.
 */

import { useCallback, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BundleGateError, useIngestFile } from '@/api/setup'
import { useTriggerUpdate } from '@/api/updates'
import { cn } from '@/lib/utils'
import type { BundleGatePayload } from '@/types/setup'
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  FileText,
  Loader2,
  Upload,
} from 'lucide-react'

interface UploadStepProps {
  onBack: () => void
  /**
   * Finish the wizard. When provided (by SetupWizard), this performs the
   * readiness-gated hand-off — navigating to the dashboard only when required
   * databases are integrity-ready, otherwise routing to the Databases recovery
   * step. When omitted, falls back to navigating to the dashboard directly.
   */
  onComplete?: () => void
}

/** Accepted file extensions for 23andMe or AncestryDNA raw data. */
const ACCEPTED_EXTENSIONS = ['.txt', '.csv', '.tsv']

function isValidFile(filename: string): boolean {
  const lower = filename.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function formatNumber(n: number): string {
  return n.toLocaleString()
}

export default function UploadStep({ onBack, onComplete }: UploadStepProps) {
  const navigate = useNavigate()
  const ingestMutation = useIngestFile()
  const triggerUpdate = useTriggerUpdate()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [bundleGate, setBundleGate] = useState<BundleGatePayload | null>(null)

  const handleFileSelect = useCallback((file: File) => {
    if (isValidFile(file.name)) {
      setSelectedFile(file)
      setFileError(null)
      setBundleGate(null)
    } else {
      // Clear any previously valid selection so an invalid pick can't be
      // uploaded by mistake, and reset the file input so re-selecting the
      // same file later still fires onChange.
      setSelectedFile(null)
      setBundleGate(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      setFileError(
        'Please select a 23andMe or AncestryDNA raw data file (.txt, .csv, or .tsv)',
      )
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

  async function handleUpload() {
    if (!selectedFile) return
    setBundleGate(null)
    try {
      await ingestMutation.mutateAsync(selectedFile)
    } catch (err) {
      if (err instanceof BundleGateError) {
        setBundleGate(err.payload)
      }
      // Other errors surfaced via ingestMutation.isError
    }
  }

  async function handleBundleUpdate() {
    try {
      await triggerUpdate.mutateAsync({ dbName: 'vep_bundle' })
      setBundleGate(null)
      ingestMutation.reset()
    } catch {
      // Error surfaced via triggerUpdate.isError
    }
  }

  function handleGoToDashboard() {
    // Delegate to the wizard's readiness-gated hand-off when present, so an
    // unhealthy install never lands on a broken dashboard; fall back to a
    // direct navigation when rendered standalone.
    if (onComplete) {
      onComplete()
    } else {
      navigate('/', { replace: true })
    }
  }

  // ── Success state ──────────────────────────────────────────

  if (ingestMutation.isSuccess && ingestMutation.data) {
    const data = ingestMutation.data
    return (
      <div className="space-y-6">
        <div className="text-center space-y-2">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-green-500/10">
            <CheckCircle2 className="h-7 w-7 text-green-600 dark:text-green-400" />
          </div>
          <h2 className="text-xl font-semibold text-foreground">
            Sample Uploaded
          </h2>
          <p className="text-sm text-muted-foreground">
            Your file has been parsed and stored successfully.
          </p>
        </div>

        <div className="rounded-lg border bg-card p-4 space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Variants parsed</span>
            <span className="font-medium text-foreground">
              {formatNumber(data.variant_count)}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">No-call variants</span>
            <span className="font-medium text-foreground">
              {formatNumber(data.nocall_count)}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">File format</span>
            <span className="font-medium text-foreground">
              {data.file_format}
            </span>
          </div>
        </div>

        <p className="text-center text-sm text-muted-foreground">
          Variant annotation will run automatically once the annotation
          pipeline is configured.
        </p>

        <button
          type="button"
          onClick={handleGoToDashboard}
          className={cn(
            'w-full rounded-lg px-6 py-3 text-sm font-medium transition-all',
            'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          )}
        >
          <span className="flex items-center justify-center gap-2">
            <ArrowRight className="h-4 w-4" />
            Go to Dashboard
          </span>
        </button>
      </div>
    )
  }

  // ── Default: upload or skip ────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center space-y-2">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <FileText className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground">
          Upload Sample
        </h2>
        <p className="text-sm text-muted-foreground">
          Upload a 23andMe or AncestryDNA raw data file to get started, or
          skip to explore the dashboard first.
        </p>
      </div>

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
        aria-label="Select 23andMe or AncestryDNA raw data file to upload"
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
            : 'Drop a 23andMe or AncestryDNA raw data file here'}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {selectedFile
            ? formatFileSize(selectedFile.size)
            : 'or click to browse (.txt, .csv, .tsv)'}
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.csv,.tsv"
          onChange={handleInputChange}
          className="hidden"
          aria-hidden="true"
          tabIndex={-1}
        />
      </div>

      {/* File type error */}
      {fileError && (
        <p className="text-center text-sm text-destructive">{fileError}</p>
      )}

      {/* Upload button */}
      {selectedFile && !ingestMutation.isPending && (
        <button
          type="button"
          onClick={handleUpload}
          className={cn(
            'w-full rounded-lg px-6 py-3 text-sm font-medium transition-all',
            'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          )}
        >
          <span className="flex items-center justify-center gap-2">
            <Upload className="h-4 w-4" />
            Upload & Parse
          </span>
        </button>
      )}

      {/* Uploading state */}
      {ingestMutation.isPending && (
        <div className="rounded-lg border bg-card p-4">
          <div className="flex items-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            <div>
              <p className="text-sm font-medium text-foreground">
                Parsing file...
              </p>
              <p className="text-xs text-muted-foreground">
                This may take a moment for large files.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Bundle-version gate (HTTP 409 on AncestryDNA + pre-v2.0.0 bundle).
          Plan §5.4 / ADNA-00d — banner with one-click update CTA. */}
      {bundleGate && (
        <div
          data-testid="bundle-gate-banner"
          role="alert"
          aria-live="polite"
          className="rounded-lg border border-amber-500/50 bg-amber-50 dark:bg-amber-950/20 p-4 space-y-3"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle
              className="h-5 w-5 text-amber-600 dark:text-amber-500 flex-shrink-0 mt-0.5"
              aria-hidden="true"
            />
            <div className="flex-1 min-w-0 space-y-1">
              <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
                Update VEP bundle (~
                {Math.round(bundleGate.size_bytes / 1_000_000)} MB) to enable
                AncestryDNA
              </p>
              <p className="text-xs text-amber-700 dark:text-amber-300">
                AncestryDNA uploads need VEP bundle{' '}
                <code className="rounded bg-amber-100 dark:bg-amber-900/40 px-1 py-0.5 font-mono">
                  {bundleGate.required_version}
                </code>{' '}
                or newer. Installed:{' '}
                <code className="rounded bg-amber-100 dark:bg-amber-900/40 px-1 py-0.5 font-mono">
                  {bundleGate.installed_version}
                </code>
                .
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleBundleUpdate}
            disabled={triggerUpdate.isPending}
            data-testid="bundle-gate-update-cta"
            className={cn(
              'w-full rounded-lg px-4 py-2.5 text-sm font-medium transition-all',
              'bg-amber-600 text-white hover:bg-amber-700 shadow-sm',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600',
              'disabled:opacity-70 disabled:cursor-not-allowed',
            )}
          >
            {triggerUpdate.isPending ? (
              <span className="flex items-center justify-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Updating bundle…
              </span>
            ) : (
              <span className="flex items-center justify-center gap-2">
                <Download className="h-4 w-4" />
                Update VEP bundle to {bundleGate.required_version}
              </span>
            )}
          </button>
          {triggerUpdate.isError && (
            <p className="text-xs text-destructive">
              {triggerUpdate.error instanceof Error
                ? triggerUpdate.error.message
                : 'Bundle update failed. Please try again.'}
            </p>
          )}
        </div>
      )}

      {/* Error state — suppressed when the bundle-gate banner is showing
          since the 409 surfaces there. */}
      {ingestMutation.isError && !bundleGate && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-center">
          <AlertCircle className="mx-auto h-5 w-5 text-destructive" />
          <p className="mt-2 text-sm text-destructive">
            {ingestMutation.error instanceof Error
              ? ingestMutation.error.message
              : 'Failed to upload file. Please check the file and try again.'}
          </p>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          disabled={ingestMutation.isPending}
          className={cn(
            'rounded-lg border border-border px-5 py-2.5 text-sm font-medium',
            'text-foreground hover:bg-accent transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            'disabled:opacity-50 disabled:cursor-not-allowed',
          )}
        >
          Back
        </button>

        <button
          type="button"
          onClick={handleGoToDashboard}
          disabled={ingestMutation.isPending}
          className={cn(
            'rounded-lg border border-border px-5 py-2.5 text-sm font-medium',
            'text-foreground hover:bg-accent transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            'disabled:opacity-50 disabled:cursor-not-allowed',
          )}
        >
          Skip — Go to Dashboard
        </button>
      </div>
    </div>
  )
}
