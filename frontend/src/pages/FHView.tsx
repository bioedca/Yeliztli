/** Familial hypercholesterolemia view (SW-B6).
 *
 * Composes monogenic FH (LDLR/APOB/PCSK9), the APOB R3527Q / rs5742904 (FDB)
 * variant, and an LDL-C polygenic score — framed against the Dutch Lipid Clinic
 * Network / Simon Broome criteria, explicitly NOT a clinical FH diagnosis.
 * Route-only: triggers POST /run on mount, then renders the assessment.
 */

import { useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import { HeartPulse, AlertTriangle } from "lucide-react"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import PRSGaugeCard from "@/components/cancer/PRSGaugeCard"
import { useFhAssessment, useRunFh } from "@/api/fh"
import { toGaugePrs } from "@/types/metabolic"

export default function FHView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const run = useRunFh(sampleId)
  const query = useFhAssessment(sampleId)
  const runMutate = run.mutate
  useEffect(() => {
    if (sampleId != null) runMutate()
  }, [sampleId, runMutate])

  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Familial Hypercholesterolemia</h1>
        <PageEmpty icon={HeartPulse} title="Select a sample to view the FH assessment." />
      </div>
    )
  }

  const isLoading = run.isPending || query.isLoading
  const hasError = run.isError || query.isError
  const a = query.data

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <HeartPulse className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Familial Hypercholesterolemia</h1>
          <p className="text-sm text-muted-foreground">
            Monogenic + polygenic LDL-C genetics — not a clinical FH diagnosis
          </p>
        </div>
      </div>

      {isLoading && <PageLoading message="Assessing FH genetics..." />}
      {hasError && !isLoading && (
        <PageError
          message={run.error instanceof Error ? run.error.message : "Failed to assess FH."}
          onRetry={() => run.mutate()}
        />
      )}

      {!isLoading && !hasError && a && (
        <>
          {/* Disclaimer / criteria framing */}
          <div
            className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 mb-6"
            data-testid="fh-criteria"
          >
            <div className="flex items-start gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
              <div className="text-sm text-amber-800 dark:text-amber-300 space-y-2">
                <p>{a.criteria_context.disclaimer}</p>
                <p>{a.criteria_context.dutch_lipid}</p>
                <p>{a.criteria_context.simon_broome}</p>
              </div>
            </div>
          </div>

          {/* Monogenic FH */}
          <section aria-label="Monogenic FH variants" className="mb-8">
            <h2 className="text-lg font-semibold mb-3">Monogenic findings (LDLR / APOB / PCSK9)</h2>
            {a.has_monogenic ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {a.monogenic.map((m, i) => (
                  <article
                    key={`${m.gene}-${m.rsid ?? "na"}-${i}`}
                    className="rounded-lg border bg-card p-4"
                    data-testid="fh-monogenic-card"
                  >
                    <h3 className="font-semibold text-sm">
                      {m.gene}{" "}
                      <span className="font-normal text-muted-foreground">{m.rsid ?? ""}</span>
                    </h3>
                    <p className="text-sm text-foreground">{m.clinvar_significance}</p>
                    <p className="text-xs text-muted-foreground">Zygosity: {m.zygosity}</p>
                  </article>
                ))}
              </div>
            ) : (
              <PageEmpty
                icon={HeartPulse}
                title="No reportable monogenic FH variant detected."
                description="This does not exclude FH; arrays cover a limited variant set."
              />
            )}
          </section>

          {/* APOB FDB highlight */}
          {a.apob_fdb && (
            <section aria-label="APOB familial defective apoB-100" className="mb-8">
              <h2 className="text-lg font-semibold mb-3">APOB familial defective apoB-100</h2>
              <article
                className="rounded-lg border bg-card p-4 max-w-xl"
                data-testid="fh-apob-fdb-card"
              >
                <h3 className="font-semibold text-sm">
                  APOB {a.apob_fdb.protein}{" "}
                  <span className="font-normal text-muted-foreground">{a.apob_fdb.rsid}</span>
                </h3>
                <p className="text-sm">
                  Genotype <span className="font-mono">{a.apob_fdb.genotype}</span>
                  {a.apob_fdb.is_pathogenic ? " — pathogenic carrier" : ""}
                </p>
                {a.apob_fdb.clinvar_significance && (
                  <p className="text-xs text-muted-foreground">
                    ClinVar: {a.apob_fdb.clinvar_significance}
                  </p>
                )}
              </article>
            </section>
          )}

          {/* LDL-C polygenic score */}
          {a.ldl_prs && (
            <section aria-label="LDL-C polygenic score" data-testid="fh-ldl-prs">
              <h2 className="text-lg font-semibold mb-3">LDL-C polygenic score</h2>
              <div className="max-w-sm">
                <PRSGaugeCard
                  prs={toGaugePrs({
                    trait: "ldl_cholesterol",
                    ...a.ldl_prs,
                    source_ancestry: "",
                    sample_size: 0,
                    source_url: a.ldl_prs.pgs_id
                      ? `https://www.pgscatalog.org/score/${a.ldl_prs.pgs_id}/`
                      : null,
                    genome_build: null,
                    variants_number: null,
                  })}
                />
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
