/** Search result summary stats bar (P3-30).
 *
 * Shows total found, variants scanned, novel count, pathogenic count,
 * genes with findings.
 */

import { formatNumber } from "@/lib/format"

interface SearchSummaryProps {
  total: number
  totalScanned: number
  novelCount: number
  pathogenicCount: number
  genesWithFindings: string[]
}

export default function SearchSummary({
  total,
  totalScanned,
  novelCount,
  pathogenicCount,
  genesWithFindings,
}: SearchSummaryProps) {
  return (
    <div
      className="rounded-lg border bg-card p-4"
      data-testid="search-summary"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        {/* Stats */}
        <div className="flex flex-wrap gap-6">
          <div>
            <p className="text-2xl font-bold" data-testid="total-found">{formatNumber(total)}</p>
            <p className="text-xs text-muted-foreground">Variants found</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{formatNumber(totalScanned)}</p>
            <p className="text-xs text-muted-foreground">Total scanned</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-red-600 dark:text-red-400">{formatNumber(pathogenicCount)}</p>
            <p className="text-xs text-muted-foreground">Pathogenic</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-blue-600 dark:text-blue-400">{formatNumber(novelCount)}</p>
            <p className="text-xs text-muted-foreground">Novel</p>
          </div>
          {genesWithFindings.length > 0 && (
            <div>
              <p className="text-2xl font-bold">{genesWithFindings.length}</p>
              <p className="text-xs text-muted-foreground">Genes affected</p>
            </div>
          )}
        </div>
      </div>

      {/* Genes list */}
      {genesWithFindings.length > 0 && (
        <div className="mt-3 pt-3 border-t">
          <p className="text-xs text-muted-foreground mb-1.5">Genes with findings</p>
          <div className="flex flex-wrap gap-1">
            {genesWithFindings.map((gene) => (
              <span
                key={gene}
                className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-mono"
              >
                {gene}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
