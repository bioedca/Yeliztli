/** Query results table (P4-02).
 *
 * Displays paginated results from POST /api/query.
 * Shows key columns from QueryVariantRow with load-more pagination.
 */

import { Loader2 } from "lucide-react"
import type { QueryResultPage, QueryVariantRow } from "@/types/query-builder"
import { formatAlleleFrequency, formatNumber } from "@/lib/format"
import { getClinvarSignificanceBadgeClass } from "@/lib/clinvar-significance"
import { cn } from "@/lib/utils"
import { CADD_TOOLTIP, REVEL_TOOLTIP, SCORE_TOOLTIP_AFFORDANCE } from "@/lib/inSilicoScoreInfo"
import ExportButton from "@/components/query-builder/ExportButton"

/** Columns displayed in the results table. */
const DISPLAY_COLUMNS: Array<{
  key: keyof QueryVariantRow
  label: string
  align?: "right" | "center"
  tooltip?: string
  format?: (v: unknown) => string
}> = [
  { key: "rsid", label: "rsID" },
  { key: "chrom", label: "Chr" },
  { key: "pos", label: "Position", align: "right", format: (v) => (v != null ? formatNumber(v as number) : "—") },
  { key: "genotype", label: "Genotype" },
  { key: "carriage_status", label: "Carriage / Zygosity" },
  { key: "gene_symbol", label: "Gene" },
  { key: "consequence", label: "Consequence" },
  { key: "clinvar_significance", label: "ClinVar" },
  { key: "clinvar_review_stars", label: "Stars", align: "center" },
  {
    key: "gnomad_af_global",
    label: "gnomAD AF",
    align: "right",
    format: (v) => formatAlleleFrequency(v as number | null),
  },
  {
    key: "cadd_phred",
    label: "CADD",
    align: "right",
    tooltip: CADD_TOOLTIP,
    format: (v) => (v != null ? String(v) : "—"),
  },
  {
    key: "revel",
    label: "REVEL",
    align: "right",
    tooltip: REVEL_TOOLTIP,
    format: (v) => (v != null ? String(v) : "—"),
  },
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
  includeAllPositions?: boolean
}

export default function QueryResultsTable({
  pages,
  totalMatching,
  hasMore,
  isFetchingMore,
  onLoadMore,
  onExport,
  isExporting,
  includeAllPositions = false,
}: QueryResultsTableProps) {
  const allItems = pages.flatMap((p) => p.items)

  return (
    <div data-testid="query-results-table">
      {/* Summary bar */}
      <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border border-border rounded-t-lg">
        <p className="text-sm font-medium">
          Showing {formatNumber(allItems.length)}
          {totalMatching != null && ` of ${formatNumber(totalMatching)}`} matching{" "}
          {includeAllPositions ? "annotated positions" : "carried variants"}
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
                    title={col.tooltip}
                    className={cn(
                      "px-3 py-2 font-medium text-xs text-muted-foreground whitespace-nowrap",
                      col.tooltip && SCORE_TOOLTIP_AFFORDANCE,
                      col.align === "right"
                        ? "text-right"
                        : col.align === "center"
                          ? "text-center"
                          : "text-left",
                    )}
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
                  className={cn(
                    "border-b border-border/50 hover:bg-muted/20 transition-colors",
                    row.carriage_status !== "carried" && "bg-muted/10 text-muted-foreground",
                  )}
                  data-testid="query-result-row"
                  data-carriage-status={row.carriage_status}
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
                        data-testid={`query-${col.key}-cell`}
                      >
                        {col.key === "carriage_status" ? (
                          <CarriageStatus row={row} />
                        ) : col.key === "clinvar_significance" && raw ? (
                          row.carriage_status === "carried" ? (
                            <ClinvarBadge value={String(raw)} />
                          ) : (
                            <MutedAnnotation value={String(raw)} status={row.carriage_status} />
                          )
                        ) : col.key === "clinvar_review_stars" && raw != null ? (
                          <ClinvarStars
                            value={raw as number}
                            status={row.carriage_status}
                          />
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

function CarriageStatus({ row }: { row: QueryVariantRow }) {
  if (row.carriage_status === "not_carried") {
    return (
      <span
        className="inline-flex rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground"
        title="The sample is homozygous for the reference allele at this annotated position."
      >
        Not carried · homozygous reference
      </span>
    )
  }

  if (row.carriage_status === "unresolved") {
    return (
      <span
        className="inline-flex rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground"
        title="The available genotype could not establish whether the annotated alternate allele is carried."
      >
        Unresolved
      </span>
    )
  }

  const label = row.zygosity === "het"
    ? "Carried · heterozygous"
    : row.zygosity === "hom_alt"
      ? "Carried · homozygous alternate"
      : "Carried"

  return (
    <span className="inline-flex rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-foreground">
      {label}
    </span>
  )
}

function nonCarriedContext(status: QueryVariantRow["carriage_status"]) {
  return status === "not_carried"
    ? "Variant-level annotation; the sample does not carry this alternate allele."
    : "Variant-level annotation; alternate-allele carriage is unresolved."
}

function MutedAnnotation({
  value,
  status,
}: {
  value: string
  status: QueryVariantRow["carriage_status"]
}) {
  const context = nonCarriedContext(status)
  return (
    <span className="text-muted-foreground" title={context} aria-label={`${value}. ${context}`}>
      {value}
    </span>
  )
}

function ClinvarStars({
  value,
  status,
}: {
  value: number
  status: QueryVariantRow["carriage_status"]
}) {
  const stars = Math.max(0, Math.min(4, Math.floor(value)))
  const context = status === "carried" ? "" : ` ${nonCarriedContext(status)}`
  return (
    <span
      role="img"
      aria-label={`${stars} ClinVar review stars.${context}`}
      className={status === "carried" ? undefined : "text-muted-foreground"}
      title={status === "carried" ? undefined : nonCarriedContext(status)}
    >
      {"★".repeat(stars)}{"☆".repeat(4 - stars)}
    </span>
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
