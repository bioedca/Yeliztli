/** Pathway detail slide-in panel for Gene Skin (P3-56).
 *
 * Shows per-SNP breakdown for a selected skin pathway with genotypes,
 * effect summaries, recommendations, MC1R allele class labels,
 * coverage notes, and PubMed literature links.
 */

import { useEffect } from "react"
import { cn } from "@/lib/utils"
import { useSkinPathwayDetail } from "@/api/skin"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { SNP_CATEGORY_COLORS, SNP_CATEGORY_DOT } from "@/lib/snpCategory"
import type { SNPDetail } from "@/types/skin"
import { X, Loader2, ExternalLink, AlertCircle, Dna, Info, AlertTriangle } from "lucide-react"

interface PathwayDetailPanelProps {
  pathwayId: string
  pathwayName: string
  sampleId: number
  onClose: () => void
}

function SNPRow({ snp }: { snp: SNPDetail }) {
  const categoryColor = SNP_CATEGORY_COLORS[snp.category] || SNP_CATEGORY_COLORS.Standard
  const dotColor = SNP_CATEGORY_DOT[snp.category] || SNP_CATEGORY_DOT.Standard

  return (
    <div className="rounded-lg border bg-card p-4">
      {/* SNP header */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <span className={cn("h-2 w-2 rounded-full shrink-0", dotColor)} />
          <div>
            <span className="font-mono text-sm font-medium text-foreground">
              {snp.rsid}
            </span>
            <span className="text-sm text-muted-foreground ml-2">
              {snp.gene} — {snp.variant_name}
            </span>
          </div>
        </div>
        <span className={cn("text-xs font-medium whitespace-nowrap", categoryColor)}>
          {snp.category}
        </span>
      </div>

      {/* Genotype + MC1R allele class label */}
      {snp.genotype && (
        <p className="text-sm mb-1">
          <span className="text-muted-foreground">Genotype: </span>
          <span className="font-mono font-medium">{snp.genotype}</span>
          {snp.mc1r_allele_class && (
            <span className="ml-2 inline-flex items-center rounded-full bg-primary/10 text-primary px-2 py-0.5 text-xs font-medium">
              MC1R {snp.mc1r_allele_class} allele
            </span>
          )}
        </p>
      )}

      {/* Effect summary */}
      <p className="text-sm text-muted-foreground mb-2">{snp.effect_summary}</p>

      {/* Insufficient data flag */}
      {snp.insufficient_data_flag && (
        <div className="flex items-start gap-2 rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 px-3 py-2 mb-2">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
          <p className="text-xs text-amber-700 dark:text-amber-300">
            Insufficient Data — result based on proxy SNP with incomplete linkage.
          </p>
        </div>
      )}

      {/* Coverage note */}
      {snp.coverage_note && !snp.insufficient_data_flag && (
        <div className="flex items-start gap-2 rounded-md bg-muted/50 px-3 py-2 mb-2">
          <Info className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" aria-hidden="true" />
          <p className="text-xs text-muted-foreground italic">{snp.coverage_note}</p>
        </div>
      )}

      {/* Recommendation */}
      {snp.recommendation && (
        <p className="text-sm text-foreground bg-muted/50 rounded px-3 py-2 mb-2">
          {snp.recommendation}
        </p>
      )}

      {/* Footer: evidence + PubMed links */}
      <div className="flex items-center justify-between gap-2 pt-2 border-t border-border/50">
        <EvidenceStars level={snp.evidence_level} />
        {snp.pmids.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap">
            {snp.pmids.map((pmid) => (
              <a
                key={pmid}
                href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                aria-label={`PubMed article ${pmid}`}
              >
                PMID:{pmid}
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function PathwayDetailPanel({
  pathwayId,
  pathwayName,
  sampleId,
  onClose,
}: PathwayDetailPanelProps) {
  const detailQuery = useSkinPathwayDetail(pathwayId, sampleId)
  const noCallSnps = detailQuery.data?.no_call_snps ?? []
  const offChipSnps = (detailQuery.data?.missing_snps ?? []).filter(
    (rsid) => !noCallSnps.includes(rsid),
  )

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [onClose])

  return (
    <aside
      className={cn(
        "fixed right-0 top-0 z-40 h-full w-full max-w-lg",
        "bg-background border-l shadow-xl",
        "flex flex-col",
        "animate-in slide-in-from-right duration-200",
      )}
      role="dialog"
      aria-modal="true"
      aria-label={`${pathwayName} pathway details`}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b px-6 py-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold truncate">{pathwayName}</h2>
          {detailQuery.data && (
            <p className="text-sm text-muted-foreground">
              {detailQuery.data.called_snps}/{detailQuery.data.total_snps} SNPs called
              {offChipSnps.length > 0 && (
                <span className="ml-1">
                  ({offChipSnps.length} not on array)
                </span>
              )}
              {noCallSnps.length > 0 && (
                <span className="ml-1">
                  ({noCallSnps.length} no-call)
                </span>
              )}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1.5 hover:bg-muted transition-colors"
          aria-label="Close pathway details"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {/* Loading */}
        {detailQuery.isLoading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* Error */}
        {detailQuery.isError && (
          <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4">
            <div className="flex items-start gap-2">
              <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
              <p className="text-sm text-destructive">
                {detailQuery.error instanceof Error
                  ? detailQuery.error.message
                  : "Failed to load pathway details."}
              </p>
            </div>
          </div>
        )}

        {/* Data */}
        {detailQuery.data && (
          <>
            {/* Pathway-level PubMed references */}
            {detailQuery.data.pmids.length > 0 && (
              <section className="mb-6" aria-label="Pathway literature references">
                <h3 className="text-sm font-semibold mb-2">Literature References</h3>
                <div className="flex flex-wrap gap-2">
                  {detailQuery.data.pmids.map((pmid) => (
                    <a
                      key={pmid}
                      href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs hover:bg-muted transition-colors"
                      aria-label={`PubMed article ${pmid}`}
                    >
                      PMID:{pmid}
                      <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  ))}
                </div>
              </section>
            )}

            {/* SNP details */}
            <section aria-label="Individual SNP results">
              <h3 className="text-sm font-semibold mb-3">
                Individual Variants ({detailQuery.data.snp_details.length})
              </h3>
              {detailQuery.data.snp_details.length > 0 ? (
                <div className="space-y-3">
                  {detailQuery.data.snp_details.map((snp) => (
                    <SNPRow key={snp.rsid} snp={snp} />
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border bg-card p-6 text-center">
                  <Dna className="h-6 w-6 text-muted-foreground mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground">
                    No variant data available for this pathway.
                  </p>
                </div>
              )}
            </section>

            {/* Missing SNPs note */}
            {(offChipSnps.length > 0 || noCallSnps.length > 0) && (
              <section className="mt-4" aria-label="Missing SNPs">
                {offChipSnps.length > 0 && (
                  <p className="text-xs text-muted-foreground italic">
                    Not on array: {offChipSnps.join(", ")}
                  </p>
                )}
                {noCallSnps.length > 0 && (
                  <p className="text-xs text-muted-foreground italic">
                    No call (on the array but the genotype read failed - may be recoverable by
                    re-testing): {noCallSnps.join(", ")}
                  </p>
                )}
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  )
}
