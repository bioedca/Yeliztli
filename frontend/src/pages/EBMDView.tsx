/** Osteoporosis / heel-eBMD view (SW-B7, bring-your-own).
 *
 * A heel estimated-bone-mineral-density polygenic score (gSOS / PGS000657),
 * framed strictly as NOT a substitute for DXA or FRAX. The score is
 * non-commercially licensed → not bundled; when it is not installed the view
 * shows BYO guidance. Route-only: triggers POST /run on mount.
 */

import { useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import { Bone, Info } from "lucide-react"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import PRSGaugeCard from "@/components/cancer/PRSGaugeCard"
import { useEbmd, useRunEbmd } from "@/api/ebmd"
import { toGaugePrs } from "@/types/metabolic"

export default function EBMDView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const run = useRunEbmd(sampleId)
  const query = useEbmd(sampleId)
  const runMutate = run.mutate
  useEffect(() => {
    if (sampleId != null) runMutate()
  }, [sampleId, runMutate])

  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Bone Mineral Density</h1>
        <PageEmpty icon={Bone} title="Select a sample to view the eBMD polygenic score." />
      </div>
    )
  }

  const isLoading = run.isPending || query.isLoading
  const hasError = run.isError || query.isError
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

      {isLoading && <PageLoading message="Scoring eBMD polygenic risk..." />}
      {hasError && !isLoading && (
        <PageError
          message={run.error instanceof Error ? run.error.message : "Failed to score eBMD."}
          onRetry={() => run.mutate()}
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
