/** Carrier status gene card (P3-38).
 *
 * Displays a single het P/LP carrier variant with gene symbol,
 * ClinVar significance, conditions, inheritance, and evidence level.
 * Shows BRCA1/2 cross-link banner when cross_links includes "cancer".
 */

import { cn } from "@/lib/utils"
import { getClinvarSignificanceCardConfig } from "@/lib/clinvar-significance"
import { formatClinvarConditionsText } from "@/lib/clinvar-conditions"
import type { CarrierVariant } from "@/types/carrier"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { INHERITANCE_LABELS } from "@/types/carrier"
import { Link } from "react-router-dom"
import { Info } from "lucide-react"

interface VariantCardProps {
  variant: CarrierVariant
  onClick: () => void
  selected?: boolean
  sampleId: number
}

export default function VariantCard({ variant, onClick, selected, sampleId }: VariantCardProps) {
  const config = getClinvarSignificanceCardConfig(variant.clinvar_significance)
  const conditions = formatClinvarConditionsText(variant.clinvar_conditions)
  const hasCancerCrossLink = variant.cross_links.includes("cancer")
  // A heterozygous P/LP variant means different things by inheritance mode: for
  // autosomal-dominant genes (BRCA1/2) it confers personal disease risk and is
  // NOT a silent recessive-carrier state, so we drop the "carrier" framing there
  // and reserve it for the recessive (AR/XL) genes. Mirrors VariantDetailPanel,
  // which uses plain "(heterozygous)" and conveys risk via its banner and the
  // cancer cross-link below. (#540)
  const isDominant = variant.inheritance === "AD"
  const zygosityNote = isDominant ? "(heterozygous)" : "(heterozygous carrier)"
  // Keep the screen-reader announcement consistent with the visible label — a
  // dominant-risk variant is not announced as a "carrier" either. (#540)
  const a11yDescriptor = isDominant ? "heterozygous variant" : "carrier"

  return (
    <button
      type="button"
      className={cn(
        "w-full text-left rounded-lg border p-4 cursor-pointer transition-all",
        "hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        config.bg,
        config.border,
        selected && "ring-2 ring-primary",
      )}
      onClick={onClick}
      aria-label={`${variant.gene_symbol} ${variant.rsid} — ${a11yDescriptor}, ${variant.clinvar_significance}`}
      data-testid="carrier-variant-card"
    >
      {/* Header: gene + significance badge */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <h3 className="font-semibold text-foreground">{variant.gene_symbol}</h3>
          <p className="text-xs font-mono text-muted-foreground">{variant.rsid}</p>
        </div>
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
            config.badge,
          )}
        >
          {variant.clinvar_significance}
        </span>
      </div>

      {/* Genotype + zygosity */}
      {variant.genotype && (
        <p className="text-sm font-mono text-foreground mb-1">
          {variant.genotype}
          <span className="text-muted-foreground ml-2">{zygosityNote}</span>
        </p>
      )}

      {/* ClinVar review stars */}
      {variant.clinvar_review_stars > 0 && (
        <p className="text-xs text-muted-foreground mb-1">
          ClinVar review: {"★".repeat(variant.clinvar_review_stars)}
          {"☆".repeat(Math.max(0, 4 - variant.clinvar_review_stars))}
        </p>
      )}

      {/* Conditions */}
      {variant.conditions.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {variant.conditions.map((c) => (
            <span
              key={c}
              className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
            >
              {c}
            </span>
          ))}
        </div>
      )}

      {/* ClinVar conditions */}
      {conditions && (
        <p className="text-xs text-muted-foreground mb-2 line-clamp-2">
          {conditions}
        </p>
      )}

      {/* Footer: evidence stars + inheritance */}
      <div className="flex items-center justify-between gap-2 pt-2 border-t border-border/50">
        <EvidenceStars level={variant.evidence_level} />
        <span className="text-xs text-muted-foreground">
          {INHERITANCE_LABELS[variant.inheritance] ?? variant.inheritance}
        </span>
      </div>

      {/* BRCA1/2 cross-link to Cancer module (P3-38) */}
      {hasCancerCrossLink && (
        <div
          className="mt-3 rounded-md bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 p-3"
          data-testid="brca-cross-link"
        >
          <div className="flex items-start gap-2">
            <Info className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" aria-hidden="true" />
            <div className="text-xs text-blue-800 dark:text-blue-300">
              <p className="mb-1">
                This gene also has implications for cancer predisposition.
                View both perspectives.
              </p>
              <Link
                to={`/cancer?sample_id=${sampleId}`}
                className="font-medium underline hover:no-underline text-blue-700 dark:text-blue-400"
                onClick={(e) => e.stopPropagation()}
              >
                View Cancer Predisposition
              </Link>
            </div>
          </div>
        </div>
      )}
    </button>
  )
}
