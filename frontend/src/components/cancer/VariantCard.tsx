/** Cancer monogenic variant card (P3-18).
 *
 * Displays a single P/LP variant with gene, ClinVar significance,
 * review stars, syndromes, cancer types, and evidence level.
 * Shows BRCA1/2 cross-link banner when cross_links includes "carrier".
 */

import { cn } from "@/lib/utils"
import { getClinvarSignificanceCardConfig } from "@/lib/clinvar-significance"
import type { CancerVariant } from "@/types/cancer"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { Link } from "react-router-dom"
import { Info } from "lucide-react"

interface VariantCardProps {
  variant: CancerVariant
  onClick: () => void
  selected?: boolean
  sampleId: number
}

const INHERITANCE_LABELS: Record<string, string> = {
  AD: "Autosomal Dominant",
  AR: "Autosomal Recessive",
  XL: "X-linked",
  XLD: "X-linked Dominant",
  XLR: "X-linked Recessive",
  MT: "Mitochondrial",
}

export default function VariantCard({ variant, onClick, selected, sampleId }: VariantCardProps) {
  const config = getClinvarSignificanceCardConfig(variant.clinvar_significance)
  const hasCrossLink = variant.cross_links.includes("carrier")

  return (
    <div
      className={cn(
        "w-full text-left rounded-lg border p-4 transition-all",
        config.bg,
        config.border,
        selected && "ring-2 ring-primary",
      )}
    >
    <button
      type="button"
      className={cn(
        "w-full text-left cursor-pointer",
        "hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:rounded-md",
      )}
      onClick={onClick}
      aria-label={`${variant.gene_symbol} ${variant.rsid} — ${variant.clinvar_significance}`}
      data-testid="cancer-variant-card"
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
          {variant.zygosity && (
            <span className="text-muted-foreground ml-2">({variant.zygosity})</span>
          )}
        </p>
      )}

      {/* ClinVar review stars */}
      {variant.clinvar_review_stars > 0 && (
        <p className="text-xs text-muted-foreground mb-1">
          ClinVar review: {"★".repeat(variant.clinvar_review_stars)}
          {"☆".repeat(Math.max(0, 4 - variant.clinvar_review_stars))}
        </p>
      )}

      {/* Syndromes */}
      {variant.syndromes.length > 0 && (
        <p className="text-sm text-muted-foreground mb-1">
          {variant.syndromes.join(", ")}
        </p>
      )}

      {/* Cancer types */}
      {variant.cancer_types.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {variant.cancer_types.map((ct) => (
            <span
              key={ct}
              className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
            >
              {ct}
            </span>
          ))}
        </div>
      )}

      {/* ClinVar conditions */}
      {variant.clinvar_conditions && (
        <p className="text-xs text-muted-foreground mb-2 line-clamp-2">
          {variant.clinvar_conditions}
        </p>
      )}

      {/* Footer: evidence stars + inheritance */}
      <div className="flex items-center justify-between gap-2 pt-2 border-t border-border/50">
        <EvidenceStars level={variant.evidence_level} />
        <span className="text-xs text-muted-foreground">
          {INHERITANCE_LABELS[variant.inheritance] ?? variant.inheritance}
        </span>
      </div>

    </button>

      {/* BRCA1/2 cross-link banner (P3-18) */}
      {hasCrossLink && (
        <div
          className="mt-3 rounded-md bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 p-3"
          data-testid="brca-cross-link"
        >
          <div className="flex items-start gap-2">
            <Info className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" aria-hidden="true" />
            <div className="text-xs text-blue-800 dark:text-blue-300">
              <p className="mb-1">
                This variant has implications for both cancer risk and reproductive carrier status.
                View both perspectives.
              </p>
              <Link
                to={`/carrier-status?sample_id=${sampleId}`}
                className="font-medium underline hover:no-underline text-blue-700 dark:text-blue-400"
              >
                View Carrier Status
              </Link>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
