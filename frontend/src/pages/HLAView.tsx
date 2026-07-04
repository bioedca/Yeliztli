/** HLA (imputed) view (Wave D).
 *
 * Surfaces clinical interpretations of a sample's imputed classical-HLA calls.
 * HLA is imputed from SNP genotypes (not directly typed), so every section is
 * framed as a screening lead requiring confirmatory clinical HLA typing, and is
 * never valid for transplant/donor matching. SW-D2 adds the drug-hypersensitivity
 * section; SW-D3–D5 add rule-outs, susceptibility, and the raw viewer.
 */

import { useSearchParams } from "react-router-dom"
import {
  AlertTriangle,
  Download,
  HelpCircle,
  Info,
  Moon,
  ShieldAlert,
  ShieldCheck,
  Syringe,
  Wheat,
} from "lucide-react"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import {
  useHlaAlleles,
  useHlaDrugHypersensitivity,
  useHlaRuleOuts,
  useHlaSusceptibility,
} from "@/api/hla"
import type {
  CeliacRuleOut,
  HlaDrugRiskAssessment,
  HlaDrugRiskStatus,
  HlaSusceptibilityFinding,
  HlaSusceptibilityStatus,
  HlaViewerResponse,
  NarcolepsyRuleOut,
} from "@/types/hla"

const STATUS_ORDER: Record<HlaDrugRiskStatus, number> = {
  at_risk: 0,
  no_risk_allele: 1,
  not_typed: 2,
}

const HLA_DOCS_URL = "https://bioedca.github.io/Yeliztli/modules/hla/"

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

type RuleOutTone = "reassuring" | "non_diagnostic" | "unknown"

const TONE_STYLE: Record<RuleOutTone, { box: string; badge: string }> = {
  reassuring: {
    box: "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/20",
    badge: "bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200",
  },
  non_diagnostic: {
    box: "border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/20",
    badge: "bg-amber-100 dark:bg-amber-900 text-amber-800 dark:text-amber-200",
  },
  unknown: {
    box: "border-border bg-muted/40",
    badge: "bg-muted text-muted-foreground",
  },
}

const CELIAC_STATUS: Record<CeliacRuleOut["status"], { tone: RuleOutTone; label: string }> = {
  rule_out: { tone: "reassuring", label: "Very unlikely" },
  permissive_present: { tone: "non_diagnostic", label: "Permissive HLA — non-diagnostic" },
  not_typed: { tone: "unknown", label: "Not typed" },
}

const NARCO_STATUS: Record<NarcolepsyRuleOut["status"], { tone: RuleOutTone; label: string }> = {
  absent_lowers: { tone: "reassuring", label: "DQB1*06:02 absent — argues against NT1" },
  present: { tone: "non_diagnostic", label: "DQB1*06:02 present — non-diagnostic" },
  not_typed: { tone: "unknown", label: "Not typed" },
}

function RuleOutCard({
  testid,
  title,
  icon: Icon,
  tone,
  label,
  interpretation,
  detected,
  lowConfidence,
}: {
  testid: string
  title: string
  icon: typeof Wheat
  tone: RuleOutTone
  label: string
  interpretation: string
  detected?: string[]
  lowConfidence: boolean
}) {
  const style = TONE_STYLE[tone]
  return (
    <div className={`rounded-lg border p-4 ${style.box}`} data-testid={testid} data-tone={tone}>
      <div className="flex items-start justify-between gap-3">
        <h3 className="flex items-center gap-2 font-semibold">
          <Icon className="h-4 w-4" />
          {title}
        </h3>
        <span
          className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ${style.badge}`}
        >
          {label}
        </span>
      </div>
      <p className="mt-3 text-sm">{interpretation}</p>
      {detected && detected.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {detected.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      )}
      {lowConfidence && (
        <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">
          Low-confidence imputation call — interpret with extra caution.
        </p>
      )}
    </div>
  )
}

const SUSC_STYLE: Record<
  HlaSusceptibilityStatus,
  { label: string; icon: typeof ShieldAlert; box: string; badge: string }
> = {
  increased_risk: {
    label: "Increased susceptibility",
    icon: ShieldAlert,
    box: "border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/20",
    badge: "bg-amber-100 dark:bg-amber-900 text-amber-800 dark:text-amber-200",
  },
  not_increased: {
    label: "No increased risk",
    icon: ShieldCheck,
    box: "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/20",
    badge: "bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200",
  },
  neutral_subtype: {
    label: "Neutral subtype",
    icon: HelpCircle,
    box: "border-border bg-muted/40",
    badge: "bg-muted text-muted-foreground",
  },
  not_typed: {
    label: "Not typed",
    icon: HelpCircle,
    box: "border-border bg-muted/40",
    badge: "bg-muted text-muted-foreground",
  },
}

function SusceptibilityCard({ f }: { f: HlaSusceptibilityFinding }) {
  const style = SUSC_STYLE[f.status]
  const Icon = style.icon
  return (
    <div
      className={`rounded-lg border p-4 ${style.box}`}
      data-testid={`hla-susc-${f.hla}`}
      data-status={f.status}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">{f.condition}</h3>
          <p className="text-sm text-muted-foreground">
            {f.hla} — {f.detail}
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${style.badge}`}
        >
          <Icon className="h-3.5 w-3.5" />
          {style.label}
        </span>
      </div>
      <p className="mt-3 text-sm">{f.interpretation}</p>
      {f.low_confidence && (
        <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">
          Low-confidence imputation call — interpret with extra caution.
        </p>
      )}
      {f.notes.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {f.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
      {f.citations.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          {f.citations.map((c, i) => {
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

function downloadHlaCsv(view: HlaViewerResponse, sampleId: number): void {
  const header = ["locus", "allele1", "allele2", "prob", "low_confidence", "source", "ancestry_model"]
  const escape = (v: string | number | boolean) => {
    const s = String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const rows = view.alleles.map((a) =>
    [a.locus, a.allele1, a.allele2, a.prob ?? "", a.low_confidence, a.source, a.ancestry_model ?? ""]
      .map(escape)
      .join(","),
  )
  // The never-for-transplant guard travels IN the export so it can't be detached.
  const body = [`# ${view.transplant_guard}`, header.join(","), ...rows].join("\n") + "\n"
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv;charset=utf-8" }))
  const link = document.createElement("a")
  link.href = url
  link.download = `hla_imputed_sample_${sampleId}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Defer the revoke: revoking synchronously after click() can intermittently
  // break the download before it starts in some browsers (e.g. Firefox).
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

function RawHlaViewer({ view, sampleId }: { view: HlaViewerResponse; sampleId: number }) {
  return (
    <section aria-label="Raw imputed HLA types" data-testid="hla-viewer" className="mt-8">
      <div className="flex items-center justify-between gap-3 mb-1">
        <h2 className="text-lg font-semibold">Raw imputed HLA types</h2>
        <button
          type="button"
          onClick={() => downloadHlaCsv(view, sampleId)}
          data-testid="hla-viewer-download"
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-sm hover:bg-muted"
        >
          <Download className="h-4 w-4" />
          Download CSV
        </button>
      </div>

      {/* Load-bearing SW-D5 guard: imputed HLA is never valid for donor matching. */}
      <div
        className="rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-4 my-3"
        data-testid="hla-transplant-guard"
        role="alert"
      >
        <div className="flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-red-600 dark:text-red-400 mt-0.5 shrink-0" />
          <p className="text-sm font-medium text-red-800 dark:text-red-300">
            {view.transplant_guard}
          </p>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Locus</th>
              <th className="px-3 py-2 font-medium">Alleles</th>
              <th className="px-3 py-2 font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {view.alleles.map((a) => (
              <tr key={a.locus} className="border-t border-border" data-testid={`hla-allele-${a.locus}`}>
                <td className="px-3 py-2 font-medium">HLA-{a.locus}</td>
                <td className="px-3 py-2">
                  {a.locus}*{a.allele1} / {a.locus}*{a.allele2}
                </td>
                <td className="px-3 py-2">
                  {a.low_confidence ? (
                    <span className="text-amber-700 dark:text-amber-400">
                      low{a.prob != null ? ` (${a.prob.toFixed(2)})` : ""}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">
                      {a.prob != null ? a.prob.toFixed(2) : "—"}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function HLAView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))
  const query = useHlaDrugHypersensitivity(sampleId)
  const ruleOuts = useHlaRuleOuts(sampleId)
  const susceptibility = useHlaSusceptibility(sampleId)
  const alleles = useHlaAlleles(sampleId)

  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">HLA (imputed)</h1>
        <PageEmpty icon={Syringe} title="Select a sample to view imputed-HLA results." />
      </div>
    )
  }

  const data = query.data
  const ro = ruleOuts.data
  const su = susceptibility.data
  const av = alleles.data
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
                action={{
                  label: "HLA setup docs",
                  onClick: () => window.open(HLA_DOCS_URL, "_blank", "noopener,noreferrer"),
                }}
              />
            )}
          </section>
        </>
      )}

      {ro?.available && (
        <section aria-label="HLA disease rule-outs" data-testid="hla-rule-outs" className="mt-8">
          <h2 className="text-lg font-semibold mb-1">Disease rule-outs</h2>
          <p className="text-sm text-muted-foreground mb-3">
            High negative-predictive-value HLA tests: absence makes the disease unlikely; presence
            is common and non-diagnostic.
          </p>
          <div className="space-y-3">
            {ro.celiac && (
              <RuleOutCard
                testid="hla-rule-out-celiac"
                title="Celiac disease (HLA-DQ)"
                icon={Wheat}
                tone={CELIAC_STATUS[ro.celiac.status].tone}
                label={CELIAC_STATUS[ro.celiac.status].label}
                interpretation={ro.celiac.interpretation}
                detected={ro.celiac.detected}
                lowConfidence={ro.celiac.low_confidence}
              />
            )}
            {ro.narcolepsy && (
              <RuleOutCard
                testid="hla-rule-out-narcolepsy"
                title="Narcolepsy type 1 (HLA-DQB1*06:02)"
                icon={Moon}
                tone={NARCO_STATUS[ro.narcolepsy.status].tone}
                label={NARCO_STATUS[ro.narcolepsy.status].label}
                interpretation={ro.narcolepsy.interpretation}
                lowConfidence={ro.narcolepsy.low_confidence}
              />
            )}
          </div>
        </section>
      )}

      {su?.available && (
        <section
          aria-label="HLA autoimmune susceptibility"
          data-testid="hla-susceptibility"
          className="mt-8"
        >
          <h2 className="text-lg font-semibold mb-1">Autoimmune susceptibility</h2>
          <p className="text-sm text-muted-foreground mb-3">
            HLA associations with autoimmune conditions — susceptibility markers only, not
            diagnostic. Most carriers never develop the condition.
          </p>
          <div className="space-y-3">
            {su.findings.map((f) => (
              <SusceptibilityCard key={f.hla} f={f} />
            ))}
          </div>
        </section>
      )}

      {av?.available && sampleId != null && <RawHlaViewer view={av} sampleId={sampleId} />}
    </div>
  )
}
