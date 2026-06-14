/** Variant detail side panel — slide-in drawer from right (P2-21).
 *
 *  Click behavior: single click on any variant row opens this panel.
 *  The full detail page is only reached via the "Open full detail" link. */

import { useEffect, useRef, useCallback } from "react"
import { Link } from "react-router-dom"
import {
  X,
  AlertTriangle,
  ExternalLink,
  Loader2,
  Dna,
  MapPin,
  Shield,
  Activity,
} from "lucide-react"

import { useVariantDetail } from "@/api/variant-detail"
import type { VariantDetail, EvidenceConflictDetail } from "@/types/variant-detail"
import WatchButton from "@/components/variant-detail/WatchButton"
import { cn } from "@/lib/utils"
import { formatAlleleFrequency } from "@/lib/format"

interface VariantDetailSidePanelProps {
  rsid: string | null
  sampleId: number | null
  onClose: () => void
}

/** Render ClinVar review stars as filled/empty unicode stars. */
function renderStars(stars: number | null): string {
  if (stars == null) return ""
  const clamped = Math.max(0, Math.min(4, stars))
  return "\u2605".repeat(clamped) + "\u2606".repeat(4 - clamped)
}

/** Format a consequence string for display (replace underscores). */
function formatConsequence(consequence: string | null): string {
  if (!consequence) return "—"
  return consequence.replace(/_/g, " ")
}

/** Section header inside the drawer. */
function SectionHeader({ icon: Icon, label }: { icon: typeof Dna; label: string }) {
  return (
    <div className="flex items-center gap-1.5 mb-2 mt-4 first:mt-0">
      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </h3>
    </div>
  )
}

/** Key-value row in the drawer. */
function DetailRow({
  label,
  value,
  className,
}: {
  label: string
  value: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex justify-between items-baseline py-0.5", className)}>
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-foreground text-right max-w-[60%] truncate">
        {value ?? "—"}
      </span>
    </div>
  )
}

/** Evidence conflict callout box. */
function EvidenceConflictSection({ detail }: { detail: EvidenceConflictDetail }) {
  if (!detail.has_conflict) return null

  return (
    <div className="mt-4 rounded-md border border-amber-500/30 bg-amber-50/50 dark:bg-amber-950/20 p-3">
      <div className="flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
        <div className="text-xs leading-relaxed text-amber-900 dark:text-amber-200">
          <p className="font-semibold mb-1">Evidence Conflict</p>
          <p>{detail.summary}</p>
          {detail.deleterious_tools.length > 0 && (
            <p className="mt-1 text-amber-700 dark:text-amber-300">
              Deleterious: {detail.deleterious_tools.join(", ")}
            </p>
          )}
          {detail.clinvar_accession && (
            <a
              href={`https://www.ncbi.nlm.nih.gov/clinvar/variation/${detail.clinvar_accession}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 mt-1.5 text-amber-700 dark:text-amber-300 hover:underline"
            >
              View ClinVar entry <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </div>
    </div>
  )
}

/** Render the loaded variant detail content. */
function PanelContent({
  variant,
  sampleId,
  onClose,
}: {
  variant: VariantDetail
  sampleId: number
  onClose: () => void
}) {
  const rareLabel = variant.ultra_rare_flag
    ? "Ultra-rare"
    : variant.rare_flag
      ? "Rare"
      : null

  return (
    <>
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-border">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-foreground truncate">
              {variant.rsid}
            </h2>
            {variant.clinvar_review_stars != null && (
              <span className="text-amber-500 text-base" title={`${variant.clinvar_review_stars}-star ClinVar review`}>
                {renderStars(variant.clinvar_review_stars)}
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-0.5 truncate">
            {[
              variant.gene_symbol,
              variant.transcript_id,
              variant.exon_number != null ? `exon ${variant.exon_number}` : null,
              variant.intron_number != null ? `intron ${variant.intron_number}` : null,
            ]
              .filter(Boolean)
              .join(" \u00B7 ") || `chr${variant.chrom}:${variant.pos.toLocaleString()}`}
          </p>
          {variant.mane_select && (
            <span className="inline-block mt-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-primary/10 text-primary">
              MANE Select
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          aria-label="Close variant detail panel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Genomic location */}
        <SectionHeader icon={MapPin} label="Location" />
        <DetailRow label="Chromosome" value={variant.chrom} />
        <DetailRow label="Position" value={variant.pos.toLocaleString()} />
        <DetailRow label="Genotype" value={variant.genotype} />
        <DetailRow label="Ref / Alt" value={`${variant.ref ?? "—"} / ${variant.alt ?? "—"}`} />
        <DetailRow label="Zygosity" value={variant.zygosity} />
        <DetailRow label="Consequence" value={formatConsequence(variant.consequence)} />

        {/* HGVS */}
        {(variant.hgvs_coding || variant.hgvs_protein) && (
          <>
            <SectionHeader icon={Dna} label="HGVS" />
            {variant.hgvs_coding && (
              <DetailRow label="Coding" value={variant.hgvs_coding} />
            )}
            {variant.hgvs_protein && (
              <DetailRow label="Protein" value={variant.hgvs_protein} />
            )}
          </>
        )}

        {/* ClinVar */}
        <SectionHeader icon={Shield} label="ClinVar" />
        <DetailRow
          label="Significance"
          value={
            variant.clinvar_significance ? (
              <span
                className={cn(
                  variant.clinvar_significance.toLowerCase().includes("pathogenic")
                    ? "text-red-600 dark:text-red-400"
                    : variant.clinvar_significance.toLowerCase().includes("benign")
                      ? "text-green-600 dark:text-green-400"
                      : "",
                )}
              >
                {variant.clinvar_significance}
              </span>
            ) : (
              "—"
            )
          }
        />
        <DetailRow label="Review" value={renderStars(variant.clinvar_review_stars) || "—"} />
        {variant.clinvar_conditions && (
          <DetailRow label="Conditions" value={variant.clinvar_conditions} />
        )}

        {/* Population frequency */}
        <SectionHeader icon={Activity} label="Frequency" />
        <DetailRow
          label="gnomAD AF"
          value={
            <span className="flex items-center gap-1.5">
              {formatAlleleFrequency(variant.gnomad_af_global)}
              {rareLabel && (
                <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">
                  {rareLabel}
                </span>
              )}
            </span>
          }
        />

        {/* In-silico predictions */}
        <SectionHeader icon={Activity} label="Predictions" />
        <DetailRow label="CADD" value={variant.cadd_phred?.toFixed(1)} />
        <DetailRow
          label="SIFT"
          value={
            variant.sift_pred ? (
              <span className="flex items-center gap-1">
                <span
                  className={cn(
                    variant.sift_pred === "D"
                      ? "text-red-600 dark:text-red-400"
                      : "text-green-600 dark:text-green-400",
                  )}
                >
                  {variant.sift_pred === "D" ? "Deleterious" : "Tolerated"}
                </span>
                {variant.sift_score != null && (
                  <span className="text-muted-foreground">({variant.sift_score.toFixed(3)})</span>
                )}
              </span>
            ) : null
          }
        />
        <DetailRow
          label="PolyPhen-2"
          value={
            variant.polyphen2_hsvar_pred ? (
              <span
                className={cn(
                  variant.polyphen2_hsvar_pred === "probably_damaging"
                    ? "text-red-600 dark:text-red-400"
                    : variant.polyphen2_hsvar_pred === "possibly_damaging"
                      ? "text-amber-600 dark:text-amber-400"
                      : "text-green-600 dark:text-green-400",
                )}
              >
                {variant.polyphen2_hsvar_pred.replace(/_/g, " ")}
              </span>
            ) : null
          }
        />
        <DetailRow label="REVEL" value={variant.revel?.toFixed(3)} />
        {variant.ensemble_pathogenic && (
          <div className="mt-1 px-2 py-1 rounded text-xs font-medium bg-red-50 dark:bg-red-950/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800/30">
            Ensemble pathogenic (≥3 tools deleterious)
          </div>
        )}

        {/* Evidence conflict */}
        {variant.evidence_conflict_detail && (
          <EvidenceConflictSection detail={variant.evidence_conflict_detail} />
        )}

        {/* Disease associations */}
        {variant.gene_phenotypes.length > 0 && (
          <>
            <SectionHeader icon={Shield} label="Disease Associations" />
            <div className="space-y-2">
              {variant.gene_phenotypes.slice(0, 5).map((gp, i) => (
                <div key={i} className="text-xs rounded border border-border p-2">
                  <p className="font-medium text-foreground">{gp.disease_name}</p>
                  <p className="text-muted-foreground mt-0.5">
                    {gp.source === "omim" ? "OMIM" : "MONDO/HPO"}
                    {gp.inheritance && ` \u00B7 ${gp.inheritance}`}
                  </p>
                  {gp.omim_link && (
                    <a
                      href={gp.omim_link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 mt-1 text-primary hover:underline"
                    >
                      OMIM <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
              ))}
              {variant.gene_phenotypes.length > 5 && (
                <p className="text-xs text-muted-foreground">
                  +{variant.gene_phenotypes.length - 5} more associations
                </p>
              )}
            </div>
          </>
        )}
      </div>

      {/* Footer with link to full detail page + watch button */}
      <div className="p-4 border-t border-border space-y-2">
        <Link
          to={`/variants/${encodeURIComponent(variant.rsid)}?sample_id=${sampleId}`}
          className="flex items-center justify-center gap-2 w-full px-4 py-2 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          Open full detail
          <ExternalLink className="h-3.5 w-3.5" />
        </Link>
        <WatchButton rsid={variant.rsid} sampleId={sampleId} />
      </div>
    </>
  )
}

export default function VariantDetailSidePanel({
  rsid,
  sampleId,
  onClose,
}: VariantDetailSidePanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const { data: variant, isLoading, error } = useVariantDetail(rsid, sampleId)
  const isOpen = rsid != null

  // Close on Escape key
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    },
    [onClose],
  )

  useEffect(() => {
    if (!isOpen) return
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [isOpen, handleKeyDown])

  // Close when clicking the overlay
  const handleOverlayClick = useCallback(
    (e: React.MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose()
      }
    },
    [onClose],
  )

  if (!isOpen) return null

  return (
    // Overlay
    <div
      className="fixed inset-0 z-40 bg-black/20 dark:bg-black/40 transition-opacity"
      onClick={handleOverlayClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onClose() }}
      role="button"
      tabIndex={0}
      aria-label="Close panel"
    >
      {/* Slide-in drawer */}
      <div
        ref={panelRef}
        role="dialog"
        aria-label={`Variant detail for ${rsid}`}
        aria-modal="true"
        className={cn(
          "fixed right-0 top-0 h-full w-full max-w-md bg-card border-l border-border shadow-xl",
          "flex flex-col",
          "animate-in slide-in-from-right duration-200",
        )}
      >
        {isLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <Loader2 className="h-6 w-6 animate-spin mx-auto text-primary" />
              <p className="text-sm text-muted-foreground mt-2">Loading variant detail...</p>
            </div>
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center p-4">
            <div className="text-center">
              <p className="text-sm text-destructive">Failed to load variant detail</p>
              <p className="text-xs text-muted-foreground mt-1">{error.message}</p>
              <button
                type="button"
                onClick={onClose}
                className="mt-3 px-3 py-1.5 text-sm rounded-md border border-input bg-background text-foreground hover:bg-accent transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        ) : variant && sampleId != null ? (
          <PanelContent variant={variant} sampleId={sampleId} onClose={onClose} />
        ) : null}
      </div>
    </div>
  )
}
