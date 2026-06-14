/** Consolidated drug-centric medication-safety report (SW-E4, roadmap #20).
 *
 * Aggregates the sample's CPIC prescribing alerts into one report: CPIC-standard
 * phenotype terms, per-gene coverage / call-confidence, a coarse actionability
 * ordering (attention-worthy drugs first), and a prominent report-level
 * reference-bias disclosure.
 *
 * The reference-bias text mirrors the backend constant
 * `MEDICATION_SAFETY_REFERENCE_BIAS` (backend/disclaimers.py) and is delivered by
 * the `GET /api/analysis/pharma/report` endpoint — this card renders it verbatim,
 * never paraphrasing the honesty disclosure.
 */

import { AlertTriangle, CheckCircle2, Info, Pill, XCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { usePharmaReport } from "@/api/pharmacogenomics"
import type {
  CallConfidence,
  CoverageInfo,
  DrugSafetyEntry,
  GeneCoverageSummary,
  ReportGeneEffect,
} from "@/types/pharmacogenomics"

interface MedicationSafetyReportProps {
  sampleId: number
}

const CONFIDENCE_CONFIG: Record<
  CallConfidence,
  { icon: typeof CheckCircle2; color: string }
> = {
  Complete: { icon: CheckCircle2, color: "text-emerald-700 dark:text-emerald-400" },
  Partial: { icon: AlertTriangle, color: "text-amber-700 dark:text-amber-400" },
  Insufficient: { icon: XCircle, color: "text-red-700 dark:text-red-400" },
}

function CoverageBadge({ coverage }: { coverage: CoverageInfo | null }) {
  if (!coverage || coverage.total === 0) return null
  return (
    <span className="text-xs text-muted-foreground" title="Assayed defining positions (SNP-level)">
      {coverage.assessed}/{coverage.total} positions
    </span>
  )
}

function ConfidenceBadge({
  confidence,
  note,
}: {
  confidence: CallConfidence | null
  note: string | null
}) {
  if (!confidence) return null
  const config = CONFIDENCE_CONFIG[confidence]
  const Icon = config.icon
  return (
    <span
      className={cn("flex items-center gap-1 text-xs font-medium", config.color)}
      title={note ?? confidence}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {confidence}
    </span>
  )
}

function GeneEffectRow({ effect }: { effect: ReportGeneEffect }) {
  return (
    <div className="rounded-md border border-border/60 bg-background/60 p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-foreground">{effect.gene}</span>
          {effect.diplotype && (
            <span className="font-mono text-xs text-muted-foreground">{effect.diplotype}</span>
          )}
          {effect.phenotype && (
            <span className="text-sm text-muted-foreground">{effect.phenotype}</span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <CoverageBadge coverage={effect.coverage} />
          <ConfidenceBadge confidence={effect.call_confidence} note={effect.confidence_note} />
          {effect.evidence_level != null && <EvidenceStars level={effect.evidence_level} />}
        </div>
      </div>

      {effect.recommendation && (
        <p className="mt-1.5 text-sm text-foreground">
          {effect.recommendation}
          {effect.classification && (
            <span className="ml-2 text-xs text-muted-foreground">
              (CPIC {effect.classification})
            </span>
          )}
        </p>
      )}

      {/* Gene-specific caveat (e.g. CYP2D6 copy-number / DPYD fatal-toxicity).
          Context only — mirrors a backend disclaimer; never overrides the result. */}
      {effect.gene_caveat && (
        <div
          className="mt-2 flex gap-1.5 rounded-md bg-amber-50 p-2 dark:bg-amber-950/30"
          role="note"
          aria-label={`${effect.gene} interpretation caveat`}
        >
          <AlertTriangle
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400"
            aria-hidden="true"
          />
          <p className="text-xs text-amber-700 dark:text-amber-300">{effect.gene_caveat}</p>
        </div>
      )}
    </div>
  )
}

function GeneCoveragePanel({ genes }: { genes: GeneCoverageSummary[] }) {
  if (genes.length === 0) return null
  return (
    <section aria-label="Per-gene coverage and confidence" className="mb-4">
      <h3 className="mb-2 text-sm font-semibold text-foreground">Per-gene coverage</h3>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {genes.map((gene) => (
          <div
            key={gene.gene}
            className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-card px-2.5 py-1.5"
          >
            <div className="flex items-baseline gap-2 truncate">
              <span className="text-sm font-semibold text-foreground">{gene.gene}</span>
              {gene.phenotype && (
                <span className="truncate text-xs text-muted-foreground">{gene.phenotype}</span>
              )}
              {gene.gene_caveat && (
                <AlertTriangle
                  className="h-3 w-3 shrink-0 text-amber-600 dark:text-amber-400"
                  aria-label={`${gene.gene} has an interpretation caveat`}
                />
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <CoverageBadge coverage={gene.coverage} />
              <ConfidenceBadge confidence={gene.call_confidence} note={gene.confidence_note} />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function DrugCard({ drug }: { drug: DrugSafetyEntry }) {
  return (
    <article
      className={cn(
        "rounded-lg border p-3",
        drug.actionable
          ? "border-amber-300 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-950/20"
          : "border-border bg-card",
      )}
      aria-label={`${drug.drug} medication safety`}
    >
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-semibold capitalize text-foreground">{drug.drug}</h3>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-xs font-medium",
            drug.actionable
              ? "bg-amber-200 text-amber-900 dark:bg-amber-900/50 dark:text-amber-200"
              : "bg-muted text-muted-foreground",
          )}
        >
          {drug.actionable ? "Review recommended" : "Routine"}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {drug.gene_effects.map((effect) => (
          <GeneEffectRow key={effect.gene} effect={effect} />
        ))}
      </div>
    </article>
  )
}

export default function MedicationSafetyReport({ sampleId }: MedicationSafetyReportProps) {
  const { data, isLoading, isError } = usePharmaReport(sampleId)

  // Additive section: stay silent while loading or on error (the rest of the
  // pharmacogenomics page still renders), and render nothing when there are no
  // pharmacogenomics findings yet (the page's own empty state covers that).
  if (isLoading || isError || !data || data.genes_assessed === 0) {
    return null
  }

  return (
    <section aria-label="Medication safety report" className="mb-8">
      <div className="mb-3 flex items-center gap-2">
        <Pill className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 className="text-lg font-semibold">Medication Safety Report</h2>
      </div>

      {/* Reference-bias disclosure — verbatim from the backend constant. */}
      <div
        role="note"
        aria-label="About this medication-safety report"
        className="mb-4 rounded-md border border-sky-200 bg-sky-50 p-3 dark:border-sky-900 dark:bg-sky-950/30"
      >
        <div className="flex items-start gap-2">
          <Info
            className="mt-0.5 h-5 w-5 shrink-0 text-sky-600 dark:text-sky-400"
            aria-hidden="true"
          />
          <p className="text-xs leading-relaxed text-sky-900 dark:text-sky-200">
            {data.reference_bias_disclosure}
          </p>
        </div>
      </div>

      {/* Summary line. */}
      <p className="mb-3 text-sm text-muted-foreground">
        {data.genes_assessed} {data.genes_assessed === 1 ? "gene" : "genes"} ·{" "}
        {data.drugs_assessed} {data.drugs_assessed === 1 ? "drug" : "drugs"} ·{" "}
        <span
          className={cn(
            data.actionable_drug_count > 0 && "font-medium text-amber-700 dark:text-amber-300",
          )}
        >
          {data.actionable_drug_count} flagged for review
        </span>
      </p>

      {/* Per-gene coverage / confidence summary (the disclosure references this). */}
      <GeneCoveragePanel genes={data.gene_coverage} />

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {data.drugs.map((drug) => (
          <DrugCard key={drug.drug} drug={drug} />
        ))}
      </div>
    </section>
  )
}
