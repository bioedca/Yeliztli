/** Two-level sample selector dropdown in the top nav (Step 49 / IND-05; Plan §9.5).
 *
 * Renders one expandable row per `individuals` row plus a terminal
 * "Unassigned" group covering every sample whose `individual_id` is NULL.
 * Clicking a sample writes `?sample_id=<id>` into the URL — every page
 * already reads that param via `parseSampleId()`.
 *
 * Data sources
 * ────────────
 *   useSamples()      — full sample list (terminal leaf nodes + Unassigned)
 *   useIndividuals()  — list of individuals (group headers)
 *   useQueries(...)   — per-individual detail, expanded for `linked_samples`
 *
 * The two-pass fan-out keeps the cache coherent with `useIndividual(id)`
 * (same `individualsKeys.detail(id)` key), so opening `/individuals/{id}`
 * after picking from the dropdown reuses the prefetched detail.
 *
 * "Unassigned" is computed client-side as `samples − union(linked_samples)`
 * because `SampleResponse` does not surface `individual_id` (Plan §9.5
 * groups by linkage, not by sample-row column).
 */

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react"
import { useSearchParams } from "react-router-dom"
import { useQueries } from "@tanstack/react-query"
import {
  Check,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  User,
  UserX,
} from "lucide-react"

import { useSamples } from "@/api/samples"
import { useIndividuals, individualsKeys } from "@/api/individuals"
import type { IndividualDetail, IndividualSummary, LinkedSample } from "@/types/individuals"
import type { Sample } from "@/types/samples"
import { formatFileFormat, parseSampleId } from "@/lib/format"
import { cn } from "@/lib/utils"

interface IndividualGroup {
  individual: IndividualSummary
  samples: LinkedSample[]
}

interface SelectorState {
  groups: IndividualGroup[]
  unassigned: Sample[]
  activeIndividualId: number | null
  activeSampleName: string | null
}

function buildSelectorState(
  samples: Sample[] | undefined,
  individuals: IndividualSummary[] | undefined,
  details: Array<IndividualDetail | undefined>,
  activeSampleId: number | null,
): SelectorState {
  const groups: IndividualGroup[] = []
  const linkedSampleIds = new Set<number>()
  let activeIndividualId: number | null = null
  let activeSampleName: string | null = null

  if (individuals) {
    individuals.forEach((ind, idx) => {
      const detail = details[idx]
      const linkedSamples = detail?.linked_samples ?? []
      for (const linked of linkedSamples) {
        linkedSampleIds.add(linked.id)
        if (activeSampleId != null && linked.id === activeSampleId) {
          activeIndividualId = ind.id
          activeSampleName = linked.name
        }
      }
      groups.push({ individual: ind, samples: linkedSamples })
    })
  }

  const unassigned: Sample[] = []
  if (samples) {
    for (const sample of samples) {
      if (!linkedSampleIds.has(sample.id)) {
        unassigned.push(sample)
        if (
          activeSampleId != null &&
          sample.id === activeSampleId &&
          activeSampleName == null
        ) {
          activeSampleName = sample.name
        }
      }
    }
  }

  return { groups, unassigned, activeIndividualId, activeSampleName }
}

export default function IndividualSelector() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeSampleId = parseSampleId(searchParams.get("sample_id"))

  const { data: samples, isLoading: samplesLoading } = useSamples()
  const { data: individuals, isLoading: individualsLoading } = useIndividuals()

  const detailQueries = useQueries({
    queries: (individuals ?? []).map((ind) => ({
      queryKey: individualsKeys.detail(ind.id),
      queryFn: async (): Promise<IndividualDetail> => {
        const res = await fetch(`/api/individuals/${ind.id}`)
        if (!res.ok) throw new Error(`Failed to fetch individual ${ind.id}`)
        return (await res.json()) as IndividualDetail
      },
    })),
  })
  const details = detailQueries.map((q) => q.data as IndividualDetail | undefined)

  // detailQueries identities change on each render, so we can't list them
  // directly. Key on each query's `dataUpdatedAt`, which advances whenever
  // React Query receives fresh detail data — this captures linked-sample
  // changes under an unchanged individual id (id-only keying would miss them).
  const detailsFingerprint = detailQueries
    .map((q) => q.dataUpdatedAt)
    .join("|")
  const state = useMemo(
    () => buildSelectorState(samples, individuals, details, activeSampleId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [samples, individuals, detailsFingerprint, activeSampleId],
  )

  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set())
  // The one treeitem in the tab order (WAI-ARIA tree pattern: roving tabindex).
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const treeRef = useRef<HTMLDivElement>(null)

  // Visible treeitems in DOM order; collapsed children are not rendered.
  const treeItems = () =>
    Array.from(treeRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [])

  const focusItem = (item: HTMLElement | null | undefined) => {
    if (!item) return
    item.focus()
    setActiveKey(item.dataset.treeKey ?? null)
  }

  // Closing unmounts the focused treeitem, which would drop focus to <body>
  // (WCAG 2.4.3); hand it back to the trigger instead (#2066).
  const closeAndRestoreFocus = () => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  const focusInitialItem = () => {
    const items = treeItems()
    focusItem(items.find((el) => el.getAttribute("aria-selected") === "true") ?? items[0])
  }

  // Auto-expand the active individual when its sample becomes selected so
  // the active row is visible on first open.
  useEffect(() => {
    if (state.activeIndividualId == null) return
    setExpanded((prev) => {
      if (prev.has(state.activeIndividualId!)) return prev
      const next = new Set(prev)
      next.add(state.activeIndividualId!)
      return next
    })
  }, [state.activeIndividualId])

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [open])

  // Opening moves focus into the tree: the selected sample if visible, else
  // the first item, so the arrow keys work straight away.
  useEffect(() => {
    if (open) focusInitialItem()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (samplesLoading || individualsLoading) {
    return (
      <span className="text-sm text-muted-foreground hidden sm:block">
        Loading...
      </span>
    )
  }

  const hasAnySample =
    (samples && samples.length > 0) ||
    state.groups.some((g) => g.samples.length > 0)
  if (!hasAnySample) {
    return (
      <span className="text-sm text-muted-foreground hidden sm:block">
        No sample loaded
      </span>
    )
  }

  const activeIndividual = state.groups.find(
    (g) => g.individual.id === state.activeIndividualId,
  )?.individual
  const label = state.activeSampleName
    ? activeIndividual
      ? `${activeIndividual.display_name} / ${state.activeSampleName}`
      : state.activeSampleName
    : "Select sample"

  const toggleExpanded = (individualId: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(individualId)) next.delete(individualId)
      else next.add(individualId)
      return next
    })
  }

  const selectSample = (sampleId: number) => {
    const params = new URLSearchParams(searchParams)
    params.set("sample_id", String(sampleId))
    setSearchParams(params)
    closeAndRestoreFocus()
  }

  // WAI-ARIA tree keyboard pattern: Up/Down move between visible items,
  // Home/End jump to the ends, Right expands a closed individual or enters an
  // open one, Left collapses an open individual or returns to the parent.
  // Enter/Space activate through the native <button>.
  const onTreeKeyDown = (e: ReactKeyboardEvent<HTMLElement>) => {
    const items = treeItems()
    const current = e.currentTarget
    const index = items.indexOf(current)
    if (index === -1) return
    const expandedAttr = current.getAttribute("aria-expanded")
    const individualId = Number(current.dataset.individualId)
    let next: HTMLElement | null | undefined
    switch (e.key) {
      case "ArrowDown":
        next = items[index + 1]
        break
      case "ArrowUp":
        next = items[index - 1]
        break
      case "Home":
        next = items[0]
        break
      case "End":
        next = items[items.length - 1]
        break
      case "ArrowRight":
        if (expandedAttr === "false") {
          e.preventDefault()
          toggleExpanded(individualId)
          return
        }
        if (expandedAttr === "true") {
          next = current.parentElement?.querySelector<HTMLElement>(
            '[role="group"] [role="treeitem"]',
          )
        }
        break
      case "ArrowLeft":
        if (expandedAttr === "true") {
          e.preventDefault()
          toggleExpanded(individualId)
          return
        }
        next = current
          .closest('[role="group"]')
          ?.parentElement?.querySelector<HTMLElement>(':scope > [role="treeitem"]')
        break
      default:
        return
    }
    e.preventDefault()
    focusItem(next)
  }

  return (
    <div ref={containerRef} className="relative hidden sm:block">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key !== "ArrowDown") return
          e.preventDefault()
          if (open) focusInitialItem()
          else setOpen(true)
        }}
        className={cn(
          "flex items-center gap-2 text-sm border border-input rounded-md px-3 py-1.5",
          "hover:bg-accent hover:text-accent-foreground transition-colors",
          "max-w-[280px]",
        )}
        aria-haspopup="tree"
        aria-expanded={open}
        aria-label="Switch sample"
      >
        <FlaskConical className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="truncate">{label}</span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div
          className="absolute top-full right-0 mt-1 w-80 bg-popover border border-border rounded-md shadow-md z-50 py-1 max-h-96 overflow-y-auto"
          ref={treeRef}
          role="tree"
          aria-label="Individuals and samples"
        >
          {state.groups.map((group) => {
            const isExpanded = expanded.has(group.individual.id)
            const childCount = group.samples.length
            return (
              <div key={`ind-${group.individual.id}`} role="none">
                <button
                  type="button"
                  role="treeitem"
                  aria-expanded={isExpanded}
                  aria-selected={false}
                  tabIndex={activeKey === `ind-${group.individual.id}` ? 0 : -1}
                  onFocus={() => setActiveKey(`ind-${group.individual.id}`)}
                  onKeyDown={onTreeKeyDown}
                  data-tree-key={`ind-${group.individual.id}`}
                  data-individual-id={group.individual.id}
                  aria-label={`${group.individual.display_name}, ${childCount} sample${childCount === 1 ? "" : "s"}`}
                  onClick={() => toggleExpanded(group.individual.id)}
                  className={cn(
                    "w-full flex items-center gap-2 px-2 py-1.5 text-sm text-left",
                    "hover:bg-accent hover:text-accent-foreground transition-colors",
                  )}
                  data-testid={`individual-row-${group.individual.id}`}
                >
                  {isExpanded ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <User className="h-3.5 w-3.5 shrink-0 text-primary" />
                  <span className="flex-1 truncate font-medium">
                    {group.individual.display_name}
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {childCount}
                  </span>
                </button>
                {isExpanded && (
                  <div role="group">
                    {childCount === 0 && (
                      <div className="pl-9 pr-3 py-1.5 text-xs italic text-muted-foreground">
                        No linked samples
                      </div>
                    )}
                    {group.samples.map((sample) => {
                      const isActive = sample.id === activeSampleId
                      return (
                        <button
                          key={`sample-${sample.id}`}
                          type="button"
                          role="treeitem"
                          aria-selected={isActive}
                          tabIndex={activeKey === `sample-${sample.id}` ? 0 : -1}
                          onFocus={() => setActiveKey(`sample-${sample.id}`)}
                          onKeyDown={onTreeKeyDown}
                          data-tree-key={`sample-${sample.id}`}
                          onClick={() => selectSample(sample.id)}
                          className={cn(
                            "w-full flex items-center gap-2 pl-9 pr-3 py-1.5 text-sm text-left",
                            "hover:bg-accent hover:text-accent-foreground transition-colors",
                            isActive && "bg-accent/50",
                          )}
                          data-testid={`sample-row-${sample.id}`}
                        >
                          <Check
                            className={cn(
                              "h-3.5 w-3.5 shrink-0",
                              isActive ? "text-primary" : "text-transparent",
                            )}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="truncate">{sample.name}</div>
                            <div className="text-xs text-muted-foreground">
                              {formatFileFormat(sample.file_format)}
                              {sample.created_at && (
                                <>
                                  {" · "}
                                  {new Date(sample.created_at).toLocaleDateString()}
                                </>
                              )}
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}

          {state.unassigned.length > 0 && (
            <div role="none">
              {state.groups.length > 0 && (
                <div
                  role="separator"
                  className="my-1 border-t border-border"
                  aria-hidden="true"
                />
              )}
              <div
                className="flex items-center gap-2 px-2 py-1.5 text-xs uppercase tracking-wide text-muted-foreground"
                role="presentation"
              >
                <UserX className="h-3.5 w-3.5" />
                <span>Unassigned ({state.unassigned.length})</span>
              </div>
              {state.unassigned.map((sample) => {
                const isActive = sample.id === activeSampleId
                return (
                  <button
                    key={`unassigned-${sample.id}`}
                    type="button"
                    role="treeitem"
                    aria-selected={isActive}
                    tabIndex={activeKey === `unassigned-${sample.id}` ? 0 : -1}
                    onFocus={() => setActiveKey(`unassigned-${sample.id}`)}
                    onKeyDown={onTreeKeyDown}
                    data-tree-key={`unassigned-${sample.id}`}
                    onClick={() => selectSample(sample.id)}
                    className={cn(
                      "w-full flex items-center gap-2 pl-9 pr-3 py-1.5 text-sm text-left",
                      "hover:bg-accent hover:text-accent-foreground transition-colors",
                      isActive && "bg-accent/50",
                    )}
                    data-testid={`sample-row-${sample.id}`}
                  >
                    <Check
                      className={cn(
                        "h-3.5 w-3.5 shrink-0",
                        isActive ? "text-primary" : "text-transparent",
                      )}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="truncate">{sample.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {formatFileFormat(sample.file_format)}
                        {sample.created_at && (
                          <>
                            {" · "}
                            {new Date(sample.created_at).toLocaleDateString()}
                          </>
                        )}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
