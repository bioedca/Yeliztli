/** Rare variant finder results table (P3-30).
 *
 * Tabular display of search results with sorting by evidence level,
 * significance color coding, and click-to-select for detail panel.
 */

import { cn } from "@/lib/utils"
import { formatAlleleFrequency } from "@/lib/format"
import type { RareVariant } from "@/types/rare-variants"
import EvidenceStars from "@/components/ui/EvidenceStars"

const SIGNIFICANCE_COLORS: Record<string, string> = {
  Pathogenic: "text-red-600 dark:text-red-400",
  "Likely pathogenic": "text-orange-600 dark:text-orange-400",
  "Uncertain significance": "text-yellow-600 dark:text-yellow-400",
}

interface ResultsTableProps {
  items: RareVariant[]
  selectedRsid: string | null
  onSelect: (variant: RareVariant) => void
}

export default function ResultsTable({ items, selectedRsid, onSelect }: ResultsTableProps) {
  if (items.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-8 text-center" data-testid="no-results">
        <p className="text-muted-foreground">
          No rare variants match the current filter criteria.
        </p>
        <p className="text-xs text-muted-foreground mt-2">
          Try adjusting the allele frequency threshold or removing gene/consequence filters.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border overflow-hidden" data-testid="results-table">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="text-left px-3 py-2 font-medium">Gene</th>
              <th className="text-left px-3 py-2 font-medium">rsID</th>
              <th className="text-left px-3 py-2 font-medium">Consequence</th>
              <th className="text-left px-3 py-2 font-medium">ClinVar</th>
              <th className="text-right px-3 py-2 font-medium">gnomAD AF</th>
              <th className="text-right px-3 py-2 font-medium">CADD</th>
              <th className="text-right px-3 py-2 font-medium">REVEL</th>
              <th className="text-left px-3 py-2 font-medium">Zygosity</th>
              <th className="text-center px-3 py-2 font-medium">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {items.map((v) => (
              <tr
                key={`${v.chrom}-${v.pos}-${v.rsid}`}
                className={cn(
                  "border-b cursor-pointer transition-colors",
                  "hover:bg-muted/30",
                  selectedRsid === v.rsid && "bg-primary/5 ring-1 ring-inset ring-primary/20",
                )}
                onClick={() => onSelect(v)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    onSelect(v)
                  }
                }}
                tabIndex={0}
                role="button"
                aria-label={`${v.gene_symbol ?? "Unknown"} ${v.rsid}`}
                data-testid="result-row"
              >
                <td className="px-3 py-2 font-medium">{v.gene_symbol ?? "—"}</td>
                <td className="px-3 py-2 font-mono text-xs">{v.rsid}</td>
                <td className="px-3 py-2 text-xs">
                  {v.consequence?.replace(/_/g, " ") ?? "—"}
                </td>
                <td className={cn("px-3 py-2 text-xs", SIGNIFICANCE_COLORS[v.clinvar_significance ?? ""])}>
                  {v.clinvar_significance ?? "—"}
                  {v.evidence_conflict && (
                    <span className="ml-1 text-amber-500" role="img" aria-label="Evidence conflict" title="Evidence conflict">⚠</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {v.gnomad_af_global != null
                    ? formatAlleleFrequency(v.gnomad_af_global)
                    : "Novel"}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {v.cadd_phred?.toFixed(1) ?? "—"}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {v.revel?.toFixed(3) ?? "—"}
                </td>
                <td className="px-3 py-2 text-xs">
                  {v.zygosity === "hom_alt" ? "Hom" : v.zygosity === "het" ? "Het" : "—"}
                </td>
                <td className="px-3 py-2 text-center">
                  <EvidenceStars level={v.evidence_level} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
