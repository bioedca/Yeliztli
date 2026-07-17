/** Metabolic module page (SW-B5): type 2 diabetes & obesity.
 *
 * Genome-wide PGS Catalog scores (T2D PGS000713; multi-ancestry BMI PGS005198)
 * with honest coverage reporting (percentile withheld on un-imputed array data),
 * plus established anchor SNPs (TCF7L2, FTO, MC4R). Route-only module: the view
 * triggers POST /run on mount, then renders the stored results.
 */

import { useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import { Activity, FlaskConical, Loader2 } from "lucide-react"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import SectionError from "@/components/ui/SectionError"
import PRSGaugeCard from "@/components/cancer/PRSGaugeCard"
import TraitArchitectureCard from "@/components/ui/TraitArchitectureCard"
import { useMetabolicAnchors, useMetabolicPRS, useRunMetabolic } from "@/api/metabolic"
import { toGaugePrs } from "@/types/metabolic"
import type { MetabolicAnchor } from "@/types/metabolic"

export function AnchorCard({ anchor }: { anchor: MetabolicAnchor }) {
  return (
    <article
      className="rounded-lg border bg-card p-4"
      data-testid="metabolic-anchor-card"
      aria-label={`${anchor.gene} ${anchor.rsid} anchor variant`}
    >
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <h3 className="font-semibold text-sm text-foreground">
          {anchor.gene} <span className="font-normal text-muted-foreground">{anchor.rsid}</span>
        </h3>
        <span className="text-xs text-muted-foreground">{anchor.trait_label}</span>
      </div>
      <p className="text-sm text-foreground">
        Genotype <span className="font-mono">{anchor.genotype}</span> —{" "}
        {anchor.indeterminate ? (
          <span className="text-amber-700 dark:text-amber-500" data-testid="anchor-indeterminate">
            effect-allele dosage not reported (genotype strand could not be resolved)
          </span>
        ) : (
          <>
            {anchor.dosage} × {anchor.effect_allele} effect allele
          </>
        )}
      </p>
      <p className="text-xs text-muted-foreground mt-1">{anchor.summary}</p>
    </article>
  )
}

export default function MetabolicView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const run = useRunMetabolic(sampleId)
  const prsQuery = useMetabolicPRS(sampleId)
  const anchorsQuery = useMetabolicAnchors(sampleId)

  // Route-only module → compute once on mount for the selected sample.
  const runMutate = run.mutate
  useEffect(() => {
    if (sampleId != null) runMutate()
  }, [sampleId, runMutate])

  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Metabolic (T2D &amp; Obesity)</h1>
        <PageEmpty icon={Activity} title="Select a sample to view metabolic polygenic results." />
      </div>
    )
  }

  // Gate the page on the PRS GET only — the stored results the page renders
  // (~12 ms) — not on `run`, the ~190 s background re-score (#1992). `run`
  // invalidates prsQuery on success, so fresh numbers refresh in place; blocking
  // the whole page on it hid already-stored results behind a 3-minute spinner on
  // every visit. `run.isError` is likewise excluded: a failed background refresh
  // must not blank the successfully-loaded results after they render. This is
  // the same narrowing #642 applied to the anchor-SNP query.
  const isLoading = prsQuery.isLoading
  const hasError = prsQuery.isError

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Activity className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Metabolic (T2D &amp; Obesity)</h1>
          <p className="text-sm text-muted-foreground">
            Polygenic scores with honest coverage + established anchor variants
          </p>
        </div>
      </div>

      {isLoading && <PageLoading message="Scoring metabolic polygenic risk..." />}

      {hasError && !isLoading && (
        <PageError
          message={
            prsQuery.error instanceof Error
              ? prsQuery.error.message
              : "Failed to load metabolic scores."
          }
          onRetry={() => prsQuery.refetch()}
        />
      )}

      {!isLoading && !hasError && (
        <>
          <section aria-label="Metabolic polygenic risk scores" data-testid="metabolic-prs-tier">
            <div className="flex items-center gap-2 mb-2">
              <h2 className="text-lg font-semibold">Polygenic Risk Scores</h2>
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-300 px-2.5 py-0.5 text-xs font-medium">
                <FlaskConical className="h-3 w-3" aria-hidden="true" />
                Research Use Only
              </span>
            </div>
            {prsQuery.data && prsQuery.data.coverage_context && (
              <p className="text-sm text-muted-foreground mb-4">{prsQuery.data.coverage_context}</p>
            )}
            {prsQuery.data && prsQuery.data.items.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {prsQuery.data.items.map((prs) => (
                  <PRSGaugeCard key={prs.trait} prs={toGaugePrs(prs)} />
                ))}
              </div>
            ) : (
              <PageEmpty
                icon={Activity}
                title="No polygenic scores available."
                description="The PGS score bundle may not be installed."
              />
            )}
          </section>

          {/* Established anchor SNPs — secondary section: render its own
              error/spinner so an anchors failure or slow load never hides the
              PRS results above (#642). Hidden entirely only when it resolved
              with no items. */}
          {(anchorsQuery.isError ||
            anchorsQuery.isLoading ||
            (anchorsQuery.data && anchorsQuery.data.items.length > 0)) && (
            <section aria-label="Established anchor SNPs" className="mt-8">
              <h2 className="text-lg font-semibold mb-1">Established variants</h2>
              <p className="text-sm text-muted-foreground mb-4">
                Directly-typed, large-effect common variants — interpretable on their own.
              </p>
              {anchorsQuery.isError ? (
                <SectionError
                  label="established anchor SNPs"
                  message={
                    anchorsQuery.error instanceof Error ? anchorsQuery.error.message : undefined
                  }
                  onRetry={() => anchorsQuery.refetch()}
                />
              ) : anchorsQuery.isLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {anchorsQuery.data?.items.map((a) => (
                    <AnchorCard key={a.rsid} anchor={a} />
                  ))}
                </div>
              )}
            </section>
          )}

          <TraitArchitectureCard />
        </>
      )}
    </div>
  )
}
