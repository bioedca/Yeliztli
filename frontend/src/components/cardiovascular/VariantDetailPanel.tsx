/** Cardiovascular variant detail slide-in panel (P3-21).
 *
 * Shows full details for a selected cardiovascular P/LP variant including
 * ClinVar data, cardiovascular category, conditions, PMIDs, and inheritance.
 */

import { cn } from "@/lib/utils"
import { getClinvarSignificanceTextClass } from "@/lib/clinvar-significance"
import type { CardiovascularVariant } from "@/types/cardiovascular"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { X, ExternalLink } from "lucide-react"
import { INHERITANCE_LABELS, CATEGORY_LABELS } from "@/constants/cardiovascular"

interface VariantDetailPanelProps {
  variant: CardiovascularVariant
  onClose: () => void
}

export default function VariantDetailPanel({
  variant,
  onClose,
}: VariantDetailPanelProps) {
  return (
    <aside
      className={cn(
        "fixed right-0 top-0 bottom-0 z-40 w-full max-w-md",
        "overflow-y-auto border-l bg-background shadow-xl",
        "animate-in slide-in-from-right duration-200",
      )}
      aria-label={`${variant.gene_symbol} variant detail`}
      data-testid="variant-detail-panel"
    >
      <div className="p-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-6">
          <div>
            <h2 className="text-xl font-bold text-foreground">{variant.gene_symbol}</h2>
            <p className="text-sm font-mono text-muted-foreground">{variant.rsid}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 hover:bg-muted transition-colors"
            aria-label="Close panel"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Classification */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">Classification</h3>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">ClinVar Significance</span>
              <span className={cn(
                "text-sm font-medium",
                getClinvarSignificanceTextClass(variant.clinvar_significance),
              )}>
                {variant.clinvar_significance}
              </span>
            </div>
            {variant.clinvar_accession && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">ClinVar Accession</span>
                <span className="text-sm font-mono text-foreground">{variant.clinvar_accession}</span>
              </div>
            )}
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Review Stars</span>
              <span className="text-sm">
                {"★".repeat(variant.clinvar_review_stars)}
                {"☆".repeat(Math.max(0, 4 - variant.clinvar_review_stars))}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Evidence Level</span>
              <EvidenceStars level={variant.evidence_level} />
            </div>
          </div>
        </section>

        {/* Cardiovascular Category */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">Cardiovascular Category</h3>
          <p className="text-sm text-foreground">
            {CATEGORY_LABELS[variant.cardiovascular_category] ?? variant.cardiovascular_category}
          </p>
        </section>

        {/* Genotype */}
        {variant.genotype && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Genotype</h3>
            <p className="text-sm font-mono text-foreground">
              {variant.genotype}
              {variant.zygosity && (
                <span className="text-muted-foreground ml-2">({variant.zygosity})</span>
              )}
            </p>
          </section>
        )}

        {/* Inheritance */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">Inheritance</h3>
          <p className="text-sm text-foreground">
            {INHERITANCE_LABELS[variant.inheritance] ?? variant.inheritance}
          </p>
        </section>

        {/* ClinVar conditions */}
        {variant.clinvar_conditions && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">ClinVar Conditions</h3>
            <p className="text-sm text-foreground">{variant.clinvar_conditions}</p>
          </section>
        )}

        {/* Conditions */}
        {variant.conditions.length > 0 && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Associated Conditions</h3>
            <ul className="space-y-1">
              {variant.conditions.map((c) => (
                <li key={c} className="text-sm text-foreground">{c}</li>
              ))}
            </ul>
          </section>
        )}

        {/* PubMed references */}
        {variant.pmids.length > 0 && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">References</h3>
            <div className="flex flex-wrap gap-2">
              {variant.pmids.map((pmid) => (
                <a
                  key={pmid}
                  href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  PMID:{pmid}
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                </a>
              ))}
            </div>
          </section>
        )}
      </div>
    </aside>
  )
}
