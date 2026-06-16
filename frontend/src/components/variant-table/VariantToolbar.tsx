/** Variant table toolbar: search + unannotated toggle + conflicts-only toggle + tag filter + preset selector + filter badge (P1-15a, P1-15c, P1-15e, P2-22, P4-12b).
 *  Source / Concordance filter chips for merged samples (AncestryDNA Plan §10.7 / Step 71). */

import { useEffect, useRef, useState } from "react"
import {
  Search,
  Eye,
  EyeOff,
  AlertTriangle,
  X,
  Tag,
  ArrowRightLeft,
  GitMerge,
  ScanSearch,
} from "lucide-react"
import ColumnPresets from "./ColumnPresets"
import { filterLabel } from "./filterSuggestions"
import { useTags } from "@/api/tags"
import {
  CONCORDANCE_LABELS,
  CONCORDANCE_OPTIONS,
  SOURCE_LABELS,
  SOURCE_OPTIONS,
  type ConcordanceTag,
  type SourceTag,
} from "@/types/variants"

interface VariantToolbarProps {
  searchQuery: string
  onSearchChange: (query: string) => void
  showUnannotated: boolean
  onToggleUnannotated: () => void
  showConflictsOnly: boolean
  onToggleConflictsOnly: () => void
  unannotatedCount: number | undefined
  totalCount: number | undefined
  totalCountLoading: boolean
  isLoading: boolean
  activePreset: string | null
  onPresetChange: (presetName: string | null, columns: string[] | null) => void
  activeFilter?: string
  onClearFilter?: () => void
  sampleId: number | null
  activeTag?: string | null
  onTagFilter?: (tagName: string | null) => void
  showGRCh38: boolean
  onToggleGRCh38: () => void
  /** AncestryDNA Plan §10.7 / Step 71: merged-sample chips render only when
   *  the sample's ``merge-provenance`` row resolves successfully. */
  isMergedSample: boolean
  sourceFilter: SourceTag | null
  onSourceFilter: (value: SourceTag | null) => void
  concordanceFilter: ConcordanceTag | null
  onConcordanceFilter: (value: ConcordanceTag | null) => void
}

export default function VariantToolbar({
  searchQuery,
  onSearchChange,
  showUnannotated,
  onToggleUnannotated,
  showConflictsOnly,
  onToggleConflictsOnly,
  unannotatedCount,
  totalCount,
  totalCountLoading,
  isLoading,
  activePreset,
  onPresetChange,
  activeFilter,
  onClearFilter,
  sampleId,
  activeTag,
  onTagFilter,
  showGRCh38,
  onToggleGRCh38,
  isMergedSample,
  sourceFilter,
  onSourceFilter,
  concordanceFilter,
  onConcordanceFilter,
}: VariantToolbarProps) {
  const [tagDropdownOpen, setTagDropdownOpen] = useState(false)
  const [sourceDropdownOpen, setSourceDropdownOpen] = useState(false)
  const [concordanceDropdownOpen, setConcordanceDropdownOpen] = useState(false)
  const tagDropdownRef = useRef<HTMLDivElement>(null)
  const sourceDropdownRef = useRef<HTMLDivElement>(null)
  const concordanceDropdownRef = useRef<HTMLDivElement>(null)
  const { data: tags } = useTags(sampleId)

  // Close dropdown on outside click
  useEffect(() => {
    if (!tagDropdownOpen) return
    function handleClick(e: MouseEvent) {
      if (tagDropdownRef.current && !tagDropdownRef.current.contains(e.target as Node)) {
        setTagDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [tagDropdownOpen])

  useEffect(() => {
    if (!sourceDropdownOpen) return
    function handleClick(e: MouseEvent) {
      if (
        sourceDropdownRef.current &&
        !sourceDropdownRef.current.contains(e.target as Node)
      ) {
        setSourceDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [sourceDropdownOpen])

  useEffect(() => {
    if (!concordanceDropdownOpen) return
    function handleClick(e: MouseEvent) {
      if (
        concordanceDropdownRef.current &&
        !concordanceDropdownRef.current.contains(e.target as Node)
      ) {
        setConcordanceDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [concordanceDropdownOpen])

  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-border bg-card">
      {/* Search input */}
      <div className="relative flex-1 max-w-sm">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search rsid or gene..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          className="w-full pl-9 pr-3 py-1.5 text-sm rounded-md border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          aria-label="Search variants by rsid or gene"
        />
      </div>

      {/* Unannotated toggle */}
      <button
        type="button"
        onClick={onToggleUnannotated}
        className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
          showUnannotated
            ? "border-primary bg-primary/10 text-primary"
            : "border-input bg-background text-muted-foreground hover:text-foreground"
        }`}
        aria-pressed={showUnannotated}
        aria-label={showUnannotated ? "Hide unannotated variants" : "Show unannotated variants"}
      >
        {showUnannotated ? (
          <Eye className="h-4 w-4" />
        ) : (
          <EyeOff className="h-4 w-4" />
        )}
        <span>
          {showUnannotated ? "Showing" : "Show"} unannotated
          {unannotatedCount != null && ` (${unannotatedCount.toLocaleString()})`}
        </span>
      </button>

      {/* Conflicts only toggle (P2-22) */}
      <button
        type="button"
        onClick={onToggleConflictsOnly}
        className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
          showConflictsOnly
            ? "border-amber-500 bg-amber-500/10 text-amber-700 dark:text-amber-400"
            : "border-input bg-background text-muted-foreground hover:text-foreground"
        }`}
        aria-pressed={showConflictsOnly}
        aria-label={showConflictsOnly ? "Show all variants" : "Show conflicts only"}
      >
        <AlertTriangle className="h-4 w-4" />
        <span>Conflicts only</span>
      </button>

      {/* Tag filter dropdown (P4-12b) */}
      <div className="relative" ref={tagDropdownRef}>
        <button
          type="button"
          onClick={() => setTagDropdownOpen((prev) => !prev)}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
            activeTag
              ? "border-teal-500 bg-teal-500/10 text-teal-600 dark:text-teal-400"
              : "border-input bg-background text-muted-foreground hover:text-foreground"
          }`}
          aria-expanded={tagDropdownOpen}
          aria-haspopup="listbox"
          aria-label={activeTag ? `Tag filter: ${activeTag}` : "Filter by tag"}
        >
          <Tag className="h-4 w-4" />
          <span>{activeTag ?? "Tags"}</span>
          {activeTag && onTagFilter && (
            <X
              className="h-3 w-3 ml-0.5"
              onClick={(e) => {
                e.stopPropagation()
                onTagFilter(null)
              }}
            />
          )}
        </button>

        {tagDropdownOpen && (
          <div
            className="absolute top-full left-0 mt-1 z-50 min-w-[180px] rounded-md border border-border bg-popover shadow-md py-1"
            role="listbox"
            aria-label="Available tags"
          >
            {!tags || tags.length === 0 ? (
              <div className="px-3 py-2 text-sm text-muted-foreground">No tags available</div>
            ) : (
              tags.map((tag) => (
                <button
                  key={tag.id}
                  type="button"
                  role="option"
                  aria-selected={activeTag === tag.name}
                  className={`w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-accent transition-colors ${
                    activeTag === tag.name ? "bg-accent" : ""
                  }`}
                  onClick={() => {
                    onTagFilter?.(activeTag === tag.name ? null : tag.name)
                    setTagDropdownOpen(false)
                  }}
                >
                  <span
                    className="inline-block h-3 w-3 rounded-full shrink-0"
                    style={{ backgroundColor: tag.color }}
                  />
                  <span className="truncate">{tag.name}</span>
                  {tag.variant_count != null && (
                    <span className="ml-auto text-xs text-muted-foreground">
                      {tag.variant_count}
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
        )}
      </div>

      {/* Active tag badge */}
      {activeTag && onTagFilter && (
        <button
          type="button"
          onClick={() => onTagFilter(null)}
          className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md border border-teal-500/30 bg-teal-500/10 text-teal-600 dark:text-teal-400 hover:bg-teal-500/20 transition-colors"
          aria-label={`Clear tag filter: ${activeTag}`}
        >
          Tag: {activeTag}
          <X className="h-3 w-3" />
        </button>
      )}

      {/* Active filter badge (P1-15e) */}
      {activeFilter && onClearFilter && (
        <button
          type="button"
          onClick={onClearFilter}
          className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
          aria-label={`Clear filter: ${filterLabel(activeFilter)}`}
        >
          {filterLabel(activeFilter)}
          <X className="h-3 w-3" />
        </button>
      )}

      {/* Merged-sample source / concordance filter chips (AncestryDNA Plan §10.7 / Step 71).
          Rendered only when ``useMergeProvenance`` resolves successfully; unmerged
          samples never see these chips, matching the per-sample column visibility. */}
      {isMergedSample && (
        <>
          <div className="relative" ref={sourceDropdownRef}>
            <button
              type="button"
              onClick={() => setSourceDropdownOpen((prev) => !prev)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
                sourceFilter
                  ? "border-sky-500 bg-sky-500/10 text-sky-600 dark:text-sky-400"
                  : "border-input bg-background text-muted-foreground hover:text-foreground"
              }`}
              aria-expanded={sourceDropdownOpen}
              aria-haspopup="listbox"
              aria-label={
                sourceFilter
                  ? `Source filter: ${SOURCE_LABELS[sourceFilter]}`
                  : "Filter by source"
              }
            >
              <GitMerge className="h-4 w-4" />
              <span>{sourceFilter ? SOURCE_LABELS[sourceFilter] : "Source"}</span>
              {sourceFilter && (
                <X
                  className="h-3 w-3 ml-0.5"
                  onClick={(e) => {
                    e.stopPropagation()
                    onSourceFilter(null)
                  }}
                />
              )}
            </button>
            {sourceDropdownOpen && (
              <div
                className="absolute top-full left-0 mt-1 z-50 min-w-[140px] rounded-md border border-border bg-popover shadow-md py-1"
                role="listbox"
                aria-label="Source values"
              >
                {SOURCE_OPTIONS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="option"
                    aria-selected={sourceFilter === value}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-accent transition-colors ${
                      sourceFilter === value ? "bg-accent" : ""
                    }`}
                    onClick={() => {
                      onSourceFilter(sourceFilter === value ? null : value)
                      setSourceDropdownOpen(false)
                    }}
                  >
                    {SOURCE_LABELS[value]}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="relative" ref={concordanceDropdownRef}>
            <button
              type="button"
              onClick={() => setConcordanceDropdownOpen((prev) => !prev)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
                concordanceFilter
                  ? "border-sky-500 bg-sky-500/10 text-sky-600 dark:text-sky-400"
                  : "border-input bg-background text-muted-foreground hover:text-foreground"
              }`}
              aria-expanded={concordanceDropdownOpen}
              aria-haspopup="listbox"
              aria-label={
                concordanceFilter
                  ? `Concordance filter: ${CONCORDANCE_LABELS[concordanceFilter]}`
                  : "Filter by concordance"
              }
            >
              <ScanSearch className="h-4 w-4" />
              <span>
                {concordanceFilter
                  ? CONCORDANCE_LABELS[concordanceFilter]
                  : "Concordance"}
              </span>
              {concordanceFilter && (
                <X
                  className="h-3 w-3 ml-0.5"
                  onClick={(e) => {
                    e.stopPropagation()
                    onConcordanceFilter(null)
                  }}
                />
              )}
            </button>
            {concordanceDropdownOpen && (
              <div
                className="absolute top-full left-0 mt-1 z-50 min-w-[180px] rounded-md border border-border bg-popover shadow-md py-1"
                role="listbox"
                aria-label="Concordance values"
              >
                {CONCORDANCE_OPTIONS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="option"
                    aria-selected={concordanceFilter === value}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-accent transition-colors ${
                      concordanceFilter === value ? "bg-accent" : ""
                    }`}
                    onClick={() => {
                      onConcordanceFilter(
                        concordanceFilter === value ? null : value,
                      )
                      setConcordanceDropdownOpen(false)
                    }}
                  >
                    {CONCORDANCE_LABELS[value]}
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* GRCh38 liftover toggle (P4-20) */}
      <button
        type="button"
        onClick={onToggleGRCh38}
        className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
          showGRCh38
            ? "border-indigo-500 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400"
            : "border-input bg-background text-muted-foreground hover:text-foreground"
        }`}
        aria-pressed={showGRCh38}
        aria-label={showGRCh38 ? "Hide GRCh38 coordinates" : "Show GRCh38 coordinates"}
      >
        <ArrowRightLeft className="h-4 w-4" />
        <span>GRCh38</span>
      </button>

      {/* Column preset selector (P1-15c) */}
      <ColumnPresets activePreset={activePreset} onPresetChange={onPresetChange} />

      {/* Total count (async) */}
      <div className="ml-auto text-sm text-muted-foreground" aria-live="polite">
        {isLoading ? (
          "Loading..."
        ) : totalCountLoading ? (
          "Loading count\u2026"
        ) : totalCount != null ? (
          `${totalCount.toLocaleString()} variants`
        ) : null}
      </div>
    </div>
  )
}
