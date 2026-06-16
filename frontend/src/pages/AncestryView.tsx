/** Ancestry module page (P3-27, P3-34, AMv2 Steps 5-6).
 *
 * Layout:
 * - Ancestry result summary card (top population, confidence, coverage, evidence)
 * - Admixture bar chart (7 populations, NNLS fractions)
 * - PCA scatter plot (user projected onto reference panel, PC selection)
 * - Analysis details (collapsible — AIMs, PCs, method, reference panel)
 * - Chromosome painting section (LAI bundle states, painting, tier comparison)
 * - Haplogroup assignments with traversal path (P3-34)
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, CheckCircle, Download, Globe, Info, Loader2, Play } from "lucide-react"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { useAncestryFindings, useHaplogroups, useLAIProgress, useLAIResults, useLAIStatus, usePCACoordinates, useTriggerLAI } from "@/api/ancestry"
import { useTriggerDownload } from "@/api/setup"
import AncestryResultCard from "@/components/ancestry/AncestryResultCard"
import AdmixtureBar from "@/components/ancestry/AdmixtureBar"
import PCAScatter from "@/components/ancestry/PCAScatter"
import HaplogroupCard from "@/components/ancestry/HaplogroupCard"
import AnalysisDetails from "@/components/ancestry/AnalysisDetails"
import ChromosomePainting from "@/components/charts/ChromosomePainting"
import AncestryPieChart from "@/components/charts/AncestryPieChart"
import LAICoverageTelemetryPanel from "@/components/ancestry/LAICoverageTelemetryPanel"
import { POPULATION_LABELS } from "@/components/ancestry/constants"

export default function AncestryView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))
  const queryClient = useQueryClient()

  const findingsQuery = useAncestryFindings(sampleId)
  const pcaQuery = usePCACoordinates(sampleId)
  const haplogroupQuery = useHaplogroups(sampleId)
  const laiStatusQuery = useLAIStatus()
  const laiResultsQuery = useLAIResults(sampleId)
  const triggerDownload = useTriggerDownload()
  const triggerLAI = useTriggerLAI()

  const [bundleDownloadStatus, setBundleDownloadStatus] = useState<
    "idle" | "starting" | "downloading" | "extracting" | "error"
  >("idle")
  const [bundleDownloadError, setBundleDownloadError] = useState<string | null>(null)
  const bundleSseRef = useRef<EventSource | null>(null)

  useEffect(() => {
    return () => {
      bundleSseRef.current?.close()
    }
  }, [])

  const handleDownloadLaiBundle = useCallback(() => {
    bundleSseRef.current?.close()
    bundleSseRef.current = null
    setBundleDownloadError(null)
    setBundleDownloadStatus("starting")
    triggerDownload.mutate(["lai_bundle"], {
      onSuccess: (result) => {
        setBundleDownloadStatus("downloading")
        const es = new EventSource(`/api/databases/progress/${result.session_id}`)
        bundleSseRef.current = es
        es.addEventListener("progress", (event: MessageEvent) => {
          const data = JSON.parse(event.data) as {
            databases: Array<{ db_name: string; status: string; progress_pct: number; error: string | null }>
          }
          const bundle = data.databases.find((db) => db.db_name === "lai_bundle")
          if (!bundle) return
          if (bundle.status === "extracting") {
            setBundleDownloadStatus("extracting")
          } else if (bundle.status === "downloading" || bundle.status === "pending") {
            setBundleDownloadStatus("downloading")
          } else if (bundle.status === "complete") {
            es.close()
            bundleSseRef.current = null
            setBundleDownloadStatus("idle")
            queryClient.invalidateQueries({ queryKey: ["lai-status"] })
          } else if (bundle.status === "failed") {
            es.close()
            bundleSseRef.current = null
            setBundleDownloadStatus("error")
            setBundleDownloadError(bundle.error || "Download failed")
          }
        })
        es.addEventListener("error", () => {
          es.close()
          bundleSseRef.current = null
          setBundleDownloadStatus("error")
          setBundleDownloadError("Lost connection to download progress stream.")
          queryClient.invalidateQueries({ queryKey: ["lai-status"] })
        })
      },
      onError: (err) => {
        setBundleDownloadStatus("error")
        setBundleDownloadError(err instanceof Error ? err.message : "Failed to start download")
      },
    })
  }, [triggerDownload, queryClient])

  // Poll for LAI progress when LAI is available but no results yet.
  // Polling stops once results are loaded or when LAI is unavailable.
  const shouldPollProgress = Boolean(
    sampleId != null
    && laiStatusQuery.data?.lai_available
    && !laiResultsQuery.data,
  )
  const laiProgressQuery = useLAIProgress(sampleId, shouldPollProgress, () => {
    // Called when progress data indicates completion — refresh results
    queryClient.invalidateQueries({ queryKey: ["lai-results", sampleId] })
  })

  // Derive job activity from progress status
  const progressStatus = laiProgressQuery.data?.status
  const laiJobActive = progressStatus === "running" || progressStatus === "pending"

  function handleTriggerLAI() {
    if (sampleId == null) return
    triggerLAI.mutate(sampleId, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["lai-progress", sampleId] })
      },
    })
  }

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Ancestry</h1>
        <PageEmpty icon={Globe} title="Select a sample to view ancestry results." />
      </div>
    )
  }

  const isLoading = findingsQuery.isLoading
  const hasError = findingsQuery.isError

  return (
    <div className="p-6">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <Globe className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Ancestry</h1>
          <p className="text-sm text-muted-foreground">
            Ancestry inference via PCA projection and NNLS admixture estimation
          </p>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && (
        <PageLoading message="Loading ancestry data..." />
      )}

      {/* Error state */}
      {hasError && !isLoading && (
        <PageError
          message={findingsQuery.error instanceof Error ? findingsQuery.error.message : "An unexpected error occurred."}
          onRetry={() => { findingsQuery.refetch(); }}
        />
      )}

      {/* No results yet */}
      {!isLoading && !hasError && !findingsQuery.data && (
        <PageEmpty
          icon={Globe}
          title="No ancestry results yet."
          description="Run the annotation pipeline to generate ancestry results."
        />
      )}

      {/* Main content */}
      {!isLoading && !hasError && findingsQuery.data && (
        <>
          {/* Ancestry Result Summary */}
          <section aria-label="Ancestry inference summary" className="mb-8">
            <AncestryResultCard finding={findingsQuery.data} />
          </section>

          {/* Admixture Bar Chart */}
          <section aria-label="Admixture proportions" className="mb-8">
            <div className="rounded-lg border bg-card p-5">
              <h2 className="text-lg font-semibold mb-3">Admixture Proportions</h2>
              <p className="text-sm text-muted-foreground mb-4">
                Estimated ancestry proportions using NNLS against reference population centroids
              </p>
              <AdmixtureBar
                admixture_fractions={findingsQuery.data.admixture_fractions}
                ci_low={findingsQuery.data.nnls_ci_low ?? undefined}
                ci_high={findingsQuery.data.nnls_ci_high ?? undefined}
              />
              {/* MID lower-precision info note (threshold matches backend MID_LOW_PRECISION_THRESHOLD) */}
              {findingsQuery.data.admixture_fractions.MID != null &&
                findingsQuery.data.admixture_fractions.MID > 0.001 &&
                findingsQuery.data.admixture_fractions.MID < 0.15 && (
                <div className="flex items-start gap-2 mt-3 p-3 rounded-md bg-amber-50 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300 text-sm">
                  <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
                  <span>
                    Middle Eastern ancestry estimates have lower precision with the current reference panel.
                  </span>
                </div>
              )}
            </div>
          </section>

          {/* PCA Scatter Plot */}
          <section aria-label="PCA scatter plot" className="mb-8">
            <div className="rounded-lg border bg-card p-5">
              <h2 className="text-lg font-semibold mb-3">PCA Projection</h2>
              <p className="text-sm text-muted-foreground mb-4">
                Your sample projected onto the reference panel PCA space
              </p>
              {pcaQuery.isLoading && (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}
              {pcaQuery.isError && (
                <div className="text-sm text-destructive">
                  Failed to load PCA coordinates.
                </div>
              )}
              {pcaQuery.data && (
                <PCAScatter pcaData={pcaQuery.data} />
              )}
              {!pcaQuery.isLoading && !pcaQuery.isError && !pcaQuery.data && (
                <div className="text-sm text-muted-foreground text-center py-8">
                  PCA coordinates not available.
                </div>
              )}
            </div>
          </section>

          {/* Analysis Details (collapsible) */}
          <section aria-label="Analysis details" className="mb-8">
            <AnalysisDetails finding={findingsQuery.data} />
          </section>

          {/* Chromosome Painting Section */}
          <section aria-label="Chromosome painting" className="mb-8">
            <div className="rounded-lg border bg-card p-5">
              <h2 className="text-lg font-semibold mb-3">Chromosome Painting</h2>

              {/* Loading LAI status */}
              {laiStatusQuery.isLoading && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Checking availability...
                </div>
              )}
              {laiStatusQuery.isError && (
                <p className="text-sm text-destructive">
                  Failed to check LAI availability.
                </p>
              )}

              {/* Bundle not downloaded */}
              {laiStatusQuery.data && !laiStatusQuery.data.bundle_downloaded && (
                <div className="space-y-3">
                  <p className="text-sm text-muted-foreground">
                    Enable chromosome-level ancestry painting for detailed per-chromosome ancestry breakdown.
                    Requires a one-time ~500 MB download and Java 8+. Analysis takes 15-30 minutes.
                  </p>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      disabled={bundleDownloadStatus !== "idle" && bundleDownloadStatus !== "error"}
                      onClick={handleDownloadLaiBundle}
                      className={cn(
                        "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
                        "bg-primary text-primary-foreground hover:bg-primary/90 transition-colors",
                        "disabled:opacity-50 disabled:cursor-not-allowed",
                      )}
                    >
                      {bundleDownloadStatus === "downloading" || bundleDownloadStatus === "extracting" || bundleDownloadStatus === "starting" ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Download className="h-4 w-4" />
                      )}
                      {bundleDownloadStatus === "starting" && "Starting..."}
                      {bundleDownloadStatus === "downloading" && "Downloading bundle..."}
                      {bundleDownloadStatus === "extracting" && "Extracting bundle..."}
                      {(bundleDownloadStatus === "idle" || bundleDownloadStatus === "error") &&
                        "Enable Chromosome Painting (~500 MB)"}
                    </button>
                  </div>
                  {bundleDownloadError && (
                    <p className="text-sm text-destructive">{bundleDownloadError}</p>
                  )}
                </div>
              )}

              {/* Java missing */}
              {laiStatusQuery.data && laiStatusQuery.data.bundle_downloaded && !laiStatusQuery.data.java_available && (
                <div className="flex items-start gap-2 rounded-md bg-amber-500/10 border border-amber-500/30 px-4 py-3">
                  <Info className="h-4 w-4 text-amber-500 mt-0.5 flex-shrink-0" />
                  <p className="text-sm text-amber-700 dark:text-amber-400">
                    Java 8+ is required for chromosome-level ancestry analysis. Please install Java and restart.
                  </p>
                </div>
              )}

              {/* LAI available — show trigger, progress, or results */}
              {laiStatusQuery.data && laiStatusQuery.data.lai_available && (
                <>
                  {/* LAI Progress Card */}
                  {laiJobActive && laiProgressQuery.data && (
                    <div data-testid="lai-progress-card" className="space-y-3">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Loader2 className="h-4 w-4 animate-spin text-primary" />
                        Chromosome Painting Analysis
                      </div>
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span>{laiProgressQuery.data.message || "Starting..."}</span>
                          <span>{Math.round(laiProgressQuery.data.progress_pct)}%</span>
                        </div>
                        <div className="h-2 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-primary transition-all duration-500"
                            style={{ width: `${laiProgressQuery.data.progress_pct}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  )}

                  {/* LAI Failed */}
                  {!laiJobActive && laiProgressQuery.data?.status === "failed" && (
                    <div className="flex items-start gap-2 rounded-md bg-destructive/10 border border-destructive/30 px-4 py-3 mb-3">
                      <AlertTriangle className="h-4 w-4 text-destructive mt-0.5 flex-shrink-0" />
                      <div className="text-sm">
                        <p className="font-medium text-destructive">Analysis failed</p>
                        <p className="text-muted-foreground">{laiProgressQuery.data.error ?? "Unknown error"}</p>
                      </div>
                    </div>
                  )}

                  {/* Trigger button — show when no results and not running */}
                  {!laiJobActive && !laiResultsQuery.data && (
                    <div className="space-y-3">
                      <p className="text-sm text-muted-foreground">
                        LAI bundle is ready. Run chromosome painting to see detailed per-chromosome ancestry breakdown.
                      </p>
                      <button
                        type="button"
                        disabled={triggerLAI.isPending}
                        onClick={handleTriggerLAI}
                        className={cn(
                          "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
                          "bg-primary text-primary-foreground hover:bg-primary/90 transition-colors",
                          "disabled:opacity-50 disabled:cursor-not-allowed",
                        )}
                      >
                        <Play className="h-4 w-4" />
                        {triggerLAI.isPending ? "Starting..." : "Run Chromosome Painting Analysis (~20 min)"}
                      </button>
                      {triggerLAI.isError && (
                        <p className="text-sm text-destructive">
                          {triggerLAI.error instanceof Error ? triggerLAI.error.message : "Failed to start analysis."}
                        </p>
                      )}
                    </div>
                  )}

                  {/* LAI Results — Painting + Pie Chart */}
                  {laiResultsQuery.data && (
                    <div className="space-y-6">
                      <div className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400">
                        <CheckCircle className="h-4 w-4" />
                        Chromosome painting complete
                      </div>

                      {/* LAI coverage telemetry (Step 24, Plan §6.7) */}
                      {laiResultsQuery.data.coverage_telemetry && (
                        <LAICoverageTelemetryPanel
                          telemetry={laiResultsQuery.data.coverage_telemetry}
                          sampleId={sampleId}
                        />
                      )}

                      {/* Chromosome Painting */}
                      <ChromosomePainting painting={laiResultsQuery.data.chromosome_painting} />

                      {/* LAI Global Ancestry Pie + Tier Comparison */}
                      <div data-testid="tier-comparison" className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
                        <div>
                          <h3 className="text-sm font-semibold mb-2">
                            Tier 2: Chromosome Painting
                          </h3>
                          <AncestryPieChart globalAncestry={laiResultsQuery.data.global_ancestry} />
                        </div>
                        <div>
                          <h3 className="text-sm font-semibold mb-2">
                            Tier 1: Instant Analysis (NNLS)
                          </h3>
                          <div className="space-y-2 pt-2">
                            {Object.entries(findingsQuery.data?.admixture_fractions ?? {})
                              .filter(([, v]) => v >= 0.001)
                              .sort(([, a], [, b]) => b - a)
                              .map(([pop, frac]) => {
                                const ciLow = findingsQuery.data?.nnls_ci_low?.[pop]
                                const ciHigh = findingsQuery.data?.nnls_ci_high?.[pop]
                                const halfWidth = ciLow != null && ciHigh != null
                                  ? (ciHigh - ciLow) / 2 * 100
                                  : null
                                return (
                                <div key={pop} className="flex items-center gap-2 text-sm">
                                  <span className="w-28 text-muted-foreground">
                                    {POPULATION_LABELS[pop] ?? pop}
                                  </span>
                                  <div className="flex-1 h-4 bg-muted rounded overflow-hidden">
                                    <div
                                      className="h-full rounded"
                                      style={{
                                        width: `${frac * 100}%`,
                                        backgroundColor: laiResultsQuery.data!.global_ancestry[pop]?.color ?? "#94A3B8",
                                      }}
                                    />
                                  </div>
                                  <span className="w-16 text-right text-muted-foreground">
                                    {(frac * 100).toFixed(1)}%
                                    {halfWidth != null && halfWidth > 0.05 && (
                                      <span className="text-xs"> {"\u00B1"}{halfWidth.toFixed(1)}</span>
                                    )}
                                  </span>
                                </div>
                                )
                              })}
                          </div>
                        </div>
                      </div>

                      {/* Concordance note */}
                      <TierConcordance
                        tier1={findingsQuery.data?.admixture_fractions ?? {}}
                        tier2={laiResultsQuery.data.global_ancestry}
                      />

                      {/* MID lower-precision warning from LAI */}
                      {laiResultsQuery.data.global_ancestry.MID?.warning && (
                        <div className="flex items-start gap-2 mt-3 p-3 rounded-md bg-amber-50 dark:bg-amber-950/30 text-amber-800 dark:text-amber-300 text-sm">
                          <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
                          <span>{laiResultsQuery.data.global_ancestry.MID.warning}</span>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </section>

          {/* Haplogroup Assignments (P3-34) */}
          <section aria-label="Haplogroup assignments">
            {haplogroupQuery.isLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            )}
            {haplogroupQuery.isError && (
              <div className="text-sm text-destructive">
                Failed to load haplogroup data.
              </div>
            )}
            {haplogroupQuery.data && (
              <HaplogroupCard assignments={haplogroupQuery.data.assignments} />
            )}
          </section>
        </>
      )}
    </div>
  )
}

/** Concordance note comparing Tier 1 NNLS vs Tier 2 LAI global ancestry. */
function TierConcordance({
  tier1,
  tier2,
}: {
  tier1: Record<string, number>
  tier2: Record<string, { fraction: number; percentage: number; display_name: string; color: string }>
}) {
  // Compute max absolute difference across populations
  const allPops = new Set([...Object.keys(tier1), ...Object.keys(tier2)])
  let maxDiff = 0
  for (const pop of allPops) {
    const t1 = tier1[pop] ?? 0
    const t2 = tier2[pop]?.fraction ?? 0
    maxDiff = Math.max(maxDiff, Math.abs(t1 - t2))
  }
  const maxDiffPct = Math.round(maxDiff * 100)
  const isSignificant = maxDiffPct > 15

  return (
    <div
      data-testid="tier-concordance"
      className={cn(
        "flex items-start gap-2 rounded-md px-4 py-3 text-sm",
        isSignificant
          ? "bg-amber-500/10 border border-amber-500/30"
          : "bg-muted/50 border border-border",
      )}
    >
      <Info className={cn("h-4 w-4 mt-0.5 flex-shrink-0", isSignificant ? "text-amber-500" : "text-muted-foreground")} />
      <p className={isSignificant ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground"}>
        {isSignificant
          ? `The instant and chromosome-level analyses differ by up to ${maxDiffPct}%. The chromosome painting result uses phased haplotype data and is generally more accurate.`
          : `The instant and chromosome-level analyses agree within ${maxDiffPct}%.`}
      </p>
    </div>
  )
}
