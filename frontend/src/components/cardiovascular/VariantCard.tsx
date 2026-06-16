/** Cardiovascular monogenic variant card (P3-21).
 *
 * Displays a single P/LP variant with gene, ClinVar significance,
 * review stars, cardiovascular category, conditions, and evidence level.
 */

import { cn } from "@/lib/utils"
import { getClinvarSignificanceCardConfig } from "@/lib/clinvar-significance"
import type { CardiovascularVariant } from "@/types/cardiovascular"
import EvidenceStars from "@/components/ui/EvidenceStars"
import {
  INHERITANCE_LABELS,
  CATEGORY_CONFIG,
  DEFAULT_CATEGORY,
} from "@/constants/cardiovascular"

interface VariantCardProps {
  variant: CardiovascularVariant
  onClick: () => void
  selected?: boolean
}

export default function VariantCard({ variant, onClick, selected }: VariantCardProps) {
  const config = getClinvarSignificanceCardConfig(variant.clinvar_significance)
  const catConfig = CATEGORY_CONFIG[variant.cardiovascular_category] || DEFAULT_CATEGORY

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
      aria-label={`${variant.gene_symbol} ${variant.rsid} — ${variant.clinvar_significance}`}
      data-testid="cardiovascular-variant-card"
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

      {/* Cardiovascular category badge */}
      {variant.cardiovascular_category && (
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium mb-2",
            catConfig.badge,
          )}
          data-testid="category-badge"
        >
          {catConfig.label}
        </span>
      )}

      {/* Genotype + zygosity. Zygosity is a separate API field, so render it even
          when genotype is absent rather than nesting it inside the genotype gate (#802). */}
      {(variant.genotype || variant.zygosity) && (
        <p className="text-sm font-mono text-foreground mb-1">
          {variant.genotype}
          {variant.zygosity && (
            <span className={variant.genotype ? "text-muted-foreground ml-2" : "text-muted-foreground"}>
              ({variant.zygosity})
            </span>
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
  )
}
