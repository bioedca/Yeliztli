/** Drug detail slide-in panel showing per-gene effects (P3-06). */

import { useRef } from "react"
import { cn } from "@/lib/utils"
import { useDialogFocus } from "@/hooks/useDialogFocus"
import { usePharmaDrugLookup } from "@/api/pharmacogenomics"
import { usePgxGuidelines } from "@/api/pgxGuidelines"
import type { GeneEffect, CallConfidence } from "@/types/pharmacogenomics"
import type { PgxAlertSources } from "@/types/pgxGuidelines"
import {
  X,
  ExternalLink,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  MinusCircle,
  Loader2,
} from "lucide-react"

interface DrugDetailPanelProps {
  drugName: string
  sampleId: number
  onClose: () => void
}

const CONFIDENCE_ICON: Record<CallConfidence, typeof CheckCircle2> = {
  Complete: CheckCircle2,
  Partial: AlertTriangle,
  Insufficient: XCircle,
}

const CONFIDENCE_COLOR: Record<CallConfidence, string> = {
  Complete: "text-emerald-700 dark:text-emerald-400",
  Partial: "text-amber-700 dark:text-amber-400",
  Insufficient: "text-red-700 dark:text-red-400",
}

/** Context-only cross-source evidence strip (SW-E2): PharmGKB LoE + DPWG + FDA
 * for this (gene, drug) pair. Only renders when the curated table has the pair
 * (`has_sources`); absence is simply no strip, never a downgrade. */
function PgxEvidenceStrip({ sources }: { sources: PgxAlertSources }) {
  const parts: string[] = []
  if (sources.pharmgkb_loe) parts.push(`PharmGKB LoE ${sources.pharmgkb_loe}`)
  if (sources.dpwg_guideline) parts.push("DPWG ✓")
  if (sources.fda_pgx_level) parts.push(`FDA: ${sources.fda_pgx_level}`)
  if (parts.length === 0) return null

  return (
    <div className="mt-2 rounded-md border border-sky-200 bg-sky-50 p-2 dark:border-sky-900 dark:bg-sky-950/30">
      <p className="text-[11px] font-medium uppercase tracking-wide text-sky-700 dark:text-sky-400">
        Cross-source evidence
      </p>
      <p className="mt-0.5 text-xs text-sky-800 dark:text-sky-300">{parts.join(" · ")}</p>
      <p className="mt-1 text-[10px] italic text-sky-700/80 dark:text-sky-400/80">
        Context only — does not change the CPIC recommendation. PharmGKB / DPWG / FDA-label
        data via PharmGKB (CC BY-SA 4.0; share-alike).
      </p>
    </div>
  )
}

function GeneEffectCard({
  effect,
  sources,
}: {
  effect: GeneEffect
  sources?: PgxAlertSources | null
}) {
  const confidence = effect.call_confidence
  const Icon = confidence ? CONFIDENCE_ICON[confidence] : null
  const color = confidence ? CONFIDENCE_COLOR[confidence] : ""

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center justify-between gap-2 mb-2">
        <h4 className="font-semibold text-sm">{effect.gene}</h4>
        {effect.not_assessed ? (
          <span className="flex items-center gap-1 text-xs font-medium text-amber-700 dark:text-amber-400">
            <MinusCircle className="h-3.5 w-3.5" />
            Not assessed
          </span>
        ) : (
          confidence &&
          Icon && (
            <span className={cn("flex items-center gap-1 text-xs font-medium", color)}>
              <Icon className="h-3.5 w-3.5" />
              {confidence}
            </span>
          )
        )}
      </div>

      {/* Uncalled guideline gene (#905): make the missing result explicit so a bare
          "CPIC Level {x}" line can't read as evaluated-and-normal. */}
      {effect.not_assessed && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 mb-2 dark:border-amber-800 dark:bg-amber-950/30">
          <p className="text-xs text-amber-800 dark:text-amber-300">
            Not assessed — {effect.gene} could not be called from this sample's array, so its
            effect on this drug is unknown (this is not an evaluated-as-normal result).
          </p>
        </div>
      )}

      {effect.diplotype && (
        <p className="text-sm font-mono mb-1">{effect.diplotype}</p>
      )}

      {effect.metabolizer_status && (
        <p className="text-sm text-muted-foreground mb-2">{effect.metabolizer_status}</p>
      )}

      {effect.recommendation && (
        <div className="rounded-md bg-muted/50 p-3 mb-2">
          <p className="text-xs font-medium text-muted-foreground mb-1">Recommendation</p>
          <p className="text-sm">{effect.recommendation}</p>
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        {effect.classification && (
          <span>CPIC Level {effect.classification}</span>
        )}
        {effect.activity_score != null && (
          <span>Activity: {effect.activity_score}</span>
        )}
      </div>

      {effect.confidence_note && (
        <p className="text-xs text-muted-foreground italic mt-2">{effect.confidence_note}</p>
      )}

      {effect.guideline_url && (
        <a
          href={effect.guideline_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline mt-2"
        >
          CPIC Guideline <ExternalLink className="h-3 w-3" />
        </a>
      )}

      {sources?.has_sources && <PgxEvidenceStrip sources={sources} />}
    </div>
  )
}

export default function DrugDetailPanel({
  drugName,
  sampleId,
  onClose,
}: DrugDetailPanelProps) {
  const { data, isLoading, isError, error } = usePharmaDrugLookup(drugName, sampleId)
  // Additive, context-only: a failure here is swallowed (no strip) so it can
  // never blank the drug detail (cf. the #642 query-isolation pattern).
  const { data: pgxData } = usePgxGuidelines(sampleId)
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(panelRef)

  // Index the sample's cross-source evidence by gene, scoped to this drug.
  const drugLabel = (data?.drug ?? drugName).toLowerCase()
  const sourcesByGene = new Map<string, PgxAlertSources>()
  for (const a of pgxData?.alerts ?? []) {
    if (a.has_sources && a.gene_symbol && a.drug && a.drug.toLowerCase() === drugLabel) {
      sourcesByGene.set(a.gene_symbol.toUpperCase(), a)
    }
  }

  return (
    <div
      ref={panelRef}
      className={cn(
        "fixed inset-y-0 right-0 z-40 w-full max-w-md",
        "border-l bg-background shadow-xl",
        "animate-in slide-in-from-right",
        "flex flex-col",
      )}
      role="dialog"
      aria-label={`${drugName} drug detail`}
      aria-modal="true"
      tabIndex={-1}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h2 className="text-lg font-semibold">{data?.drug ?? drugName}</h2>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1.5 hover:bg-muted transition-colors"
          aria-label="Close drug detail"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {isError && (
          <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4">
            <p className="text-sm text-destructive">
              Failed to load drug details: {(error as Error).message}
            </p>
          </div>
        )}

        {data && data.gene_effects.length === 0 && (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No gene interactions found for this drug.
          </p>
        )}

        {data?.gene_effects.map((effect) => (
          <GeneEffectCard
            key={effect.gene}
            effect={effect}
            sources={sourcesByGene.get(effect.gene.toUpperCase()) ?? null}
          />
        ))}
      </div>
    </div>
  )
}
