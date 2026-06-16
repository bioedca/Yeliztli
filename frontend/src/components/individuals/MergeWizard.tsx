/** Merge wizard modal (Step 69 / MRG-05; Plan §10.7).
 *
 * Three steps:
 *   1. Strategy — radio picker over the §10.3 strategies; `flag_only` is the
 *      default per Plan §10.3 ("clinically safest — withholds the call").
 *   2. Preview — fires `POST /api/individuals/{id}/merge/preview` and
 *      renders the concordance summary + an est. duration hint.
 *   3. Confirm — display name input + `POST /api/individuals/{id}/merge`;
 *      on success, subscribes to the SSE annotation channel for the new
 *      merged sample and shows progress until terminal state, then routes
 *      to the new sample's dashboard.
 *
 * Plan §10.6: an empty `job_id` on the commit response means the merge
 * committed but annotation was not enqueued (service's `try/except`
 * warning branch); the wizard surfaces a manual re-annotate CTA instead
 * of opening an EventSource for an empty job id.
 */

import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Loader2, X } from "lucide-react"

import { useAnnotationProgress } from "@/api/annotation"
import {
  useMergeCommit,
  useMergePreview,
} from "@/api/individuals"
import type {
  ConcordanceSummary,
  LinkedSample,
  MergeStrategy,
} from "@/types/individuals"

type WizardStep = "strategy" | "preview" | "confirm"

interface MergeWizardProps {
  individualId: number
  individualDisplayName: string
  linkedSamples: LinkedSample[]
  /** Pre-selected ordered pair `[S1, S2]`. The caller (IndividualDetail) is
   * responsible for picking the two samples to merge; the wizard does not
   * surface a selector. Plan §10.5 step 1 calls for exactly two ids. */
  sourceSampleIds: [number, number]
  onClose: () => void
}

const STRATEGY_OPTIONS: ReadonlyArray<{
  value: MergeStrategy
  label: string
  description: string
}> = [
  {
    value: "flag_only",
    label: "Flag discordant calls (recommended)",
    description:
      "Discordant loci are written as `??` and skipped by analysis modules until you resolve them manually. Clinically safest — withholds a call rather than picking one.",
  },
  {
    value: "prefer_23andme",
    label: "Prefer 23andMe call",
    description:
      "At a discordant locus, keep the 23andMe genotype and record the AncestryDNA call in `discordant_alt_genotype`.",
  },
  {
    value: "prefer_ancestrydna",
    label: "Prefer AncestryDNA call",
    description:
      "Symmetric — at a discordant locus, keep the AncestryDNA genotype and record the 23andMe call in `discordant_alt_genotype`.",
  },
]

export function MergeWizard({
  individualId,
  individualDisplayName,
  linkedSamples,
  sourceSampleIds,
  onClose,
}: MergeWizardProps) {
  const navigate = useNavigate()
  const [step, setStep] = useState<WizardStep>("strategy")
  const [strategy, setStrategy] = useState<MergeStrategy>("flag_only")
  const [displayName, setDisplayName] = useState(
    `${individualDisplayName} (merged)`,
  )

  const previewMutation = useMergePreview()
  const commitMutation = useMergeCommit()

  // Bind to the annotation SSE channel once a job id arrives. The hook
  // tolerates null and tears down its EventSource when the id resets.
  const jobId = commitMutation.data?.job_id || null
  const mergedSampleId = commitMutation.data?.merged_sample_id ?? null
  const progress = useAnnotationProgress(jobId, mergedSampleId)

  // Close on Escape — mirrors the project's existing modal idiom
  // (ColumnPresets.CreatePresetDialog).
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [onClose])

  // Redirect to the new sample's dashboard once annotation reports complete.
  // Append `post_merge=1` + the SSE `job_id` so the Dashboard can open the
  // PostMergeRewatchModal on landing (Plan §10.7 redirect→modal hand-off).
  useEffect(() => {
    if (
      commitMutation.data?.merged_sample_id != null &&
      progress?.status === "complete"
    ) {
      const params = new URLSearchParams({
        sample_id: String(commitMutation.data.merged_sample_id),
        post_merge: "1",
      })
      if (commitMutation.data.job_id) {
        params.set("job_id", commitMutation.data.job_id)
      }
      navigate(`/?${params.toString()}`)
    }
  }, [commitMutation.data, progress?.status, navigate])

  const [s1Id, s2Id] = sourceSampleIds
  const s1 = linkedSamples.find((s) => s.id === s1Id)
  const s2 = linkedSamples.find((s) => s.id === s2Id)

  const handlePreview = () => {
    previewMutation.mutate(
      {
        individualId,
        data: { source_sample_ids: sourceSampleIds, strategy },
      },
      { onSuccess: () => setStep("preview") },
    )
  }

  const handleCommit = () => {
    commitMutation.mutate({
      individualId,
      data: {
        source_sample_ids: sourceSampleIds,
        strategy,
        display_name: displayName.trim(),
      },
    })
  }

  // A successful commit only keeps the Done button spinning while a real
  // annotation job is still running. An empty/missing `job_id` means
  // annotation was never enqueued (Plan §10.6) — there is no SSE channel to
  // reach a terminal state, so the button must not spin forever.
  const hasAnnotationJob = !!commitMutation.data?.job_id
  const commitInFlight =
    commitMutation.isPending ||
    (commitMutation.isSuccess &&
      hasAnnotationJob &&
      progress?.status !== "complete" &&
      progress?.status !== "failed" &&
      progress?.status !== "cancelled")

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      data-testid="merge-wizard-overlay"
    >
      <div
        className="bg-card border border-border rounded-lg shadow-xl w-full max-w-xl mx-4"
        role="dialog"
        aria-modal="true"
        aria-labelledby="merge-wizard-title"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h3 id="merge-wizard-title" className="font-medium text-sm">
            Merge samples — {individualDisplayName}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <StepIndicator current={step} />

        <div className="px-4 py-3 space-y-3">
          <p className="text-xs text-muted-foreground" data-testid="merge-source-pair">
            Merging{" "}
            <span className="font-medium text-foreground">
              {s1?.name ?? `Sample ${s1Id}`}
            </span>{" "}
            <span aria-hidden="true">→</span> S₁ &middot;{" "}
            <span className="font-medium text-foreground">
              {s2?.name ?? `Sample ${s2Id}`}
            </span>{" "}
            <span aria-hidden="true">→</span> S₂
          </p>

          {step === "strategy" && (
            <>
              <StrategyStep strategy={strategy} onChange={setStrategy} />
              {previewMutation.error && (
                <p
                  className="text-sm text-destructive"
                  role="alert"
                  data-testid="merge-preview-error"
                >
                  {previewMutation.error.message}
                </p>
              )}
            </>
          )}

          {step === "preview" && (
            <PreviewStep
              loading={previewMutation.isPending}
              errorMessage={
                previewMutation.error ? previewMutation.error.message : null
              }
              concordance={previewMutation.data?.concordance_summary ?? null}
              estDurationSeconds={
                previewMutation.data?.est_duration_seconds ?? null
              }
            />
          )}

          {step === "confirm" && (
            <ConfirmStep
              displayName={displayName}
              onDisplayNameChange={setDisplayName}
              commitInFlight={commitInFlight}
              committed={commitMutation.isSuccess}
              progressPct={progress?.progress_pct ?? null}
              progressMessage={progress?.message ?? null}
              progressStatus={progress?.status ?? null}
              jobId={commitMutation.data?.job_id ?? null}
              mergedSampleId={commitMutation.data?.merged_sample_id ?? null}
              commitErrorMessage={
                commitMutation.error ? commitMutation.error.message : null
              }
            />
          )}
        </div>

        <div className="flex justify-end gap-2 px-4 py-3 border-t border-border">
          {step === "strategy" && (
            <>
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 text-sm rounded-md border border-input bg-background hover:bg-accent text-foreground"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handlePreview}
                disabled={previewMutation.isPending}
                className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-1.5"
              >
                {previewMutation.isPending && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                )}
                Preview
              </button>
            </>
          )}

          {step === "preview" && (
            <>
              <button
                type="button"
                onClick={() => setStep("strategy")}
                className="px-3 py-1.5 text-sm rounded-md border border-input bg-background hover:bg-accent text-foreground"
              >
                Back
              </button>
              <button
                type="button"
                onClick={() => setStep("confirm")}
                disabled={previewMutation.isPending || !previewMutation.data}
                className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                Continue
              </button>
            </>
          )}

          {step === "confirm" && (
            <>
              {!commitMutation.isSuccess && (
                <button
                  type="button"
                  onClick={() => setStep("preview")}
                  disabled={commitMutation.isPending}
                  className="px-3 py-1.5 text-sm rounded-md border border-input bg-background hover:bg-accent text-foreground"
                >
                  Back
                </button>
              )}
              <button
                type="button"
                onClick={
                  commitMutation.isSuccess ? onClose : handleCommit
                }
                disabled={
                  !commitMutation.isSuccess &&
                  (commitInFlight || displayName.trim().length === 0)
                }
                className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 inline-flex items-center gap-1.5"
              >
                {commitInFlight && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                )}
                {commitMutation.isSuccess ? "Done" : "Merge"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function StepIndicator({ current }: { current: WizardStep }) {
  const steps: ReadonlyArray<{ id: WizardStep; label: string }> = [
    { id: "strategy", label: "1. Strategy" },
    { id: "preview", label: "2. Preview" },
    { id: "confirm", label: "3. Confirm" },
  ]
  return (
    <ol
      className="flex gap-2 px-4 py-2 text-xs border-b border-border"
      aria-label="Wizard progress"
    >
      {steps.map((s) => (
        <li
          key={s.id}
          aria-current={s.id === current ? "step" : undefined}
          className={
            s.id === current
              ? "font-medium text-foreground"
              : "text-muted-foreground"
          }
        >
          {s.label}
        </li>
      ))}
    </ol>
  )
}

function StrategyStep({
  strategy,
  onChange,
}: {
  strategy: MergeStrategy
  onChange: (next: MergeStrategy) => void
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium mb-1">Merge strategy</legend>
      {STRATEGY_OPTIONS.map((opt) => {
        const inputId = `merge-strategy-${opt.value}`
        return (
          <label
            key={opt.value}
            htmlFor={inputId}
            aria-label={opt.label}
            className={
              "block cursor-pointer rounded-md border p-2 " +
              (strategy === opt.value
                ? "border-primary bg-primary/5"
                : "border-input hover:bg-accent")
            }
          >
            <div className="flex items-start gap-2">
              <input
                id={inputId}
                type="radio"
                name="merge-strategy"
                value={opt.value}
                checked={strategy === opt.value}
                onChange={() => onChange(opt.value)}
                className="mt-0.5"
              />
              <div className="flex-1">
                <p className="text-sm font-medium">{opt.label}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {opt.description}
                </p>
              </div>
            </div>
          </label>
        )
      })}
    </fieldset>
  )
}

function PreviewStep({
  loading,
  errorMessage,
  concordance,
  estDurationSeconds,
}: {
  loading: boolean
  errorMessage: string | null
  concordance: ConcordanceSummary | null
  estDurationSeconds: number | null
}) {
  if (loading) {
    return (
      <p className="text-sm text-muted-foreground inline-flex items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Computing concordance…
      </p>
    )
  }
  if (errorMessage) {
    return (
      <p className="text-sm text-destructive" role="alert">
        {errorMessage}
      </p>
    )
  }
  if (!concordance) return null

  const rows: ReadonlyArray<{ key: keyof ConcordanceSummary; label: string }> = [
    { key: "match", label: "Match" },
    { key: "filled_nocall", label: "Filled no-call" },
    { key: "discordant", label: "Discordant" },
    { key: "unique_S1", label: "Unique to S₁" },
    { key: "unique_S2", label: "Unique to S₂" },
    { key: "collapsed_rsid", label: "Collapsed rsIDs" },
  ]

  return (
    <div className="space-y-2" data-testid="merge-preview-summary">
      <p className="text-sm font-medium">Concordance summary</p>
      <table className="w-full text-sm">
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.key}
              className="border-t border-border first:border-t-0"
            >
              <td className="py-1 text-muted-foreground">{row.label}</td>
              <td
                className="py-1 text-right tabular-nums font-medium"
                data-testid={`concordance-${row.key}`}
              >
                {(concordance[row.key] ?? 0).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {estDurationSeconds != null && (
        <p className="text-xs text-muted-foreground">
          Estimated merge + annotation: ~{estDurationSeconds}s.
        </p>
      )}
    </div>
  )
}

function ConfirmStep({
  displayName,
  onDisplayNameChange,
  commitInFlight,
  committed,
  progressPct,
  progressMessage,
  progressStatus,
  jobId,
  mergedSampleId,
  commitErrorMessage,
}: {
  displayName: string
  onDisplayNameChange: (next: string) => void
  commitInFlight: boolean
  committed: boolean
  progressPct: number | null
  progressMessage: string | null
  progressStatus: string | null
  jobId: string | null
  mergedSampleId: number | null
  commitErrorMessage: string | null
}) {
  // Plan §10.6: empty `job_id` ⇒ committed but annotation not enqueued.
  const annotationNotEnqueued = useMemo(
    () => committed && (jobId === null || jobId === ""),
    [committed, jobId],
  )

  return (
    <div className="space-y-3">
      <div>
        <label
          htmlFor="merge-display-name"
          className="block text-sm font-medium mb-1"
        >
          Merged sample name
        </label>
        <input
          id="merge-display-name"
          type="text"
          value={displayName}
          onChange={(event) => onDisplayNameChange(event.target.value)}
          disabled={committed}
          className="w-full px-3 py-1.5 text-sm rounded-md border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
        />
      </div>

      {commitErrorMessage && (
        <p className="text-sm text-destructive" role="alert">
          {commitErrorMessage}
        </p>
      )}

      {committed && (
        <div className="space-y-2" data-testid="merge-progress">
          <p className="text-sm">
            Merge committed — sample{" "}
            <span className="font-medium">{mergedSampleId}</span>.
          </p>

          {annotationNotEnqueued ? (
            <p className="text-xs text-muted-foreground">
              Annotation was not enqueued automatically. Trigger it from the
              sample dashboard via{" "}
              <code className="text-xs">POST /api/annotation/{mergedSampleId}</code>
              .
            </p>
          ) : (
            <>
              <div
                className="h-2 w-full rounded-full bg-muted overflow-hidden"
                role="progressbar"
                aria-valuenow={progressPct ?? 0}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Annotation progress"
              >
                <div
                  className={
                    "h-full rounded-full transition-all duration-300 " +
                    (progressStatus === "failed"
                      ? "bg-destructive"
                      : progressStatus === "complete"
                        ? "bg-green-500"
                        : "bg-primary")
                  }
                  style={{ width: `${progressPct ?? 0}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {progressMessage ?? "Annotating merged sample…"}
              </p>
            </>
          )}
        </div>
      )}

      {commitInFlight && !committed && (
        <p className="text-sm text-muted-foreground inline-flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Writing merged
          sample…
        </p>
      )}
    </div>
  )
}
