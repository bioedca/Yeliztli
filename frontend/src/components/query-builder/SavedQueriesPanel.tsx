/** Saved queries panel (P4-06).
 *
 * Full CRUD for named query management:
 * - Create: save current filter as a named query
 * - Read: list all saved queries, load into builder
 * - Update: rename queries, overwrite filter with current
 * - Delete: remove with confirmation
 */

import { useState, useRef, useEffect } from "react"
import { Loader2, Save, Trash2, FolderOpen, Clock, Pencil, RefreshCw, Check, X } from "lucide-react"
import { useSavedQueries, useSaveQuery, useUpdateSavedQuery, useDeleteSavedQuery } from "@/api/query-builder"
import type { RuleGroupModel, SavedQuery } from "@/types/query-builder"

interface SavedQueriesPanelProps {
  currentFilter: RuleGroupModel
  onLoad: (query: SavedQuery) => void
}

export default function SavedQueriesPanel({
  currentFilter,
  onLoad,
}: SavedQueriesPanelProps) {
  const [saveName, setSaveName] = useState("")
  const [showSaveInput, setShowSaveInput] = useState(false)
  const [editingName, setEditingName] = useState<string | null>(null)
  const [editValue, setEditValue] = useState("")
  const [overwriteError, setOverwriteError] = useState<string | null>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)

  const savedQueries = useSavedQueries()
  const saveQuery = useSaveQuery()
  const updateQuery = useUpdateSavedQuery()
  const deleteQuery = useDeleteSavedQuery()

  useEffect(() => {
    if (editingName && renameInputRef.current) {
      renameInputRef.current.focus()
      renameInputRef.current.select()
    }
  }, [editingName])

  const handleSave = () => {
    const name = saveName.trim()
    if (!name) return
    saveQuery.mutate(
      { name, filter: currentFilter },
      {
        onSuccess: () => {
          setSaveName("")
          setShowSaveInput(false)
        },
      },
    )
  }

  const handleDelete = (name: string) => {
    if (!window.confirm(`Delete query "${name}"?`)) return
    deleteQuery.mutate(name)
  }

  const handleStartRename = (name: string) => {
    setEditingName(name)
    setEditValue(name)
  }

  const handleCancelRename = () => {
    setEditingName(null)
    setEditValue("")
  }

  const handleConfirmRename = () => {
    if (!editingName) return
    const newName = editValue.trim()
    if (!newName || newName === editingName) {
      handleCancelRename()
      return
    }
    updateQuery.mutate(
      { name: editingName, new_name: newName },
      { onSuccess: () => handleCancelRename() },
    )
  }

  const handleOverwrite = (name: string) => {
    if (!window.confirm(`Overwrite "${name}" with the current filter?`)) return
    setOverwriteError(null)
    updateQuery.mutate(
      { name, filter: currentFilter },
      {
        onSuccess: () => setOverwriteError(null),
        onError: (err) => setOverwriteError(err instanceof Error ? err.message : "Overwrite failed"),
      },
    )
  }

  const hasRules = currentFilter.rules && currentFilter.rules.length > 0

  return (
    <div className="rounded-lg border border-border bg-card" data-testid="saved-queries-panel">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <FolderOpen className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold">Saved Queries</h3>
        </div>
        {!showSaveInput && (
          <button
            type="button"
            onClick={() => setShowSaveInput(true)}
            disabled={!hasRules}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-xs font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title={hasRules ? "Save current query" : "Add rules to save a query"}
            data-testid="save-query-btn"
          >
            <Save className="h-3.5 w-3.5" />
            Save
          </button>
        )}
      </div>

      {/* Save input */}
      {showSaveInput && (
        <div className="px-4 py-3 border-b border-border bg-muted/30">
          <label htmlFor="query-name-input" className="text-xs font-medium text-muted-foreground mb-1 block">
            Query name
          </label>
          <div className="flex gap-2">
            <input
              id="query-name-input"
              type="text"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="e.g., Pathogenic rare variants"
              className="flex-1 rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSave()
                if (e.key === "Escape") setShowSaveInput(false)
              }}
              data-testid="query-name-input"
              ref={(el) => {
                if (el && !el.dataset.focused) {
                  el.focus()
                  el.dataset.focused = 'true'
                }
              }}
            />
            <button
              type="button"
              onClick={handleSave}
              disabled={!saveName.trim() || saveQuery.isPending}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-xs font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              {saveQuery.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setShowSaveInput(false)
                setSaveName("")
              }}
              className="rounded-md border border-input px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors"
            >
              Cancel
            </button>
          </div>
          {saveQuery.isError && (
            <p className="text-xs text-destructive-text mt-1">
              {saveQuery.error instanceof Error ? saveQuery.error.message : "Failed to save"}
            </p>
          )}
        </div>
      )}

      {/* Query list */}
      <div className="max-h-[300px] overflow-y-auto">
        {savedQueries.isLoading && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {savedQueries.data && savedQueries.data.length === 0 && (
          <div className="px-4 py-6 text-center">
            <p className="text-sm text-muted-foreground">No saved queries yet.</p>
            <p className="text-xs text-muted-foreground mt-1">
              Build a query and click Save to store it for later.
            </p>
          </div>
        )}

        {savedQueries.data && savedQueries.data.length > 0 && (
          <ul className="divide-y divide-border" data-testid="saved-queries-list">
            {savedQueries.data.map((q) => (
              <li key={q.name} className="group px-4 py-2.5 hover:bg-muted/30 transition-colors">
                {editingName === q.name ? (
                  /* Inline rename editor */
                  <div className="flex items-center gap-2" data-testid="rename-editor">
                    <input
                      ref={renameInputRef}
                      type="text"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleConfirmRename()
                        if (e.key === "Escape") handleCancelRename()
                      }}
                      className="flex-1 rounded-md border border-input bg-background px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                      data-testid="rename-input"
                    />
                    <button
                      type="button"
                      onClick={handleConfirmRename}
                      disabled={!editValue.trim() || updateQuery.isPending}
                      className="rounded-md p-1.5 text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
                      aria-label="Confirm rename"
                      data-testid="confirm-rename-btn"
                    >
                      {updateQuery.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Check className="h-3.5 w-3.5" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelRename}
                      className="rounded-md p-1.5 text-muted-foreground hover:bg-muted transition-colors"
                      aria-label="Cancel rename"
                      data-testid="cancel-rename-btn"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                    {updateQuery.isError && (
                      <p className="text-xs text-destructive-text">
                        {updateQuery.error instanceof Error ? updateQuery.error.message : "Rename failed"}
                      </p>
                    )}
                  </div>
                ) : (
                  /* Normal query row */
                  <div className="flex items-center justify-between">
                    <button
                      type="button"
                      onClick={() => onLoad(q)}
                      className="flex-1 text-left min-w-0"
                      data-testid="load-query-btn"
                    >
                      <p className="text-sm font-medium truncate">{q.name}</p>
                      {(() => {
                        const ruleCount = countRules(q.filter)
                        return (
                          <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                            <Clock className="h-3 w-3" />
                            {new Date(q.updated_at).toLocaleDateString()}
                            <span className="mx-1">&middot;</span>
                            {ruleCount} rule{ruleCount !== 1 ? "s" : ""}
                          </p>
                        )
                      })()}
                    </button>
                    <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-all">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleOverwrite(q.name)
                        }}
                        disabled={!hasRules || updateQuery.isPending}
                        className="rounded-md p-1.5 text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        aria-label={`Overwrite query "${q.name}" with current filter`}
                        title="Overwrite with current filter"
                        data-testid="overwrite-query-btn"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleStartRename(q.name)
                        }}
                        className="rounded-md p-1.5 text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
                        aria-label={`Rename query "${q.name}"`}
                        title="Rename"
                        data-testid="rename-query-btn"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDelete(q.name)
                        }}
                        className="rounded-md p-1.5 text-muted-foreground hover:text-destructive-text hover:bg-destructive/10 transition-colors"
                        aria-label={`Delete query "${q.name}"`}
                        data-testid="delete-query-btn"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Overwrite error feedback */}
      {overwriteError && (
        <div className="px-4 py-2 border-t border-border">
          <p className="text-xs text-destructive-text" data-testid="overwrite-error">{overwriteError}</p>
        </div>
      )}
    </div>
  )
}

/** Count total rules (recursively) in a filter tree. */
function countRules(filter: RuleGroupModel): number {
  let count = 0
  for (const rule of filter.rules ?? []) {
    if ("combinator" in rule) {
      count += countRules(rule as RuleGroupModel)
    } else {
      count += 1
    }
  }
  return count
}
