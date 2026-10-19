/** Osteoporosis / heel-eBMD view (SW-B7, bring-your-own).
 *
 * A heel estimated-bone-mineral-density polygenic score (gSOS / PGS000657),
 * framed strictly as NOT a substitute for DXA or FRAX. The score is
 * non-commercially licensed → not bundled; when it is not installed the view
 * shows BYO guidance. Route-only: scored by POST /run, which the page triggers
 * only when the stored score is missing or stale (#2118).
 */

import { useSearchParams } from "react-router-dom"
import { Bone, Info } from "lucide-react"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import PRSGaugeCard from "@/components/cancer/PRSGaugeCard"
import { useEbmd, useRunEbmd } from "@/api/ebmd"
import ScoringStatus from "@/components/ui/ScoringStatus"
import NotYetScored from "@/components/ui/NotYetScored"
import WithheldResults from "@/components/ui/WithheldResults"
import { useAutoScore } from "@/hooks/useAutoScore"
import { toGaugePrs } from "@/types/metabolic"

export default function EBMDView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))
  // Keyed by sample: switching sample on this page remounts it, so its queries
  // re-check their scoring state on mount and no run state carries over (#2118).
  return <EBMDPage key={sampleId ?? "none"} sampleId={sampleId} />
}

function EBMDPage({ sampleId }: { sampleId: number | null }) {
  const run = useRunEbmd(sampleId)
  const query = useEbmd(sampleId)
  const runMutate = run.mutate
  useAutoScore({
    sampleId,
    // A failed re-check leaves the cached state unconfirmed, so it does not run.
    settled: query.data !== undefined && !query.isFetching && !query.isError,
    scoringState: query.data?.scoring_state,
    run: runMutate,
  })

  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Bone Mineral Density</h1>
        <PageEmpty icon={Bone} title="Select a sample to view the eBMD polygenic score." />
      </div>
    )
  }

  // Gate on the eBMD GET, not on `run` — the ~190 s background re-score that
  // invalidates this query on success. Blocking the page on it hid already-stored
  // results behind a multi-minute spinner on every visit, and a failed background
  // refresh must not blank the loaded results (#1992).
  const isLoading = query.isLoading
  // Only an error with nothing to show blocks the page; a failed re-fetch keeps
  // the cached results on screen with a warning (#2118).
  const hasError = query.isError && query.data === undefined
  const data = query.data

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Bone className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Bone Mineral Density (eBMD)</h1>
          <p className="text-sm text-muted-foreground">
            Research-grade fracture-risk stratification — not a DXA/FRAX substitute
          </p>
        </div>
      </div>

      {data && (
        <ScoringStatus
          scoringState={data.scoring_state}
          isScoring={run.isPending}
          error={run.error}
          refreshFailed={query.isError}
          onRescore={() => runMutate()}
        />
      )}

      {isLoading && <PageLoading message="Scoring eBMD polygenic risk..." />}
      {hasError && !isLoading && (
        <PageError
          message={query.error instanceof Error ? query.error.message : "Failed to load eBMD score."}
          onRetry={() => query.refetch()}
        />
      )}

      {!isLoading && !hasError && data && (
        <>
          {/* Framing: not a substitute */}
          <div
            className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 mb-6"
            data-testid="ebmd-context"
          >
            <div className="flex items-start gap-2">
              <Info className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
              <div className="text-sm text-amber-800 dark:text-amber-300 space-y-2">
                <p>{data.context.not_a_substitute}</p>
                {data.context.direction && <p>{data.context.direction}</p>}
                <p>{data.context.utility}</p>
              </div>
            </div>
          </div>

          {data.available && data.prs ? (
            <section aria-label="eBMD polygenic score" data-testid="ebmd-prs">
              <h2 className="text-lg font-semibold mb-3">Heel eBMD polygenic score</h2>
              <div className="max-w-sm">
                <PRSGaugeCard
                  prs={toGaugePrs({
                    trait: "heel_ebmd",
                    ...data.prs,
                    source_ancestry: "",
                    sample_size: 0,
                    genome_build: null,
                    variants_number: null,
                    source_url: data.prs.pgs_id
                      ? `https://www.pgscatalog.org/score/${data.prs.pgs_id}/`
                      : null,
                  })}
                />
              </div>
            </section>
          ) : data.withheld || data.scoring_state === "never" ? (
            // `available` is false here too, but "not installed" would be the
            // wrong reason: the score is withheld, or not computed yet (#2307).
            <section aria-label="eBMD polygenic score" data-testid="ebmd-prs-pending">
              {data.withheld ? (
                <WithheldResults label="eBMD polygenic score" />
              ) : (
                <NotYetScored label="eBMD polygenic score" />
              )}
            </section>
          ) : (
            <section aria-label="eBMD score not installed" data-testid="ebmd-byo">
              <PageEmpty
                icon={Bone}
                title={`The recommended eBMD score (${data.recommended_pgs_id}) is not installed.`}
                description={data.context.byo}
              />
            </section>
          )}
        </>
      )}
    </div>
  )
}
