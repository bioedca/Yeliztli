/** Cancer variant detail slide-in panel (P3-18).
 *
 * Shows full details for a selected cancer P/LP variant including
 * ClinVar data, syndromes, cancer types, PMIDs, and BRCA1/2 cross-link.
 */

import { cn } from "@/lib/utils"
import { getClinvarSignificanceTextClass } from "@/lib/clinvar-significance"
import type { CancerVariant } from "@/types/cancer"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { Link } from "react-router-dom"
import { X, ExternalLink, Info } from "lucide-react"

interface VariantDetailPanelProps {
  variant: CancerVariant
  sampleId: number
  onClose: () => void
}

export default function VariantDetailPanel({
  variant,
  sampleId,
  onClose,
}: VariantDetailPanelProps) {
  const hasCrossLink = variant.cross_links.includes("carrier")

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
            {variant.inheritance === "AD"
              ? "Autosomal Dominant"
              : variant.inheritance === "AR"
                ? "Autosomal Recessive"
                : variant.inheritance}
          </p>
        </section>

        {/* ClinVar conditions */}
        {variant.clinvar_conditions && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Conditions</h3>
            <p className="text-sm text-foreground">{variant.clinvar_conditions}</p>
          </section>
        )}

        {/* Syndromes */}
        {variant.syndromes.length > 0 && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Associated Syndromes</h3>
            <ul className="space-y-1">
              {variant.syndromes.map((s) => (
                <li key={s} className="text-sm text-foreground">{s}</li>
              ))}
            </ul>
          </section>
        )}

        {/* Cancer types */}
        {variant.cancer_types.length > 0 && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Cancer Types</h3>
            <div className="flex flex-wrap gap-1.5">
              {variant.cancer_types.map((ct) => (
                <span
                  key={ct}
                  className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs text-foreground"
                >
                  {ct}
                </span>
              ))}
            </div>
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

        {/* BRCA1/2 cross-link banner (P3-18) */}
        {hasCrossLink && (
          <div
            className="rounded-md bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 p-4"
            data-testid="brca-cross-link-panel"
          >
            <div className="flex items-start gap-2">
              <Info className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" aria-hidden="true" />
              <div className="text-sm text-blue-800 dark:text-blue-300">
                <p className="mb-2">
                  This variant has implications for both cancer risk and reproductive carrier
                  status. View both perspectives.
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
    </aside>
  )
}
