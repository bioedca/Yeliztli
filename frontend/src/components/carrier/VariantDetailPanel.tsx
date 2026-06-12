/** Carrier variant detail slide-in panel (P3-38).
 *
 * Shows full details for a selected carrier het P/LP variant including
 * ClinVar data, conditions, inheritance, per-gene notes, PMIDs,
 * and BRCA1/2 cross-link to cancer module.
 */

import { cn } from "@/lib/utils"
import type { CarrierVariant } from "@/types/carrier"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { INHERITANCE_LABELS } from "@/types/carrier"
import { Link } from "react-router-dom"
import { X, ExternalLink, Info } from "lucide-react"

interface VariantDetailPanelProps {
  variant: CarrierVariant
  sampleId: number
  geneNote: string | undefined
  onClose: () => void
}

export default function VariantDetailPanel({
  variant,
  sampleId,
  geneNote,
  onClose,
}: VariantDetailPanelProps) {
  const hasCancerCrossLink = variant.cross_links.includes("cancer")
  const shouldMentionCancerModule = hasCancerCrossLink
  const isADOnly = variant.inheritance === "AD" && !hasCancerCrossLink
  const isAutosomalRecessive = variant.inheritance === "AR"
  const isHBBSickleTrait =
    variant.gene_symbol.toUpperCase() === "HBB" && variant.rsid.trim().toLowerCase() === "rs334"
  const usesPersonalRiskStyle = shouldMentionCancerModule || isADOnly || isHBBSickleTrait

  return (
    <aside
      className={cn(
        "fixed right-0 top-0 bottom-0 z-40 w-full max-w-md",
        "overflow-y-auto border-l bg-background shadow-xl",
        "animate-in slide-in-from-right duration-200",
      )}
      aria-label={`${variant.gene_symbol} carrier variant detail`}
      data-testid="carrier-detail-panel"
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

        {/* Carrier status banner */}
        <div
          className={cn(
            "rounded-md border p-3 mb-5",
            usesPersonalRiskStyle
              ? "bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800"
              : "bg-teal-50 dark:bg-teal-950/30 border-teal-200 dark:border-teal-800",
          )}
        >
          <p
            className={cn(
              "text-sm",
              usesPersonalRiskStyle
                ? "text-blue-800 dark:text-blue-300"
                : "text-teal-800 dark:text-teal-300",
            )}
          >
            {shouldMentionCancerModule ? (
              <>
                Heterozygous {variant.gene_symbol} variant. This information may
                be relevant for family planning and may also indicate personal
                hereditary cancer risk. Review the Cancer module for that perspective.
              </>
            ) : isADOnly ? (
              <>
                Heterozygous {variant.gene_symbol} variant. This information may
                be relevant for family planning. Review this result with a genetics
                professional.
              </>
            ) : isHBBSickleTrait ? (
              <>
                Heterozygous HBB HbS variant. This is sickle-cell trait, not
                sickle-cell disease. Sickle-cell trait is usually asymptomatic,
                but it has documented personal health associations including kidney
                findings, pulmonary embolism/VTE context, and exertional-stress
                risks such as rhabdomyolysis. Review with a clinician or genetics
                professional; this information may also be relevant for family
                planning.
              </>
            ) : isAutosomalRecessive ? (
              <>
                Heterozygous carrier - typically unaffected. This information may
                be relevant for family planning.
              </>
            ) : (
              <>
                Heterozygous carrier. This information may be relevant for family
                planning.
              </>
            )}
          </p>
        </div>

        {/* Classification */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">Classification</h3>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">ClinVar Significance</span>
              <span className={cn(
                "text-sm font-medium",
                variant.clinvar_significance === "Pathogenic" && "text-red-600 dark:text-red-400",
                variant.clinvar_significance === "Likely pathogenic" && "text-orange-600 dark:text-orange-400",
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
              <span className="text-muted-foreground ml-2">(heterozygous)</span>
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

        {/* ClinVar conditions */}
        {variant.clinvar_conditions && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">ClinVar Conditions</h3>
            <p className="text-sm text-foreground">{variant.clinvar_conditions}</p>
          </section>
        )}

        {/* Gene-specific notes */}
        {geneNote && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Gene Notes</h3>
            <p className="text-sm text-muted-foreground whitespace-pre-line">{geneNote}</p>
          </section>
        )}

        {/* Variant notes */}
        {variant.notes && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Notes</h3>
            <p className="text-sm text-muted-foreground">{variant.notes}</p>
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

        {/* BRCA1/2 cross-link to Cancer module */}
        {hasCancerCrossLink && (
          <div
            className="rounded-md bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 p-4"
            data-testid="brca-cross-link-panel"
          >
            <div className="flex items-start gap-2">
              <Info className="h-4 w-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" aria-hidden="true" />
              <div className="text-sm text-blue-800 dark:text-blue-300">
                <p className="mb-2">
                  {variant.gene_symbol} variants have implications for both cancer
                  predisposition and reproductive carrier status. View both perspectives.
                </p>
                <Link
                  to={`/cancer?sample_id=${sampleId}`}
                  className="font-medium underline hover:no-underline text-blue-700 dark:text-blue-400"
                >
                  View Cancer Predisposition
                </Link>
              </div>
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}
