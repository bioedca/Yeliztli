/** Carrier variant detail slide-in panel (P3-38).
 *
 * Shows full details for a selected carrier-module finding including ClinVar
 * data, conditions, inheritance, per-gene notes, PMIDs, affected-status context,
 * and BRCA1/2 cross-link to cancer module.
 */

import { useId, useRef } from "react"
import { cn } from "@/lib/utils"
import { useDialogFocus } from "@/hooks/useDialogFocus"
import { getClinvarSignificanceTextClass } from "@/lib/clinvar-significance"
import { formatClinvarConditionsText } from "@/lib/clinvar-conditions"
import {
  DEFAULT_COPY_NUMBER_CAVEAT,
  INHERITANCE_LABELS,
  type CarrierVariant,
} from "@/types/carrier"
import EvidenceStars from "@/components/ui/EvidenceStars"
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
  const copyNumberCaveatId = useId()
  const conditions = formatClinvarConditionsText(variant.clinvar_conditions)
  const hasCancerCrossLink = variant.cross_links.includes("cancer")
  const findingType = variant.finding_type ?? "carrier"
  const isHomozygousAffected = findingType === "affected_homozygous"
  const isPossibleCompoundHet = findingType === "possible_compound_heterozygote"
  const isAffectedStatus = isHomozygousAffected || isPossibleCompoundHet
  const copyNumberCaveat =
    variant.copy_number_caveat ??
    (variant.copy_number_limited ? DEFAULT_COPY_NUMBER_CAVEAT : null)
  const hasCopyNumberCaveat = Boolean(copyNumberCaveat)
  const shouldMentionCancerModule = hasCancerCrossLink
  const isADOnly = variant.inheritance === "AD" && !hasCancerCrossLink
  const isAutosomalRecessive = variant.inheritance === "AR"
  const isHBBSickleTrait =
    variant.gene_symbol.toUpperCase() === "HBB" && variant.rsid.trim().toLowerCase() === "rs334"
  const usesPersonalRiskStyle = shouldMentionCancerModule || isADOnly || isHBBSickleTrait
  // Keep the panel's accessible name inheritance-aware too: a dominant-risk gene
  // (BRCA1/2) is not announced as a "carrier" — consistent with VariantCard. (#540)
  const isDominant = variant.inheritance === "AD"
  const componentVariants = variant.component_variants ?? []
  const hasComponentVariants = componentVariants.length > 1
  const genotypeZygosityNote = isHomozygousAffected
    ? "(homozygous affected-status)"
    : isPossibleCompoundHet
      ? "(possible compound heterozygote)"
      : isDominant
        ? "(heterozygous)"
        : "(heterozygous carrier)"
  const panelAriaLabel = isAffectedStatus
    ? `${variant.gene_symbol} affected-status finding detail`
    : `${variant.gene_symbol} ${isDominant ? "variant" : "carrier variant"} detail`
  const bannerSurfaceClass = isAffectedStatus
    ? "bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800"
    : hasCopyNumberCaveat
      ? "bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800"
    : usesPersonalRiskStyle
      ? "bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800"
      : "bg-teal-50 dark:bg-teal-950/30 border-teal-200 dark:border-teal-800"
  const bannerTextClass = isAffectedStatus
    ? "text-amber-800 dark:text-amber-300"
    : hasCopyNumberCaveat
      ? "text-amber-800 dark:text-amber-300"
    : usesPersonalRiskStyle
      ? "text-blue-800 dark:text-blue-300"
      : "text-teal-800 dark:text-teal-300"
  const panelRef = useRef<HTMLElement>(null)
  useDialogFocus(panelRef)

  return (
    <aside
      ref={panelRef}
      className={cn(
        "fixed right-0 top-0 bottom-0 z-40 w-full max-w-md",
        "overflow-y-auto border-l bg-background shadow-xl",
        "animate-in slide-in-from-right duration-200",
      )}
      role="dialog"
      aria-modal="true"
      aria-label={panelAriaLabel}
      aria-describedby={hasCopyNumberCaveat ? copyNumberCaveatId : undefined}
      tabIndex={-1}
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
            bannerSurfaceClass,
          )}
          id={hasCopyNumberCaveat ? copyNumberCaveatId : undefined}
          data-testid={hasCopyNumberCaveat ? "carrier-copy-number-caveat-panel" : undefined}
        >
          <p
            className={cn(
              "text-sm",
              bannerTextClass,
            )}
          >
            {isHomozygousAffected ? (
              <>
                Homozygous {variant.gene_symbol}{" "}
                {variant.clinvar_significance.toLowerCase()} finding. This is
                an affected-status result, not typical carrier status. Review
                this result with a clinician or genetics professional and
                confirm with clinical-grade testing.
                {copyNumberCaveat ? ` ${copyNumberCaveat}` : ""}
              </>
            ) : isPossibleCompoundHet ? (
              <>
                Two distinct {variant.gene_symbol} variants in an autosomal-recessive
                disease gene. If they are in trans, this pattern is consistent with
                affected status rather than an unaffected carrier state.{" "}
                {variant.phase_caveat ??
                  "Genotyping arrays do not phase these variants, so clinical testing is needed."}
                {copyNumberCaveat ? ` ${copyNumberCaveat}` : ""}
              </>
            ) : shouldMentionCancerModule ? (
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
            ) : hasCopyNumberCaveat ? (
              <>
                Heterozygous {variant.gene_symbol} point-variant finding.{" "}
                {copyNumberCaveat} Review this result with a genetics
                professional; this information may be relevant for family planning.
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
              <span className="text-muted-foreground ml-2">{genotypeZygosityNote}</span>
            </p>
          </section>
        )}

        {/* Component variants for aggregate affected-status findings */}
        {hasComponentVariants && (
          <section className="mb-5" data-testid="carrier-component-variants">
            <h3 className="text-sm font-semibold text-foreground mb-2">Component Variants</h3>
            <ul className="space-y-2">
              {componentVariants.map((component, index) => {
                const componentId =
                  component.rsid ||
                  [component.chrom, component.pos].filter((part) => part != null).join(":") ||
                  `variant ${index + 1}`
                return (
                  <li
                    key={`${componentId}-${index}`}
                    className="border-l-2 border-amber-300 pl-3 text-sm"
                  >
                    <div className="font-mono text-foreground">{componentId}</div>
                    <div className="text-muted-foreground">
                      {component.genotype ?? "Genotype unavailable"} · {component.zygosity}
                    </div>
                  </li>
                )
              })}
            </ul>
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
        {conditions && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">ClinVar Conditions</h3>
            <p className="text-sm text-foreground">{conditions}</p>
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
