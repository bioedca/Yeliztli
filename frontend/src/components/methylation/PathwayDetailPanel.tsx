/** Pathway detail slide-in panel for MTHFR & Methylation (P3-53).
 *
 * Shows the per-SNP breakdown for a selected pathway with genotypes,
 * effect summaries, recommendations, coverage notes, and PubMed links.
 * Serves as the expandable "Advanced View" referenced in the PRD.
 */

import { useRef, useState } from "react"
import { useDialogFocus } from "@/hooks/useDialogFocus"
import { cn } from "@/lib/utils"
import { useMethylationPathwayDetail } from "@/api/methylation"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { SNP_CATEGORY_COLORS, SNP_CATEGORY_DOT } from "@/lib/snpCategory"
import type { SNPDetail } from "@/types/methylation"
import {
  X,
  Loader2,
  ExternalLink,
  AlertCircle,
  Dna,
  Info,
  ChevronDown,
  ChevronUp,
  TrendingUp,
} from "lucide-react"
import { isPatientResponseWithheldError } from "@/api/errors"
import WithheldResults from "@/components/ui/WithheldResults"

/** Whether a genotype is a nucleotide call whose letters can be compared with
 * a variant name. Vendor insertion/deletion tokens ("ID") cannot (#2103). */
function isNucleotideCall(snp: SNPDetail): boolean {
  return snp.genotype != null && /^[ACGT]+$/.test(snp.genotype)
}

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

      {/* Genotype */}
      {snp.genotype && (
        <p className="text-sm mb-1">
          <span className="text-muted-foreground">Genotype: </span>
          <span className="font-mono font-medium">{snp.genotype}</span>
        </p>
      )}

      {/* Effect summary */}
      <p className="text-sm text-muted-foreground mb-2">{snp.effect_summary}</p>

      {/* Coverage note */}
      {snp.coverage_note && (
        <div className="flex items-start gap-2 text-xs text-muted-foreground bg-muted/30 rounded px-3 py-2 mb-2">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" aria-hidden="true" />
          <span>{snp.coverage_note}</span>
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
  const detailQuery = useMethylationPathwayDetail(pathwayId, sampleId)
  const panelRef = useRef<HTMLElement>(null)
  useDialogFocus(panelRef, onClose)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const noCallSnps = detailQuery.data?.no_call_snps ?? []
  const offChipSnps = (detailQuery.data?.missing_snps ?? []).filter(
    (rsid) => !noCallSnps.includes(rsid),
  )

  return (
    <aside
      className={cn(
        "fixed right-0 top-0 z-40 h-full w-full max-w-lg",
        "bg-background border-l shadow-xl",
        "flex flex-col",
        "animate-in slide-in-from-right duration-200",
      )}
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-label={`${pathwayName} pathway details`}
      tabIndex={-1}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b px-6 py-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold truncate">{pathwayName}</h2>
          {detailQuery.data && (
            <div className="flex items-center gap-2">
              <p className="text-sm text-muted-foreground">
                {detailQuery.data.called_snps}/{detailQuery.data.total_snps} SNPs called
                {offChipSnps.length > 0 && (
                  <span className="ml-1">({offChipSnps.length} not on array)</span>
                )}
                {noCallSnps.length > 0 && (
                  <span className="ml-1">({noCallSnps.length} no-call)</span>
                )}
              </p>
              {detailQuery.data.additive_promoted && (
                <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300">
                  <TrendingUp className="h-3 w-3" aria-hidden="true" />
                  Additive promoted
                </span>
              )}
            </div>
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
        {detailQuery.isError && isPatientResponseWithheldError(detailQuery.error) && (
          <WithheldResults label="This pathway's" />
        )}
        {detailQuery.isError && !isPatientResponseWithheldError(detailQuery.error) && (
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

            {/* Advanced View toggle */}
            <section aria-label="Individual SNP results">
              <button
                type="button"
                className="flex items-center justify-between w-full text-sm font-semibold mb-3 hover:text-primary transition-colors"
                onClick={() => setShowAdvanced(!showAdvanced)}
                aria-expanded={showAdvanced}
                aria-controls="snp-details-list"
              >
                <span>
                  Advanced View — Individual Variants ({detailQuery.data.snp_details.length})
                </span>
                {showAdvanced ? (
                  <ChevronUp className="h-4 w-4 shrink-0" />
                ) : (
                  <ChevronDown className="h-4 w-4 shrink-0" />
                )}
              </button>

              {showAdvanced && (
                <div id="snp-details-list">
                  {/* Orientation note (#2103). Evidence and claim mapping:
                      data/science-evidence/2026-10-14-methylation-genotype-orientation-2103/
                      (accessed 2026-10-14). DNA-change names follow the gene's
                      transcriptional orientation (c.), genomic ones the reference (g.): HGVS Nomenclature v21.1.5;
                      PMID:26931183; PMID:39702242; dbSNP rs1801133
                      NC_000001.10:g.11856378G>A = NM_005957.5:c.665C>T. Arrays report
                      some variants on the other strand: PMID:28402386;
                      PMID:24517501. Base pairing: PMID:13054692. */}
                  {detailQuery.data.snp_details.some(isNucleotideCall) && (
                    <p className="flex items-start gap-2 text-xs text-muted-foreground mb-3">
                      <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" aria-hidden="true" />
                      <span>
                        Genotypes are shown as your array reported them. A variant name
                        written as a coding-DNA change, such as C677T, follows the strand its
                        gene is read from (a genomic description, written with g., follows the
                        genome&apos;s reference strand instead), and arrays report some variants
                        on the other strand. When they do, the genotype shows the
                        complementary letters (A pairs with T, C with G). Names written as
                        protein changes, such as Val158Met, use amino-acid codes and cannot be
                        compared letter by letter with a genotype.
                      </span>
                    </p>
                  )}
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
                    No call (on the array but the genotype read failed - may be
                    recoverable by re-testing): {noCallSnps.join(", ")}
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
