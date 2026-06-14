/** Contextual empty states for the variant table (P1-15e).
 *
 *  Three distinct states:
 *  1. Pre-upload: no sample selected → "Upload a file to get started"
 *  2. Pre-annotation: sample exists with raw variants but none annotated →
 *     "Run annotation to see results here"
 *  3. No filter match: annotated variants exist but current filters exclude
 *     all → "No variants match your filters" + clear + quick-apply suggestions
 */

import { Upload, FlaskConical, SearchX, ShieldAlert } from "lucide-react"
import { FILTER_SUGGESTIONS } from "./filterSuggestions"

// ── Shared wrapper ──────────────────────────────────────────────────

function EmptyWrapper({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode
  title: string
  description: string
  children?: React.ReactNode
}) {
  return (
    <div
      className="flex flex-col items-center justify-center py-20 text-center"
      role="status"
      aria-label={title}
    >
      <div className="mb-4 text-muted-foreground">{icon}</div>
      <p className="text-lg font-medium text-muted-foreground">{title}</p>
      <p className="text-sm text-muted-foreground mt-1 max-w-md">{description}</p>
      {children && <div className="mt-4">{children}</div>}
    </div>
  )
}

// ── Suggestion button ───────────────────────────────────────────────

function SuggestionButton({
  label,
  onClick,
}: {
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="px-3 py-1.5 text-xs rounded-md border border-input bg-background hover:bg-accent text-foreground transition-colors"
    >
      {label}
    </button>
  )
}

// ── 1. Pre-upload empty state ───────────────────────────────────────

export function PreUploadEmpty() {
  return (
    <EmptyWrapper
      icon={<Upload className="h-10 w-10" />}
      title="Upload a file to get started"
      description="Go to the Dashboard to upload a 23andMe raw data file."
    />
  )
}

// ── 2. Pre-annotation empty state ───────────────────────────────────

export function PreAnnotationEmpty({
  totalVariants,
  onShowUnannotated,
}: {
  totalVariants: number
  onShowUnannotated: () => void
}) {
  return (
    <EmptyWrapper
      icon={<FlaskConical className="h-10 w-10" />}
      title="Run annotation to see results here"
      description={`${totalVariants.toLocaleString()} variants uploaded. Run the annotation pipeline to add ClinVar, gnomAD, and other annotations.`}
    >
      <SuggestionButton label="Show raw variants" onClick={onShowUnannotated} />
    </EmptyWrapper>
  )
}

// ── 3. No filter match empty state ──────────────────────────────────

export function NoMatchEmpty({
  searchQuery,
  hasActiveFilter,
  onClearSearch,
  onClearFilters,
  onApplyFilter,
}: {
  searchQuery: string
  hasActiveFilter: boolean
  onClearSearch: () => void
  onClearFilters: () => void
  onApplyFilter: (filter: string) => void
}) {
  return (
    <EmptyWrapper
      icon={<SearchX className="h-10 w-10" />}
      title="No variants match your filters"
      description="Try adjusting your search or filters to find variants."
    >
      <div className="flex flex-wrap gap-2 justify-center">
        {searchQuery && (
          <SuggestionButton label="Clear search" onClick={onClearSearch} />
        )}
        {hasActiveFilter && (
          <SuggestionButton label="Clear filters" onClick={onClearFilters} />
        )}
        {FILTER_SUGGESTIONS.map((suggestion) => (
          <SuggestionButton
            key={suggestion.filter}
            label={suggestion.label}
            onClick={() => onApplyFilter(suggestion.filter)}
          />
        ))}
      </div>
    </EmptyWrapper>
  )
}

// ── 4. Error empty state ────────────────────────────────────────────

export function ErrorEmpty({ message }: { message: string }) {
  return (
    <EmptyWrapper
      icon={<ShieldAlert className="h-10 w-10 text-destructive/60" />}
      title="Error loading variants"
      description={message}
    />
  )
}
