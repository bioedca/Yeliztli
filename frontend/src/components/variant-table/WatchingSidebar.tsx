/** Watching section in variant table sidebar (P4-21k).
 *
 * Lists all watched variants with current ClinVar significance and watched_at date.
 * Sortable by watched_at and by significance change (reclassified variants surfaced first).
 */

import { useMemo, useState } from "react"
import { Eye, EyeOff, ArrowUpDown, AlertTriangle, ChevronDown, ChevronRight } from "lucide-react"

import { useWatchedVariants, useUnwatchVariant, type WatchedVariant } from "@/api/watches"
import { cn } from "@/lib/utils"

type SortMode = "watched_at" | "reclassified"

interface WatchingSidebarProps {
  sampleId: number | null
  onSelectVariant?: (rsid: string) => void
  selectedRsid?: string | null
}

/** Check whether a watched variant has been reclassified since it was watched. */
function isReclassified(v: WatchedVariant): boolean {
  if (v.clinvar_significance_at_watch == null && v.clinvar_significance_current == null) {
    return false
  }
  return v.clinvar_significance_at_watch !== v.clinvar_significance_current
}

/** Format a significance string for compact display. */
function formatSignificance(sig: string | null): string {
  if (!sig) return "—"
  return sig.replace(/_/g, " ")
}

/** Format watched_at date for compact display. */
function formatDate(dateStr: string): string {
  if (!dateStr) return ""
  try {
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
  } catch {
    return dateStr
  }
}

export default function WatchingSidebar({
  sampleId,
  onSelectVariant,
  selectedRsid,
}: WatchingSidebarProps) {
  const { data: watchedVariants, isLoading } = useWatchedVariants(sampleId)
  const unwatchMutation = useUnwatchVariant(sampleId)
  const [sortMode, setSortMode] = useState<SortMode>("watched_at")
  const [expanded, setExpanded] = useState(true)
  const [pendingUnwatch, setPendingUnwatch] = useState<string | null>(null)

  const sortedVariants = useMemo(() => {
    if (!watchedVariants?.length) return []
    const sorted = [...watchedVariants]

    if (sortMode === "reclassified") {
      // Reclassified variants first, then by watched_at desc
      sorted.sort((a, b) => {
        const aReclass = isReclassified(a) ? 0 : 1
        const bReclass = isReclassified(b) ? 0 : 1
        if (aReclass !== bReclass) return aReclass - bReclass
        return new Date(b.watched_at).getTime() - new Date(a.watched_at).getTime()
      })
    }
    // Default "watched_at" order is already desc from API

    return sorted
  }, [watchedVariants, sortMode])

  const reclassifiedCount = useMemo(
    () => sortedVariants.filter(isReclassified).length,
    [sortedVariants],
  )

  if (sampleId == null) return null

  return (
    <div className="border-b border-border bg-card" data-testid="watching-sidebar">
      {/* Collapsible header */}
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex items-center gap-2 w-full px-4 py-2 text-sm font-medium text-foreground hover:bg-accent/50 transition-colors"
        aria-expanded={expanded}
        aria-controls="watching-sidebar-content"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
        <Eye className="h-4 w-4 text-amber-500" />
        <span>Watching</span>
        {watchedVariants && watchedVariants.length > 0 && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
            {reclassifiedCount > 0 && (
              <span className="flex items-center gap-0.5 text-amber-700 dark:text-amber-400">
                <AlertTriangle className="h-3 w-3" />
                {reclassifiedCount}
              </span>
            )}
            <span>{watchedVariants.length}</span>
          </span>
        )}
      </button>

      {expanded && (
        <div id="watching-sidebar-content" className="px-4 pb-3">
          {isLoading ? (
            <p className="text-xs text-muted-foreground py-2">Loading watched variants...</p>
          ) : !watchedVariants?.length ? (
            <p className="text-xs text-muted-foreground py-2">
              No watched variants. Click the eye icon on a variant to start watching.
            </p>
          ) : (
            <>
              {/* Sort toggle */}
              <div className="flex items-center gap-1.5 mb-2">
                <ArrowUpDown className="h-3 w-3 text-muted-foreground" />
                <button
                  type="button"
                  onClick={() => setSortMode(sortMode === "watched_at" ? "reclassified" : "watched_at")}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                  aria-label={`Sort by ${sortMode === "watched_at" ? "reclassification status" : "date watched"}`}
                >
                  {sortMode === "watched_at" ? "Sort: Date watched" : "Sort: Reclassified first"}
                </button>
              </div>

              {/* Watched variant list */}
              <ul className="space-y-1" aria-label="Watched variants">
                {sortedVariants.map((v) => {
                  const reclassified = isReclassified(v)
                  return (
                    <li
                      key={v.rsid}
                      className={cn(
                        "group relative rounded-md text-xs transition-colors",
                        reclassified && "ring-1 ring-amber-300 dark:ring-amber-700",
                      )}
                    >
                      <button
                        type="button"
                        className={cn(
                          "flex items-start gap-2 px-2 py-1.5 w-full text-left cursor-pointer transition-colors rounded-md bg-transparent border-none text-inherit text-xs",
                          selectedRsid === v.rsid
                            ? "bg-accent"
                            : "hover:bg-accent/50",
                        )}
                        onClick={() => onSelectVariant?.(v.rsid)}
                        aria-label={`${v.rsid}${reclassified ? " — reclassified" : ""}`}
                      >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono font-medium truncate">{v.rsid}</span>
                          {reclassified && (
                            <AlertTriangle
                              className="h-3 w-3 shrink-0 text-amber-500"
                              aria-label="Reclassified since watched"
                            />
                          )}
                        </div>
                        <div className="text-muted-foreground mt-0.5">
                          {reclassified ? (
                            <span>
                              <span className="line-through">{formatSignificance(v.clinvar_significance_at_watch)}</span>
                              {" → "}
                              <span className="text-amber-700 dark:text-amber-400 font-medium">
                                {formatSignificance(v.clinvar_significance_current)}
                              </span>
                            </span>
                          ) : (
                            <span>{formatSignificance(v.clinvar_significance_current)}</span>
                          )}
                        </div>
                        <div className="text-muted-foreground mt-0.5">
                          {formatDate(v.watched_at)}
                        </div>
                      </div>
                      </button>
                      {/* Unwatch button on hover */}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setPendingUnwatch(v.rsid)
                          unwatchMutation.mutate(v.rsid, {
                            onSettled: () => setPendingUnwatch(null),
                          })
                        }}
                        className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 focus:opacity-100 p-0.5 rounded hover:bg-destructive/10 focus:bg-destructive/10 text-muted-foreground hover:text-destructive focus:text-destructive transition-all"
                        aria-label={`Unwatch ${v.rsid}`}
                        disabled={pendingUnwatch === v.rsid}
                      >
                        <EyeOff className="h-3 w-3" />
                      </button>
                    </li>

                  )
                })}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  )
}
