/** Query builder page (P4-02) with SQL console tab (P4-04).
 *
 * Layout:
 * - Page header with icon
 * - Tabs: Visual Builder | SQL Console
 * - Visual Builder: QueryBuilder panel + action bar + results + saved queries sidebar
 * - SQL Console: Monaco SQL editor + results table + schema sidebar
 */

import { useCallback, useContext, useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { prepareRuleGroup, type RuleGroupType } from "react-querybuilder"
import { Filter, Play, Loader2, AlertCircle, RotateCcw, Terminal } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import PageEmpty from "@/components/ui/PageEmpty"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import { useQueryFields, useRunQuery, useExportQuery } from "@/api/query-builder"
import { normalizeFilterForApi, toEditorFilter } from "@/lib/query-filter"
import { throwApiError } from "@/api/errors"
import type { QueryResultPage, QueryExportFormat, RuleGroupModel, SavedQuery } from "@/types/query-builder"
import QueryBuilderPanel from "@/components/query-builder/QueryBuilderPanel"
import QueryResultsTable from "@/components/query-builder/QueryResultsTable"
import SavedQueriesPanel from "@/components/query-builder/SavedQueriesPanel"
import SqlConsole from "@/components/query-builder/SqlConsole"
import { QueryBuilderDraftContext } from "@/components/query-builder/QueryBuilderDraftContext"

type TabId = "visual" | "sql"

export default function QueryBuilderView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [activeTab, setActiveTab] = useState<TabId>("visual")
  const draft = useContext(QueryBuilderDraftContext)
  if (!draft) {
    throw new Error("QueryBuilderView must render inside QueryBuilderDraftProvider")
  }
  const { query, setQuery, resetQuery } = draft
  const [resultPages, setResultPages] = useState<QueryResultPage[]>([])
  const [hasExecuted, setHasExecuted] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)
  const [includeAllPositions, setIncludeAllPositions] = useState(false)
  const [isFetchingMore, setIsFetchingMore] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const resultGenerationRef = useRef(0)
  const previousSampleIdRef = useRef(sampleId)

  const fieldsQuery = useQueryFields()
  const runQuery = useRunQuery()
  const exportQuery = useExportQuery()

  useEffect(() => {
    if (previousSampleIdRef.current === sampleId) return
    previousSampleIdRef.current = sampleId
    resultGenerationRef.current += 1
    setResultPages([])
    setHasExecuted(false)
    setIncludeAllPositions(false)
    setLoadMoreError(null)
    setIsFetchingMore(false)
    setIsRunning(false)
    setRunError(null)
    setExportError(null)
  }, [sampleId])

  const handleExport = useCallback(
    (format: string) => {
      if (!sampleId) return
      const filter = query as unknown as RuleGroupModel
      const resultGeneration = resultGenerationRef.current
      setExportError(null)
      exportQuery.mutate(
        {
          sampleId,
          filter,
          format: format as QueryExportFormat,
          includeAllPositions,
        },
        {
          onError: (error) => {
            if (resultGeneration !== resultGenerationRef.current) return
            setExportError(error instanceof Error ? error.message : "Export failed.")
          },
        },
      )
    },
    [sampleId, query, exportQuery, includeAllPositions],
  )

  const handleLoadSaved = useCallback((saved: SavedQuery) => {
    resultGenerationRef.current += 1
    // Saved filters come back without rule ids (the API model drops them), and
    // react-querybuilder re-keys an id-less tree on every change, remounting the
    // input mid-edit. Prepare ids once so a loaded query edits like a fresh one.
    setQuery(prepareRuleGroup(toEditorFilter(saved.filter) as unknown as RuleGroupType))
    setResultPages([])
    setHasExecuted(false)
    setIncludeAllPositions(false)
    setLoadMoreError(null)
    setIsFetchingMore(false)
    setIsRunning(false)
    setRunError(null)
    setExportError(null)
  }, [setQuery])

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Query Builder</h1>
        <PageEmpty icon={Filter} title="Select a sample to build queries against annotated variants." />
      </div>
    )
  }

  const handleRun = async () => {
    if (!sampleId) return
    const filter = query as unknown as RuleGroupModel
    const resultGeneration = ++resultGenerationRef.current
    setResultPages([])
    setHasExecuted(false)
    setLoadMoreError(null)
    setIsFetchingMore(false)
    setIsRunning(true)
    setRunError(null)
    setExportError(null)
    try {
      const data = await runQuery.mutateAsync({ sampleId, filter, includeAllPositions })
      if (resultGeneration !== resultGenerationRef.current) return
      setResultPages([data])
      setHasExecuted(true)
    } catch (error) {
      if (resultGeneration !== resultGenerationRef.current) return
      setRunError(error instanceof Error ? error.message : "An unexpected error occurred.")
    } finally {
      if (resultGeneration === resultGenerationRef.current) {
        setIsRunning(false)
      }
    }
  }

  const handleLoadMore = () => {
    if (!sampleId || resultPages.length === 0) return
    const lastPage = resultPages[resultPages.length - 1]
    if (!lastPage.has_more || !lastPage.next_cursor_chrom || lastPage.next_cursor_pos == null) return
    const resultGeneration = resultGenerationRef.current
    setLoadMoreError(null)
    setIsFetchingMore(true)

    const filter = normalizeFilterForApi(query as unknown as RuleGroupModel)
    fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sample_id: sampleId,
        filter,
        include_all_positions: includeAllPositions,
        cursor_chrom: lastPage.next_cursor_chrom,
        cursor_pos: lastPage.next_cursor_pos,
        limit: 50,
      }),
    })
      .then(async (res) => {
        if (!res.ok) {
          await throwApiError(res, "Unable to load more results. Please try again.")
        }
        return res.json()
      })
      .then((data: QueryResultPage) => {
        if (resultGeneration !== resultGenerationRef.current) return
        setResultPages((prev) => [...prev, data])
      })
      .catch((err) => {
        if (resultGeneration !== resultGenerationRef.current) return
        setLoadMoreError(err instanceof Error ? err.message : "Failed to load more results")
      })
      .finally(() => {
        if (resultGeneration === resultGenerationRef.current) {
          setIsFetchingMore(false)
        }
      })
  }

  const handleClear = () => {
    resultGenerationRef.current += 1
    resetQuery()
    setResultPages([])
    setHasExecuted(false)
    setIncludeAllPositions(false)
    setLoadMoreError(null)
    setIsFetchingMore(false)
    setIsRunning(false)
    setRunError(null)
    setExportError(null)
  }

  const handleQueryChange = (nextQuery: RuleGroupType) => {
    resultGenerationRef.current += 1
    setQuery(nextQuery)
    setResultPages([])
    setHasExecuted(false)
    setLoadMoreError(null)
    setIsFetchingMore(false)
    setIsRunning(false)
    setRunError(null)
    setExportError(null)
  }

  const handleIncludeAllPositionsChange = (checked: boolean) => {
    resultGenerationRef.current += 1
    setIncludeAllPositions(checked)
    // Results and exports must always use the same carriage mode. Clearing the
    // old pages forces an explicit rerun instead of leaving a stale result set.
    setResultPages([])
    setHasExecuted(false)
    setLoadMoreError(null)
    setIsFetchingMore(false)
    setIsRunning(false)
    setRunError(null)
    setExportError(null)
  }

  const hasRules = query.rules.length > 0
  const resultsMatchSample = previousSampleIdRef.current === sampleId
  const totalMatching = resultPages.length > 0 ? (resultPages[0].total_matching ?? null) : null
  const hasMore = resultPages.length > 0 && resultPages[resultPages.length - 1].has_more

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
          <Filter className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Query Builder</h1>
          <p className="text-sm text-muted-foreground">
            Build custom filters or write raw SQL against annotated variants
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div
        className="flex border-b border-border mb-6"
        role="tablist"
        aria-label="Query mode"
      >
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "visual"}
          id="tab-visual"
          aria-controls="panel-visual"
          onClick={() => setActiveTab("visual")}
          className={cn(
            "inline-flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px",
            activeTab === "visual"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground hover:border-border",
          )}
          data-testid="tab-visual"
        >
          <Filter className="h-4 w-4" />
          Visual Builder
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "sql"}
          id="tab-sql"
          aria-controls="panel-sql"
          onClick={() => setActiveTab("sql")}
          className={cn(
            "inline-flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px",
            activeTab === "sql"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground hover:border-border",
          )}
          data-testid="tab-sql"
        >
          <Terminal className="h-4 w-4" />
          SQL Console
        </button>
      </div>

      {/* Visual Builder tab */}
      {activeTab === "visual" && (
        <div id="panel-visual" role="tabpanel" aria-labelledby="tab-visual">
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
            {/* Main content */}
            <div className="space-y-4">
              {/* Query builder */}
              {fieldsQuery.isLoading && (
                <PageLoading message="Loading field metadata..." />
              )}

              {fieldsQuery.isError && (
                <PageError
                  message={fieldsQuery.error instanceof Error ? fieldsQuery.error.message : "Failed to load field metadata."}
                  onRetry={() => fieldsQuery.refetch()}
                />
              )}

              {fieldsQuery.data && (
                <section aria-label="Query builder">
                  <QueryBuilderPanel
                    fields={fieldsQuery.data.fields}
                    query={query}
                    onQueryChange={handleQueryChange}
                  />
                </section>
              )}

              {/* Action bar */}
              <div
                className="flex flex-wrap items-center gap-3"
                data-testid="query-builder-action-bar"
              >
                <button
                  type="button"
                  onClick={handleRun}
                  disabled={!hasRules || isRunning}
                  className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  data-testid="run-query-btn"
                >
                  {isRunning ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                  Run Query
                </button>
                <button
                  type="button"
                  onClick={handleClear}
                  disabled={!hasRules && !hasExecuted}
                  className="inline-flex items-center gap-2 rounded-md border border-input px-4 py-2 text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  data-testid="clear-query-btn"
                >
                  <RotateCcw className="h-4 w-4" />
                  Clear
                </button>
                <div className="inline-flex items-center">
                  <label
                    className="inline-flex items-center gap-2 text-sm text-muted-foreground"
                    title="Also return homozygous-reference and unresolved annotated positions. Their annotations are not shown as carried findings."
                  >
                    <input
                      type="checkbox"
                      checked={includeAllPositions}
                      onChange={(event) => handleIncludeAllPositionsChange(event.target.checked)}
                      aria-describedby="include-all-positions-help"
                      className="h-4 w-4 rounded border-input accent-primary"
                      data-testid="include-all-positions"
                    />
                    Include all annotated positions
                  </label>
                  <span id="include-all-positions-help" className="sr-only">
                    Includes homozygous-reference and unresolved positions. Their annotations
                    are shown as locus metadata, not as carried findings.
                  </span>
                </div>
              </div>

              {/* Error */}
              {resultsMatchSample && runError && (
                <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium text-destructive">Query failed</p>
                      <p className="text-sm text-muted-foreground mt-1">
                        {runError}
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {resultsMatchSample && exportError && (
                <div
                  className="rounded-lg border border-destructive/50 bg-destructive/5 p-4"
                  role="alert"
                >
                  <div className="flex items-start gap-3">
                    <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium text-destructive">Export failed</p>
                      <p className="text-sm text-muted-foreground mt-1">{exportError}</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Results */}
              {resultsMatchSample && hasExecuted && resultPages.length > 0 && resultPages[0].items.length > 0 && (
                <section aria-label="Query results">
                  <QueryResultsTable
                    pages={resultPages}
                    totalMatching={totalMatching}
                    hasMore={hasMore}
                    isFetchingMore={isFetchingMore}
                    onLoadMore={handleLoadMore}
                    onExport={handleExport}
                    isExporting={exportQuery.isPending}
                    includeAllPositions={includeAllPositions}
                  />
                </section>
              )}

              {/* Load more error */}
              {resultsMatchSample && loadMoreError && (
                <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium text-destructive">Failed to load more results</p>
                      <p className="text-sm text-muted-foreground mt-1">{loadMoreError}</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Empty state after executing */}
              {resultsMatchSample && hasExecuted && resultPages.length > 0 && resultPages[0].items.length === 0 && (
                <div className="rounded-lg border bg-card p-8 text-center">
                  <Filter className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
                  <p className="text-muted-foreground">
                    No variants match your query criteria.
                  </p>
                  <p className="text-xs text-muted-foreground mt-2">
                    Try adjusting your filters or using broader criteria.
                  </p>
                </div>
              )}
            </div>

            {/* Sidebar: Saved queries */}
            <aside className="space-y-4">
              <SavedQueriesPanel
                currentFilter={query as unknown as RuleGroupModel}
                onLoad={handleLoadSaved}
              />
            </aside>
          </div>
        </div>
      )}

      {/* SQL Console tab */}
      {activeTab === "sql" && (
        <div id="panel-sql" role="tabpanel" aria-labelledby="tab-sql">
          <SqlConsole sampleId={sampleId} />
        </div>
      )}
    </div>
  )
}
