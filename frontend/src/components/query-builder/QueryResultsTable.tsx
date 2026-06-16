/** Query results table (P4-02).
 *
 * Displays paginated results from POST /api/query.
 * Shows key columns from QueryVariantRow with load-more pagination.
 */

import { Loader2 } from "lucide-react"
import type { QueryResultPage, QueryVariantRow } from "@/types/query-builder"
import { formatNumber } from "@/lib/format"
import { getClinvarSignificanceBadgeClass } from "@/lib/clinvar-significance"
import ExportButton from "@/components/query-builder/ExportButton"

/** Columns displayed in the results table. */
const DISPLAY_COLUMNS: Array<{
  key: keyof QueryVariantRow
  label: string
  align?: "right" | "center"
  format?: (v: unknown) => string
}> = [
  { key: "rsid", label: "rsID" },
  { key: "chrom", label: "Chr" },
  { key: "pos", label: "Position", align: "right", format: (v) => (v != null ? formatNumber(v as number) : "—") },
  { key: "genotype", label: "Genotype" },
  { key: "gene_symbol", label: "Gene" },
  { key: "consequence", label: "Consequence" },
  { key: "clinvar_significance", label: "ClinVar" },
  { key: "clinvar_review_stars", label: "Stars", align: "center" },
  {
    key: "gnomad_af_global",
    label: "gnomAD AF",
    align: "right",
    format: (v) => (v != null ? (v as number).toExponential(2) : "—"),
  },
  { key: "cadd_phred", label: "CADD", align: "right", format: (v) => (v != null ? String(v) : "—") },
  { key: "revel", label: "REVEL", align: "right", format: (v) => (v != null ? String(v) : "—") },
]

const QUERY_EXPORT_FORMATS = ["vcf", "tsv", "json", "csv"] as const

interface QueryResultsTableProps {
  pages: QueryResultPage[]
  totalMatching: number | null
  hasMore: boolean
  isFetchingMore: boolean
  onLoadMore: () => void
  onExport?: (format: string) => void
  isExporting?: boolean
}

export default function QueryResultsTable({
  pages,
  totalMatching,
  hasMore,
  isFetchingMore,
  onLoadMore,
  onExport,
  isExporting,
}: QueryResultsTableProps) {
  const allItems = pages.flatMap((p) => p.items)

  return (
    <div data-testid="query-results-table">
      {/* Summary bar */}
      <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border border-border rounded-t-lg">
        <p className="text-sm font-medium">
          Showing {formatNumber(allItems.length)}
          {totalMatching != null && ` of ${formatNumber(totalMatching)}`} matching variants
        </p>
        {onExport && (
          <ExportButton
            formats={QUERY_EXPORT_FORMATS}
            onExport={onExport}
            disabled={allItems.length === 0}
            isPending={isExporting}
          />
        )}
      </div>

      {/* Table */}
      <div className="border border-t-0 border-border rounded-b-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/30">
                {DISPLAY_COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    className={`px-3 py-2 font-medium text-xs text-muted-foreground whitespace-nowrap ${
                      col.align === "right"
                        ? "text-right"
                        : col.align === "center"
                          ? "text-center"
                          : "text-left"
                    }`}
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {allItems.map((row, i) => (
                <tr
                  key={`${row.rsid}-${row.chrom}-${row.pos}-${i}`}
                  className="border-b border-border/50 hover:bg-muted/20 transition-colors"
                  data-testid="query-result-row"
                >
                  {DISPLAY_COLUMNS.map((col) => {
                    const raw = row[col.key]
                    const display = col.format ? col.format(raw) : (raw != null ? String(raw) : "—")
                    return (
                      <td
                        key={col.key}
                        className={`px-3 py-2 whitespace-nowrap ${
                          col.align === "right"
                            ? "text-right"
                            : col.align === "center"
                              ? "text-center"
                              : "text-left"
                        } ${col.key === "rsid" ? "font-mono text-xs" : ""}`}
                      >
                        {col.key === "clinvar_significance" && raw ? (
                          <ClinvarBadge value={String(raw)} />
                        ) : col.key === "clinvar_review_stars" && raw != null ? (
                          (() => {
                            const stars = Math.max(0, Math.min(4, Math.floor(raw as number)))
                            return (
                              <span role="img" aria-label={`${stars} stars`}>
                                {"★".repeat(stars)}{"☆".repeat(4 - stars)}
                              </span>
                            )
                          })()
                        ) : (
                          display
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
              {allItems.length === 0 && (
                <tr>
                  <td
                    colSpan={DISPLAY_COLUMNS.length}
                    className="px-3 py-8 text-center text-muted-foreground"
                  >
                    No variants match your query.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Load more */}
      {hasMore && (
        <div className="flex justify-center py-3">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={isFetchingMore}
            className="inline-flex items-center gap-2 rounded-md bg-secondary text-secondary-foreground px-4 py-2 text-sm font-medium hover:bg-secondary/80 transition-colors disabled:opacity-50"
            data-testid="load-more-btn"
          >
            {isFetchingMore ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading...
              </>
            ) : (
              "Load more"
            )}
          </button>
        </div>
      )}
    </div>
  )
}

function ClinvarBadge({ value }: { value: string }) {
  // Route severity through the shared ClinVar tone classifier so multi-word
  // vocabulary like "Conflicting classifications of pathogenicity" is not
  // mis-coloured red by a raw `.includes("pathogenic")` substring test (#799).
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${getClinvarSignificanceBadgeClass(
        value,
      )}`}
    >
      {value}
    </span>
  )
}
