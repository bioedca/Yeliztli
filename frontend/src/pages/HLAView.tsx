/** HLA (imputed) view (Wave D).
 *
 * Surfaces clinical interpretations of a sample's imputed classical-HLA calls.
 * HLA is imputed from SNP genotypes (not directly typed), so every section is
 * framed as a screening lead requiring confirmatory clinical HLA typing, and is
 * never valid for transplant/donor matching. SW-D2 adds the drug-hypersensitivity
 * section; SW-D3–D5 add rule-outs, susceptibility, and the raw viewer.
 */

import { useSearchParams } from "react-router-dom"
import { HelpCircle, Info, ShieldAlert, ShieldCheck, Syringe } from "lucide-react"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { useHlaDrugHypersensitivity } from "@/api/hla"
import type { HlaDrugRiskAssessment, HlaDrugRiskStatus } from "@/types/hla"

const STATUS_ORDER: Record<HlaDrugRiskStatus, number> = {
  at_risk: 0,
  no_risk_allele: 1,
  not_typed: 2,
}

const STATUS_STYLE: Record<
  HlaDrugRiskStatus,
  { label: string; icon: typeof ShieldAlert; box: string; badge: string }
> = {
  at_risk: {
    label: "Risk allele carried",
    icon: ShieldAlert,
    box: "border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30",
    badge: "bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200",
  },
  no_risk_allele: {
    label: "Risk allele not detected",
    icon: ShieldCheck,
    box: "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/20",
    badge: "bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200",
  },
  not_typed: {
    label: "Not typed",
    icon: HelpCircle,
    box: "border-border bg-muted/40",
    badge: "bg-muted text-muted-foreground",
  },
}

function pubmedUrl(citation: string): string | null {
  const m = /^PMID:(\d+)$/.exec(citation)
  return m ? `https://pubmed.ncbi.nlm.nih.gov/${m[1]}/` : null
}

function DrugRiskCard({ a }: { a: HlaDrugRiskAssessment }) {
  const style = STATUS_STYLE[a.status]
  const Icon = style.icon
  return (
    <div
      className={`rounded-lg border p-4 ${style.box}`}
      data-testid={`hla-drug-${a.allele}`}
      data-status={a.status}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">{a.allele}</h3>
          <p className="text-sm text-muted-foreground">
            {a.drugs.join(", ")} — {a.reaction}
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${style.badge}`}
        >
          <Icon className="h-3.5 w-3.5" />
          {style.label}
          {a.carried && a.zygosity ? ` (${a.zygosity})` : ""}
        </span>
      </div>

      <p className="mt-3 text-sm">{a.recommendation}</p>

      {a.low_confidence && (
        <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">
          Low-confidence imputation call — interpret with extra caution.
        </p>
      )}

      {a.notes.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {a.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}

      {a.citations.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          {a.guideline}:{" "}
          {a.citations.map((c, i) => {
            const url = pubmedUrl(c)
            return (
              <span key={c}>
                {i > 0 && ", "}
                {url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline hover:text-foreground"
                  >
                    {c}
                  </a>
                ) : (
                  c
                )}
              </span>
            )
          })}
        </p>
      )}
    </div>
  )
}

export default function HLAView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))
  const query = useHlaDrugHypersensitivity(sampleId)

  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">HLA (imputed)</h1>
        <PageEmpty icon={Syringe} title="Select a sample to view imputed-HLA results." />
      </div>
    )
  }

  const data = query.data
  const sorted = data?.assessments
    ? [...data.assessments].sort((x, y) => STATUS_ORDER[x.status] - STATUS_ORDER[y.status])
    : []

  return (
    <div className="p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Syringe className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">HLA (imputed)</h1>
          <p className="text-sm text-muted-foreground">
            Imputed classical-HLA typing — a screening lead, not a clinical HLA result
          </p>
        </div>
      </div>

      {query.isLoading && <PageLoading message="Loading imputed-HLA results..." />}
      {query.isError && !query.isLoading && (
        <PageError
          message={query.error instanceof Error ? query.error.message : "Failed to load HLA results."}
          onRetry={() => query.refetch()}
        />
      )}

      {!query.isLoading && !query.isError && data && (
        <>
          {data.caveat && (
            <div
              className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 mb-6"
              data-testid="hla-caveat"
            >
              <div className="flex items-start gap-2">
                <Info className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
                <p className="text-sm text-amber-800 dark:text-amber-300">{data.caveat}</p>
              </div>
            </div>
          )}

          <section aria-label="HLA drug hypersensitivity" data-testid="hla-drug-hypersensitivity">
            <h2 className="text-lg font-semibold mb-3">Drug hypersensitivity</h2>
            {data.available ? (
              <div className="space-y-3">
                {sorted.map((a) => (
                  <DrugRiskCard key={a.allele} a={a} />
                ))}
              </div>
            ) : (
              <PageEmpty
                icon={Syringe}
                title="No imputed HLA calls for this sample."
                description={data.unavailable_note ?? undefined}
              />
            )}
          </section>
        </>
      )}
    </div>
  )
}
