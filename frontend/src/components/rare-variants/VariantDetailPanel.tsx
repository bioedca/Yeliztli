/** Rare variant detail slide-in panel (P3-30).
 *
 * Shows full details for a selected rare variant including
 * population frequencies, prediction scores, ClinVar data, and HGVS.
 */

import { cn } from "@/lib/utils"
import { getClinvarSignificanceTextClass } from "@/lib/clinvar-significance"
import { formatAlleleFrequency } from "@/lib/format"
import type { RareVariant } from "@/types/rare-variants"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { X, ExternalLink } from "lucide-react"

interface VariantDetailPanelProps {
  variant: RareVariant
  onClose: () => void
}

const POPULATION_LABELS: Record<string, string> = {
  gnomad_af_global: "Global",
  gnomad_af_afr: "African/African American",
  gnomad_af_amr: "Latino/Admixed American",
  gnomad_af_eas: "East Asian",
  gnomad_af_eur: "European (non-Finnish)",
  gnomad_af_fin: "Finnish",
  gnomad_af_sas: "South Asian",
}


export default function VariantDetailPanel({ variant, onClose }: VariantDetailPanelProps) {
  const popFreqs = [
    { key: "gnomad_af_global", value: variant.gnomad_af_global },
    { key: "gnomad_af_afr", value: variant.gnomad_af_afr },
    { key: "gnomad_af_amr", value: variant.gnomad_af_amr },
    { key: "gnomad_af_eas", value: variant.gnomad_af_eas },
    { key: "gnomad_af_eur", value: variant.gnomad_af_eur },
    { key: "gnomad_af_fin", value: variant.gnomad_af_fin },
    { key: "gnomad_af_sas", value: variant.gnomad_af_sas },
  ]

  const hasAnyFreq = popFreqs.some((p) => p.value != null)

  return (
    <aside
      className={cn(
        "fixed right-0 top-0 bottom-0 z-40 w-full max-w-md",
        "overflow-y-auto border-l bg-background shadow-xl",
        "animate-in slide-in-from-right duration-200",
      )}
      aria-label={`${variant.gene_symbol ?? variant.rsid} variant detail`}
      data-testid="variant-detail-panel"
    >
      <div className="p-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-6">
          <div>
            <h2 className="text-xl font-bold text-foreground">
              {variant.gene_symbol ?? "Unknown Gene"}
            </h2>
            <p className="text-sm font-mono text-muted-foreground">{variant.rsid}</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              chr{variant.chrom}:{variant.pos.toLocaleString()}
            </p>
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

        {/* Genotype */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">Genotype</h3>
          <div className="space-y-1.5">
            {variant.genotype && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Genotype</span>
                <span className="text-sm font-mono">{variant.genotype}</span>
              </div>
            )}
            {variant.zygosity && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Zygosity</span>
                <span className="text-sm">{variant.zygosity === "hom_alt" ? "Homozygous" : "Heterozygous"}</span>
              </div>
            )}
            {variant.ref && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Ref / Alt</span>
                <span className="text-sm font-mono">{variant.ref} / {variant.alt ?? "—"}</span>
              </div>
            )}
          </div>
        </section>

        {/* Consequence & HGVS */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">Functional Annotation</h3>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Consequence</span>
              <span className="text-sm">{variant.consequence?.replace(/_/g, " ") ?? "—"}</span>
            </div>
            {variant.hgvs_coding && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">HGVS Coding</span>
                <span className="text-sm font-mono text-xs break-all text-right max-w-[200px]">{variant.hgvs_coding}</span>
              </div>
            )}
            {variant.hgvs_protein && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">HGVS Protein</span>
                <span className="text-sm font-mono text-xs break-all text-right max-w-[200px]">{variant.hgvs_protein}</span>
              </div>
            )}
          </div>
        </section>

        {/* ClinVar */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">ClinVar</h3>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Significance</span>
              <span className={cn(
                "text-sm font-medium",
                getClinvarSignificanceTextClass(variant.clinvar_significance),
              )}>
                {variant.clinvar_significance ?? "—"}
              </span>
            </div>
            {variant.clinvar_review_stars != null && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Review Stars</span>
                <span className="text-sm">
                  {"★".repeat(variant.clinvar_review_stars)}
                  {"☆".repeat(Math.max(0, 4 - variant.clinvar_review_stars))}
                </span>
              </div>
            )}
            {variant.clinvar_accession && (
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Accession</span>
                <a
                  href={`https://www.ncbi.nlm.nih.gov/clinvar/${variant.clinvar_accession}/`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-sm text-primary hover:underline font-mono"
                >
                  {variant.clinvar_accession}
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                </a>
              </div>
            )}
            {variant.clinvar_conditions && (
              <div>
                <span className="text-sm text-muted-foreground">Conditions</span>
                <p className="text-sm mt-0.5">{variant.clinvar_conditions}</p>
              </div>
            )}
          </div>
        </section>

        {/* Disease / Inheritance */}
        {(variant.disease_name || variant.inheritance_pattern) && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Disease</h3>
            <div className="space-y-1.5">
              {variant.disease_name && (
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Disease</span>
                  <span className="text-sm text-right max-w-[200px]">{variant.disease_name}</span>
                </div>
              )}
              {variant.inheritance_pattern && (
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Inheritance</span>
                  <span className="text-sm">{variant.inheritance_pattern}</span>
                </div>
              )}
            </div>
          </section>
        )}

        {/* Prediction Scores */}
        <section className="mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-2">Prediction Scores</h3>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">CADD Phred</span>
              <span className={cn(
                "text-sm font-mono",
                variant.cadd_phred != null && variant.cadd_phred >= 20 && "text-red-700 dark:text-red-400",
              )}>
                {variant.cadd_phred?.toFixed(1) ?? "—"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">REVEL</span>
              <span className={cn(
                "text-sm font-mono",
                variant.revel != null && variant.revel >= 0.5 && "text-red-700 dark:text-red-400",
              )}>
                {variant.revel?.toFixed(4) ?? "—"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Ensemble Pathogenic</span>
              <span className={cn(
                "text-sm font-medium",
                variant.ensemble_pathogenic ? "text-red-700 dark:text-red-400" : "text-muted-foreground",
              )}>
                {variant.ensemble_pathogenic ? "Yes (≥3 tools)" : "No"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Evidence Level</span>
              <EvidenceStars level={variant.evidence_level} />
            </div>
            {variant.evidence_conflict && (
              <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
                ⚠ Evidence conflict detected between annotation sources
              </p>
            )}
          </div>
        </section>

        {/* Population Frequencies */}
        {hasAnyFreq && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Population Frequencies</h3>
            <div className="space-y-1.5">
              {popFreqs.map(({ key, value }) => (
                <div key={key} className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">{POPULATION_LABELS[key]}</span>
                  <span className="text-sm font-mono">{formatAlleleFrequency(value)}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        {!hasAnyFreq && (
          <section className="mb-5">
            <h3 className="text-sm font-semibold text-foreground mb-2">Population Frequencies</h3>
            <p className="text-sm text-muted-foreground">
              Not found in gnomAD (novel variant)
            </p>
          </section>
        )}
      </div>
    </aside>
  )
}
