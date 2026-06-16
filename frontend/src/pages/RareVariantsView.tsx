/** Rare variant finder page (P3-30).
 *
 * Layout:
 * - Filter panel (gene panel upload, AF threshold, consequence/ClinVar filters)
 * - Search summary stats bar (counts + export buttons)
 * - Results table with sortable columns
 * - Variant detail slide-in panel
 *
 * PRD P3-30: Gene panel upload, filter controls, results table with export.
 */

import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Search, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { getRareVariantCategoryMeta } from "@/lib/rare-variant-category"
import { useRareVariantFindings, useRareVariantSearch } from "@/api/rare-variants"
import type { RareVariant, RareVariantSearchResponse } from "@/types/rare-variants"
import FilterPanel from "@/components/rare-variants/FilterPanel"
import ResultsTable from "@/components/rare-variants/ResultsTable"
import SearchSummary from "@/components/rare-variants/SearchSummary"
import VariantDetailPanel from "@/components/rare-variants/VariantDetailPanel"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"

export default function RareVariantsView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedVariant, setSelectedVariant] = useState<RareVariant | null>(null)
  const [searchResult, setSearchResult] = useState<RareVariantSearchResponse | null>(null)

  // Close detail panel on Escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && selectedVariant) {
        setSelectedVariant(null)
      }
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [selectedVariant])

  const findingsQuery = useRareVariantFindings(sampleId)
  const searchMutation = useRareVariantSearch(sampleId)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Rare Variants</h1>
        <PageEmpty icon={Search} title="Select a sample to search for rare variants." />
      </div>
    )
  }

  const hasSearchResult = searchResult != null
  const hasFindingsOnly = !hasSearchResult && findingsQuery.data && findingsQuery.data.items.length > 0
  const isLoading = findingsQuery.isLoading
  const hasError = findingsQuery.isError

  return (
    <div className="p-6">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <Search className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Rare Variant Finder</h1>
          <p className="text-sm text-muted-foreground">
            Search for rare and novel variants with custom gene panels and filters
          </p>
        </div>
      </div>

      {/* Filter panel */}
      <section aria-label="Search filters" className="mb-6">
        <FilterPanel
          onSearch={(filters) => {
            searchMutation.mutate(filters, {
              onSuccess: (data) => {
                setSearchResult(data)
                setSelectedVariant(null)
              },
            })
          }}
          isSearching={searchMutation.isPending}
        />
      </section>

      {/* Search error */}
      {searchMutation.isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 mb-6">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
            <div>
              <p className="font-medium text-destructive">Search failed</p>
              <p className="text-sm text-muted-foreground mt-1">
                {searchMutation.error instanceof Error
                  ? searchMutation.error.message
                  : "An unexpected error occurred."}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Loading state (initial findings load) */}
      {isLoading && !hasSearchResult && (
        <PageLoading message="Loading rare variant data..." />
      )}

      {/* Error state (findings load) */}
      {hasError && !isLoading && !hasSearchResult && (
        <PageError
          message={findingsQuery.error instanceof Error ? findingsQuery.error.message : "An unexpected error occurred."}
          onRetry={() => { findingsQuery.refetch(); }}
        />
      )}

      {/* Search results */}
      {hasSearchResult && (
        <>
          {/* Summary stats */}
          <section aria-label="Search results summary" className="mb-4">
            <SearchSummary
              total={searchResult.total}
              totalScanned={searchResult.total_variants_scanned}
              novelCount={searchResult.novel_count}
              pathogenicCount={searchResult.pathogenic_count}
              genesWithFindings={searchResult.genes_with_findings}
              sampleId={sampleId}
            />
          </section>

          {/* Results table */}
          <section aria-label="Search results">
            <ResultsTable
              items={searchResult.items}
              selectedRsid={selectedVariant?.rsid ?? null}
              onSelect={(v) =>
                setSelectedVariant(
                  selectedVariant?.rsid === v.rsid ? null : v,
                )
              }
            />
          </section>
        </>
      )}

      {/* Stored findings (no active search) */}
      {hasFindingsOnly && (
        <section aria-label="Stored rare variant findings">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Previous Findings</h2>
            <p className="text-xs text-muted-foreground">
              {findingsQuery.data!.total} findings from last analysis run
            </p>
          </div>
          <div className="rounded-lg border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="findings-table">
                <thead>
                  <tr className="border-b bg-muted/50">
                    <th className="text-left px-3 py-2 font-medium">Gene</th>
                    <th className="text-left px-3 py-2 font-medium">rsID</th>
                    <th className="text-left px-3 py-2 font-medium">Category</th>
                    <th className="text-left px-3 py-2 font-medium">ClinVar</th>
                    <th className="text-left px-3 py-2 font-medium">Zygosity</th>
                    <th className="text-center px-3 py-2 font-medium">Evidence</th>
                    <th className="text-left px-3 py-2 font-medium">Finding</th>
                  </tr>
                </thead>
                <tbody>
                  {findingsQuery.data!.items.map((f, i) => (
                    <tr key={`${f.rsid}-${i}`} className="border-b" data-testid="finding-row">
                      <td className="px-3 py-2 font-medium">{f.gene_symbol ?? "—"}</td>
                      <td className="px-3 py-2 font-mono text-xs">{f.rsid ?? "—"}</td>
                      <td className="px-3 py-2">
                        <span className={cn(
                          "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                          getRareVariantCategoryMeta(f.category).className,
                        )}>
                          {getRareVariantCategoryMeta(f.category).label}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs">{f.clinvar_significance ?? "—"}</td>
                      <td className="px-3 py-2 text-xs">
                        {f.zygosity === "hom_alt" ? "Hom" : f.zygosity === "het" ? "Het" : "—"}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <EvidenceStarsInline level={f.evidence_level} />
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground line-clamp-2 max-w-[300px]">
                        {f.finding_text}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Export buttons for stored findings */}
          <div className="flex gap-2 mt-3 justify-end" data-testid="findings-export">
            <a
              href={`/api/analysis/rare-variants/export/tsv?sample_id=${sampleId}`}
              download
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors"
            >
              Export TSV
            </a>
            <a
              href={`/api/analysis/rare-variants/export/vcf?sample_id=${sampleId}`}
              download
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors"
            >
              Export VCF
            </a>
          </div>
        </section>
      )}

      {/* Empty state: no findings and no search */}
      {!isLoading && !hasError && !hasSearchResult && !hasFindingsOnly && (
        <PageEmpty
          icon={Search}
          title="No rare variant findings yet."
          description="Use the filters above to search for rare variants, or run the annotation pipeline to generate automatic findings."
        />
      )}

      {/* Variant detail slide-in panel */}
      {selectedVariant && (
        <>
          <div
            className="fixed inset-0 z-30 bg-black/20"
            onClick={() => setSelectedVariant(null)}
            aria-hidden="true"
          />
          <VariantDetailPanel
            variant={selectedVariant}
            onClose={() => setSelectedVariant(null)}
          />
        </>
      )}
    </div>
  )
}

/** Inline evidence stars for findings table. */
function EvidenceStarsInline({ level }: { level: number }) {
  const stars = Math.max(0, Math.min(4, level))
  return (
    <span
      className="text-xs text-muted-foreground"
      role="img"
      aria-label={`${stars} of 4 stars evidence`}
    >
      {"★".repeat(stars)}{"☆".repeat(4 - stars)}
    </span>
  )
}
