/** Variant table core: TanStack Table + infinite scroll + useInfiniteQuery (P1-15a).
 *  Chromosome anchors: jump-to-chromosome navigation bar (P1-15b).
 *  Column preset profiles (P1-15c).
 *  Contextual empty states (P1-15e).
 *  Variant detail side panel on row click (P2-21).
 *  Annotation columns per presets + evidence conflict indicator + "Conflicts only" toggle (P2-22). */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type VisibilityState,
} from "@tanstack/react-table"
import { Loader2 } from "lucide-react"

import { useVariants, useVariantsCount, useTotalVariantCount, useChromosomeCounts } from "@/api/variants"
import { useColumnPresets } from "@/api/columnPresets"
import { useMergeProvenance } from "@/api/samples"
import { useTags } from "@/api/tags"
import { SCORE_TOOLTIP_AFFORDANCE } from "@/lib/inSilicoScoreInfo"
import { cn } from "@/lib/utils"
import type { ConcordanceTag, SourceTag, VariantRow } from "@/types/variants"
import { allColumns } from "./columns"
import VariantToolbar from "./VariantToolbar"
import ChromosomeNav from "./ChromosomeNav"
import { ALWAYS_VISIBLE, COLUMN_TOOLTIPS } from "./ColumnPresets"
import {
  PreUploadEmpty,
  PreAnnotationEmpty,
  NoMatchEmpty,
  ErrorEmpty,
} from "./EmptyStates"
import VariantDetailSidePanel from "@/components/variant-detail/VariantDetailSidePanel"
import WatchingSidebar from "./WatchingSidebar"

interface VariantTableProps {
  sampleId: number | null
}

/** GRCh38 liftover column IDs (P4-20). */
const GRCH38_COLUMNS = ["chrom_grch38", "pos_grch38"]

/** Merged-sample provenance column IDs (AncestryDNA Plan §10.7 / Step 71).
 *  Hidden on unmerged samples regardless of preset; shown by default when
 *  ``useMergeProvenance`` resolves to a row (i.e. sample is merged). */
const MERGE_PROV_COLUMNS = ["source", "concordance"]

/** Convert a preset's column list to TanStack Table VisibilityState.
 *  GRCh38 columns are controlled separately via the liftover toggle (P4-20).
 *  Source / Concordance columns are controlled by merged-sample status
 *  (AncestryDNA Plan §10.7 / Step 71). */
function presetToVisibility(
  presetColumns: string[] | null,
  allColumnIds: string[],
  showGRCh38: boolean,
  isMergedSample: boolean,
): VisibilityState {
  if (!presetColumns) {
    // All visible — but still respect GRCh38 + merge-provenance toggles
    const visibility: VisibilityState = {}
    for (const colId of allColumnIds) {
      if (ALWAYS_VISIBLE.has(colId)) continue
      if (GRCH38_COLUMNS.includes(colId)) {
        visibility[colId] = showGRCh38
      } else if (MERGE_PROV_COLUMNS.includes(colId)) {
        visibility[colId] = isMergedSample
      } else {
        visibility[colId] = true
      }
    }
    return visibility
  }
  const visibility: VisibilityState = {}
  for (const colId of allColumnIds) {
    if (ALWAYS_VISIBLE.has(colId)) continue
    if (GRCH38_COLUMNS.includes(colId)) {
      visibility[colId] = showGRCh38
    } else if (MERGE_PROV_COLUMNS.includes(colId)) {
      // Source / Concordance visibility tracks merged-sample status, not
      // the active preset — presets predate Phase 3 and would silently
      // hide them otherwise.
      visibility[colId] = isMergedSample
    } else {
      visibility[colId] = presetColumns.includes(colId)
    }
  }
  return visibility
}

export default function VariantTable({ sampleId }: VariantTableProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const [showUnannotated, setShowUnannotated] = useState(false)
  const [showConflictsOnly, setShowConflictsOnly] = useState(false)
  const [showGRCh38, setShowGRCh38] = useState(false)
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    chrom_grch38: false,
    pos_grch38: false,
    source: false,
    concordance: false,
  })
  const [startChrom, setStartChrom] = useState<string | null>(null)
  const [activeFilter, setActiveFilter] = useState<string | undefined>(undefined)
  const [activeTag, setActiveTag] = useState<string | null>(null)
  // AncestryDNA Plan §10.7 / Step 71: per-row merge-provenance filter chips.
  const [sourceFilter, setSourceFilter] = useState<SourceTag | null>(null)
  const [concordanceFilter, setConcordanceFilter] = useState<ConcordanceTag | null>(null)

  // Detect merged-sample status. The hook returns 404 for unmerged samples,
  // which we treat as "not merged" without surfacing the error to the user.
  const { data: provenance, error: provenanceError } = useMergeProvenance(sampleId)
  const isMergedSample = !!provenance && !provenanceError

  // Variant detail side panel state (P2-21)
  const [selectedRsid, setSelectedRsid] = useState<string | null>(null)

  // Column preset state from URL param (P1-15c)
  const [activePreset, setActivePreset] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get("profile") || null
  })

  // Fetch presets to resolve initial URL param
  const { data: presets } = useColumnPresets()
  const { data: tags } = useTags(sampleId)
  const tagColors = useMemo(
    () => new Map(tags?.map((tag) => [tag.name, tag.color]) ?? []),
    [tags],
  )
  // TanStack Table accessor columns store their ID in `accessorKey` rather than `id`.
  // The union type doesn't expose accessorKey directly, so we cast through `any`.
  const allColumnIds = useMemo(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    () => allColumns.map((c) => c.id ?? ((c as any).accessorKey as string)).filter(Boolean),
    [],
  )

  // Apply preset from URL param on initial load
  const initialPresetApplied = useRef(false)
  useEffect(() => {
    if (initialPresetApplied.current || !presets || !activePreset) return
    const match = presets.find((p) => p.name.toLowerCase() === activePreset.toLowerCase())
    if (match) {
      setActivePreset(match.name)
      setColumnVisibility(
        presetToVisibility(match.columns, allColumnIds, showGRCh38, isMergedSample),
      )
    } else {
      // Invalid preset in URL — reset
      setActivePreset(null)
    }
    initialPresetApplied.current = true
  }, [presets, activePreset, allColumnIds, showGRCh38, isMergedSample])

  // Reveal Source / Concordance columns once merge-provenance resolves
  // (AncestryDNA Plan §10.7 / Step 71). Skipped while a preset is active
  // since the preset path already calls presetToVisibility with the
  // current isMergedSample value.
  useEffect(() => {
    setColumnVisibility((prev) => {
      if (prev.source === isMergedSample && prev.concordance === isMergedSample) {
        return prev
      }
      return { ...prev, source: isMergedSample, concordance: isMergedSample }
    })
  }, [isMergedSample])

  // Clear merge-provenance filter chips when the active sample is not merged
  // (AncestryDNA Plan §10.7 / Step 71). The chips are hidden for unmerged
  // samples, so a stale source/concordance value would silently over-filter
  // the server query with no way to clear it from the UI.
  useEffect(() => {
    if (!isMergedSample) {
      setSourceFilter(null)
      setConcordanceFilter(null)
    }
  }, [isMergedSample, sampleId])

  const handlePresetChange = useCallback(
    (presetName: string | null, columns: string[] | null) => {
      setActivePreset(presetName)
      setColumnVisibility(
        presetToVisibility(columns, allColumnIds, showGRCh38, isMergedSample),
      )

      // Update URL param
      const url = new URL(window.location.href)
      if (presetName) {
        url.searchParams.set("profile", presetName.toLowerCase())
      } else {
        url.searchParams.delete("profile")
      }
      window.history.replaceState({}, "", url.toString())
    },
    [allColumnIds, showGRCh38, isMergedSample],
  )

  // GRCh38 liftover toggle (P4-20): show/hide GRCh38 columns independently of presets
  const handleToggleGRCh38 = useCallback(() => {
    setShowGRCh38((prev) => {
      const next = !prev
      setColumnVisibility((vis) => ({
        ...vis,
        chrom_grch38: next,
        pos_grch38: next,
      }))
      return next
    })
  }, [])

  // Server-side filter string (set by quick-apply suggestions in P1-15e, P2-22
  // conflicts toggle, Step 71 merged-sample source/concordance chips).
  const filter = useMemo(() => {
    const parts: string[] = []
    if (activeFilter) parts.push(activeFilter)
    if (showConflictsOnly) parts.push("evidence_conflict:1")
    if (sourceFilter) parts.push(`source:${sourceFilter}`)
    if (concordanceFilter) parts.push(`concordance:${concordanceFilter}`)
    return parts.length > 0 ? parts.join(",") : undefined
  }, [activeFilter, showConflictsOnly, sourceFilter, concordanceFilter])

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetching,
    isFetchingNextPage,
    status,
    error,
  } = useVariants({ sampleId, filter, showUnannotated, startChrom, tag: activeTag })

  // Chromosome counts for the nav bar (P1-15b)
  const { data: chromCounts, isLoading: chromCountsLoading } =
    useChromosomeCounts(sampleId)

  const { data: countData, isLoading: countLoading } = useVariantsCount({
    sampleId,
    filter,
    showUnannotated,
    tag: activeTag,
  })

  const { data: totalVariants } = useTotalVariantCount(sampleId)

  // Derive current chromosome from the first visible row (P1-15b)
  const activeChrom = useMemo(() => {
    if (!data?.pages?.length) return null
    const firstPage = data.pages[0]
    if (!firstPage?.items?.length) return null
    return firstPage.items[0].chrom
  }, [data?.pages])

  // Jump to a chromosome: reset infinite query by changing startChrom
  const tableContainerRef = useRef<HTMLElement>(null)

  const handleJumpToChrom = useCallback(
    (chrom: string) => {
      setStartChrom(chrom)
      tableContainerRef.current?.scrollTo({ top: 0, behavior: "instant" })
    },
    [],
  )

  // Flatten pages into a single array
  const allRows = useMemo(() => {
    if (!data?.pages) return []
    const rows = data.pages.flatMap((page) => page.items)

    // Client-side filtering: hide unannotated by default
    const filtered = showUnannotated
      ? rows
      : rows.filter((row) => row.annotation_coverage != null)

    // Client-side search filtering (rsid / gene_symbol)
    if (!searchQuery.trim()) return filtered
    const q = searchQuery.trim().toLowerCase()
    return filtered.filter(
      (row) =>
        row.rsid.toLowerCase().includes(q) ||
        (row.gene_symbol && row.gene_symbol.toLowerCase().includes(q)),
    )
  }, [data?.pages, showUnannotated, searchQuery])

  const table = useReactTable<VariantRow>({
    data: allRows,
    columns: allColumns,
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    meta: { tagColors },
    initialState: {
      columnPinning: { left: ["evidence_conflict"] },
    },
  })

  // Infinite scroll: observe sentinel element at bottom of table
  const sentinelRef = useRef<HTMLDivElement>(null)

  const handleIntersect = useCallback(
    (entries: IntersectionObserverEntry[]) => {
      const entry = entries[0]
      if (entry?.isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage()
      }
    },
    [hasNextPage, isFetchingNextPage, fetchNextPage],
  )

  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel) return

    const observer = new IntersectionObserver(handleIntersect, {
      rootMargin: "200px",
    })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [handleIntersect])

  // Detect pre-annotation state (P1-15e): sample has raw variants but none annotated.
  // When showUnannotated is off (default), annotated count = 0 but total > 0.
  const isPreAnnotation = useMemo(() => {
    if (showUnannotated || searchQuery || activeFilter || showConflictsOnly) return false
    // Merge-provenance filter chips also narrow the server query — a
    // filtered-empty result must not surface the pre-annotation message.
    if (sourceFilter || concordanceFilter) return false
    if (totalVariants == null || totalVariants === 0) return false
    // countData is filtered by annotation_coverage:notnull — if 0, no annotated variants
    if (countData && countData.total === 0) return true
    return false
  }, [showUnannotated, searchQuery, activeFilter, showConflictsOnly, sourceFilter, concordanceFilter, totalVariants, countData])

  // Empty states (P1-15e)
  if (sampleId == null) {
    return <PreUploadEmpty />
  }

  if (status === "error") {
    return <ErrorEmpty message={error?.message ?? "An unexpected error occurred."} />
  }

  return (
    <div className="flex flex-col h-full">
      <ChromosomeNav
        chromosomeCounts={chromCounts}
        isLoading={chromCountsLoading}
        activeChrom={activeChrom}
        onJumpToChrom={handleJumpToChrom}
      />

      <VariantToolbar
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        showUnannotated={showUnannotated}
        onToggleUnannotated={() => setShowUnannotated((prev) => !prev)}
        showConflictsOnly={showConflictsOnly}
        onToggleConflictsOnly={() => setShowConflictsOnly((prev) => !prev)}
        unannotatedCount={totalVariants}
        totalCount={countData?.total}
        totalCountLoading={countLoading}
        isLoading={status === "pending"}
        activePreset={activePreset}
        onPresetChange={handlePresetChange}
        activeFilter={activeFilter}
        onClearFilter={() => setActiveFilter(undefined)}
        tags={tags}
        activeTag={activeTag}
        onTagFilter={setActiveTag}
        showGRCh38={showGRCh38}
        onToggleGRCh38={handleToggleGRCh38}
        isMergedSample={isMergedSample}
        sourceFilter={sourceFilter}
        onSourceFilter={setSourceFilter}
        concordanceFilter={concordanceFilter}
        onConcordanceFilter={setConcordanceFilter}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar: Watching section (P4-21k) */}
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard-accessible (axe: scrollable-region-focusable) */}
        <aside className="w-64 shrink-0 border-r border-border overflow-y-auto bg-card" aria-label="Variant table sidebar" tabIndex={0}>
          <WatchingSidebar
            sampleId={sampleId}
            onSelectVariant={setSelectedRsid}
            selectedRsid={selectedRsid}
          />
        </aside>

        {/* Main table area */}
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard-accessible (axe: scrollable-region-focusable) */}
        <section ref={tableContainerRef} className="flex-1 overflow-auto" aria-label="Variant table" tabIndex={0}>
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 z-10 bg-card border-b border-border">
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map((header) => {
                    const tooltip = COLUMN_TOOLTIPS[header.column.id]
                    return (
                      <th
                        key={header.id}
                        title={tooltip}
                        className={cn(
                          "px-3 py-2 text-left font-medium text-muted-foreground whitespace-nowrap",
                          tooltip && SCORE_TOOLTIP_AFFORDANCE,
                        )}
                        style={{ width: header.getSize() }}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    )
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {status === "pending" ? (
                <tr>
                  <td colSpan={table.getAllColumns().length} className="text-center py-12">
                    {/* role="status" (implies aria-live=polite) so SR users are
                        told variants are loading; the spinner is decorative. (#601) */}
                    <div role="status">
                      <Loader2
                        className="h-6 w-6 animate-spin mx-auto text-primary"
                        aria-hidden="true"
                      />
                      <p className="text-sm text-muted-foreground mt-2">Loading variants...</p>
                    </div>
                  </td>
                </tr>
              ) : allRows.length === 0 ? (
                <tr>
                  <td colSpan={table.getAllColumns().length}>
                    {isPreAnnotation ? (
                      <PreAnnotationEmpty
                        totalVariants={totalVariants ?? 0}
                        onShowUnannotated={() => setShowUnannotated(true)}
                      />
                    ) : (
                      <NoMatchEmpty
                        searchQuery={searchQuery}
                        hasActiveFilter={!!activeFilter}
                        onClearSearch={() => setSearchQuery("")}
                        onClearFilters={() => setActiveFilter(undefined)}
                        onApplyFilter={(f) => {
                          setActiveFilter(f)
                          setShowUnannotated(true)
                        }}
                      />
                    )}
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={`border-b border-border/50 hover:bg-accent/50 transition-colors cursor-pointer ${
                      row.original.rsid === selectedRsid ? "bg-accent" : ""
                    }`}
                    onClick={() => setSelectedRsid(row.original.rsid)}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        className="px-3 py-1.5 whitespace-nowrap"
                        style={{ width: cell.column.getSize() }}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>

          {/* Infinite scroll sentinel */}
          <div ref={sentinelRef} className="h-10 flex items-center justify-center">
            {isFetchingNextPage && (
              <div
                role="status"
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading more...
              </div>
            )}
            {!hasNextPage && allRows.length > 0 && !isFetching && (
              <p className="text-xs text-muted-foreground">All variants loaded</p>
            )}
          </div>
        </section>
      </div>

      {/* Variant detail side panel (P2-21) */}
      <VariantDetailSidePanel
        rsid={selectedRsid}
        sampleId={sampleId}
        onClose={() => setSelectedRsid(null)}
      />
    </div>
  )
}
