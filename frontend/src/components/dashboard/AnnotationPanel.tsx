/** Annotation status panel for the dashboard (P2-06).
 *
 * Shows a progress bar with batch granularity, ETA estimate,
 * and a cancel button. Displayed between status bar and module
 * cards when annotation is running or recently completed.
 */

import { useState, useRef, useCallback, useEffect } from "react"
import {
  useStartAnnotation,
  useCancelAnnotation,
  useAnnotationProgress,
  useActiveAnnotationJob,
  type AnnotationProgress,
} from "@/api/annotation"
import { cn } from "@/lib/utils"
import {
  FlaskConical,
  Loader2,
  CheckCircle2,
  XCircle,
  Ban,
  X,
  Play,
} from "lucide-react"

interface AnnotationPanelProps {
  sampleId: number
  /** Total raw variant count (for context in the progress message). */
  variantCount: number | null
}

/** Estimate time remaining based on progress rate. */
function useETA(progress: AnnotationProgress | null) {
  const startTimeRef = useRef<number | null>(null)
  const [eta, setEta] = useState<string | null>(null)

  useEffect(() => {
    if (!progress || progress.status !== "running") {
      startTimeRef.current = null
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setEta(null)
      return
    }

    if (startTimeRef.current === null) {
      startTimeRef.current = Date.now()
    }

    const pct = progress.progress_pct
    if (pct <= 0) {
      setEta(null)
      return
    }

    const elapsed = (Date.now() - startTimeRef.current) / 1000
    const remaining = (elapsed / pct) * (100 - pct)

    if (remaining < 60) setEta(`~${Math.ceil(remaining)}s remaining`)
    else if (remaining < 3600) setEta(`~${Math.ceil(remaining / 60)}m remaining`)
    else setEta(`~${(remaining / 3600).toFixed(1)}h remaining`)
  }, [progress])

  return eta
}

export default function AnnotationPanel({ sampleId, variantCount }: AnnotationPanelProps) {
  const [userJobId, setUserJobId] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(false)

  // Check for an already-running job on mount (e.g. page reload during annotation).
  // Derive effective jobId: prefer user-initiated job, fall back to active job from server.
  const { data: activeJob } = useActiveAnnotationJob(sampleId)
  const jobId = userJobId ?? (dismissed ? null : activeJob?.job_id ?? null)

  const setJobId = useCallback((id: string | null) => {
    setUserJobId(id)
    if (id) setDismissed(false)
  }, [])

  const startAnnotation = useStartAnnotation()
  const cancelAnnotation = useCancelAnnotation()
  const progress = useAnnotationProgress(jobId, sampleId)
  const eta = useETA(progress)

  const handleStart = useCallback(() => {
    setDismissed(false)
    startAnnotation.mutate(sampleId, {
      onSuccess: (result) => {
        setJobId(result.job_id)
      },
    })
  }, [sampleId, startAnnotation, setJobId])

  const handleCancel = useCallback(() => {
    if (jobId) {
      cancelAnnotation.mutate(jobId)
    }
  }, [jobId, cancelAnnotation])

  const handleDismiss = useCallback(() => {
    setDismissed(true)
    setJobId(null)
  }, [setJobId])

  // ── No active job: show "Run Annotation" button ──────────
  if (!jobId || dismissed) {
    return (
      <div
        className="rounded-lg border border-border bg-card p-4"
        role="region"
        aria-label="Annotation"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <FlaskConical className="h-5 w-5 text-primary" />
            <div>
              <p className="text-sm font-medium text-foreground">
                Annotation Pipeline
              </p>
              <p className="text-xs text-muted-foreground">
                {variantCount != null
                  ? `Annotate ${variantCount.toLocaleString()} variants with ClinVar, gnomAD, VEP, and dbNSFP`
                  : "Run the annotation pipeline to add clinical and functional annotations"}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleStart}
            disabled={startAnnotation.isPending}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md",
              "bg-primary text-primary-foreground hover:bg-primary/90",
              "disabled:opacity-50 disabled:cursor-not-allowed",
              "transition-colors"
            )}
          >
            {startAnnotation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Run Annotation
          </button>
        </div>
        {startAnnotation.isError && (
          <p className="mt-2 text-xs text-destructive" role="alert">
            {startAnnotation.error.message}
          </p>
        )}
      </div>
    )
  }

  // ── Active job: show progress ────────────────────────────
  return (
    <div
      className="rounded-lg border border-border bg-card p-4"
      role="region"
      aria-label="Annotation progress"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <StatusIcon status={progress?.status ?? "pending"} />
          <span className="text-sm font-medium text-foreground">
            {statusLabel(progress?.status ?? "pending")}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {eta && progress?.status === "running" && (
            <span className="text-xs text-muted-foreground">{eta}</span>
          )}
          {progress?.status === "running" || progress?.status === "pending" ? (
            <button
              type="button"
              onClick={handleCancel}
              disabled={cancelAnnotation.isPending}
              className={cn(
                "flex items-center gap-1 px-2 py-1 text-xs rounded-md",
                "border border-input bg-background hover:bg-accent",
                "text-muted-foreground hover:text-foreground",
                "disabled:opacity-50 transition-colors"
              )}
              aria-label="Cancel annotation"
            >
              <X className="h-3 w-3" />
              Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={handleDismiss}
              className="text-muted-foreground hover:text-foreground p-1 rounded transition-colors"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div
        className="h-2 w-full rounded-full bg-muted overflow-hidden"
        role="progressbar"
        aria-valuenow={progress?.progress_pct ?? 0}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Annotation progress"
      >
        <div
          className={cn(
            "h-full rounded-full transition-all duration-300",
            progress?.status === "failed"
              ? "bg-destructive"
              : progress?.status === "complete"
                ? "bg-green-500"
                : progress?.status === "cancelled"
                  ? "bg-yellow-500"
                  : "bg-primary"
          )}
          style={{ width: `${progress?.progress_pct ?? 0}%` }}
        />
      </div>

      {/* Status message */}
      <div className="mt-2 flex items-center justify-between">
        <p className="text-xs text-muted-foreground truncate">
          {progress?.message ?? "Queued for annotation..."}
        </p>
        <span className="text-xs text-muted-foreground tabular-nums ml-2 shrink-0">
          {(progress?.progress_pct ?? 0).toFixed(1)}%
        </span>
      </div>

      {/* Error message */}
      {progress?.error && (
        <p className="mt-2 text-xs text-destructive" role="alert">
          {progress.error}
        </p>
      )}
    </div>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: AnnotationProgress["status"] }) {
  switch (status) {
    case "running":
      return <Loader2 className="h-4 w-4 animate-spin text-primary" />
    case "complete":
      return <CheckCircle2 className="h-4 w-4 text-green-500" />
    case "failed":
      return <XCircle className="h-4 w-4 text-destructive" />
    case "cancelled":
      return <Ban className="h-4 w-4 text-yellow-500" />
    default:
      return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
  }
}

function statusLabel(status: AnnotationProgress["status"]): string {
  switch (status) {
    case "pending":
      return "Queued..."
    case "running":
      return "Annotating..."
    case "complete":
      return "Annotation Complete"
    case "failed":
      return "Annotation Failed"
    case "cancelled":
      return "Annotation Cancelled"
    default:
      return "Annotation"
  }
}
