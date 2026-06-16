/** SQL Console with Monaco editor (P4-04).
 *
 * Provides a Monaco-powered SQL editor with:
 * - SQL syntax highlighting
 * - Results table with column headers
 * - Schema reference sidebar ("Show schema")
 * - Read-only enforcement error display
 * - Execution time display
 * - Ctrl/Cmd+Enter to run
 */

import { useCallback, useEffect, useRef, useState } from "react"
import Editor, { type OnMount } from "@monaco-editor/react"
import {
  Play,
  Loader2,
  AlertCircle,
  Database,
  Table2,
  ChevronDown,
  ChevronRight,
  Clock,
  AlertTriangle,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { useExecuteSql, useExportSql, useSchemaInfo } from "@/api/query-builder"
import type { SqlResult, SqlExportFormat, SchemaTable } from "@/types/query-builder"
import ExportButton from "@/components/query-builder/ExportButton"

const DEFAULT_SQL = "SELECT * FROM annotated_variants LIMIT 10;"

interface SqlConsoleProps {
  sampleId: number
}

export default function SqlConsole({ sampleId }: SqlConsoleProps) {
  const [sql, setSql] = useState(DEFAULT_SQL)
  const [result, setResult] = useState<SqlResult | null>(null)
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)

  const executeSql = useExecuteSql()
  const exportSql = useExportSql()
  const schema = useSchemaInfo(sampleId)

  const handleExport = useCallback(
    (format: string) => {
      const text = editorRef.current?.getValue() ?? sql
      if (!text.trim()) return
      exportSql.mutate({
        sampleId,
        sql: text.trim(),
        format: format as SqlExportFormat,
      })
    },
    [sampleId, sql, exportSql],
  )

  const handleRun = useCallback(() => {
    const text = editorRef.current?.getValue() ?? sql
    if (!text.trim()) return
    executeSql.mutate(
      { sampleId, sql: text.trim() },
      {
        onSuccess: (data) => setResult(data),
      },
    )
  }, [sampleId, sql, executeSql])

  // Keep a stable ref to the latest handleRun to avoid stale closure in Monaco action.
  const handleRunRef = useRef(handleRun)
  useEffect(() => {
    handleRunRef.current = handleRun
  }, [handleRun])

  const handleEditorMount: OnMount = (editor, monaco) => {
    editorRef.current = editor

    // Bind Ctrl/Cmd+Enter to run query
    editor.addAction({
      id: "run-sql",
      label: "Run SQL",
      keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter],
      run: () => handleRunRef.current(),
    })
  }

  const handleInsertTable = (tableName: string) => {
    if (editorRef.current) {
      const position = editorRef.current.getPosition()
      if (position) {
        editorRef.current.executeEdits("insert-table", [
          {
            range: {
              startLineNumber: position.lineNumber,
              startColumn: position.column,
              endLineNumber: position.lineNumber,
              endColumn: position.column,
            },
            text: tableName,
          },
        ])
        editorRef.current.focus()
      }
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-6">
      {/* Main content */}
      <div className="space-y-4">
        {/* Editor */}
        <div
          className="rounded-lg border border-border overflow-hidden"
          data-testid="sql-editor-container"
        >
          <Editor
            height="240px"
            language="sql"
            value={sql}
            onChange={(v) => setSql(v ?? "")}
            onMount={handleEditorMount}
            theme="vs-dark"
            options={{
              minimap: { enabled: false },
              fontSize: 13,
              lineNumbers: "on",
              scrollBeyondLastLine: false,
              wordWrap: "on",
              automaticLayout: true,
              tabSize: 2,
              padding: { top: 8 },
              suggest: { showKeywords: true },
            }}
          />
        </div>

        {/* Action bar */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleRun}
            disabled={!sql.trim() || executeSql.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="run-sql-btn"
          >
            {executeSql.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Run SQL
          </button>
          <span className="text-xs text-muted-foreground">
            {/Mac|iPhone|iPad|iPod/.test(navigator.userAgent) ? "\u2318" : "Ctrl"}+Enter to run
          </span>
        </div>

        {/* Error */}
        {executeSql.isError && (
          <div
            className="rounded-lg border border-destructive/50 bg-destructive/5 p-4"
            data-testid="sql-error"
          >
            <div className="flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
              <div>
                <p className="font-medium text-destructive">Query failed</p>
                <p className="text-sm text-muted-foreground mt-1 font-mono whitespace-pre-wrap">
                  {executeSql.error instanceof Error
                    ? executeSql.error.message
                    : "An unexpected error occurred."}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Results */}
        {result && !executeSql.isError && (
          <SqlResultsDisplay
            result={result}
            onExport={handleExport}
            isExporting={exportSql.isPending}
          />
        )}
      </div>

      {/* Schema sidebar */}
      <aside className="space-y-4">
        <SchemaPanel
          tables={schema.data ?? []}
          isLoading={schema.isLoading}
          isError={schema.isError}
          onInsertTable={handleInsertTable}
        />
      </aside>
    </div>
  )
}

// ── Results display ──────────────────────────────────────────────────

const SQL_EXPORT_FORMATS = ["tsv", "json", "csv"] as const

function SqlResultsDisplay({
  result,
  onExport,
  isExporting,
}: {
  result: SqlResult
  onExport?: (format: string) => void
  isExporting?: boolean
}) {
  if (result.columns.length === 0 && result.row_count === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center" data-testid="sql-empty-result">
        <Database className="h-6 w-6 text-muted-foreground mx-auto mb-2" />
        <p className="text-sm text-muted-foreground">
          Query executed successfully with no results.
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          {result.execution_time_ms.toFixed(1)} ms
        </p>
      </div>
    )
  }

  return (
    <div data-testid="sql-results">
      {/* Summary bar */}
      <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border border-border rounded-t-lg">
        <p className="text-sm font-medium">
          {result.row_count.toLocaleString()} row{result.row_count !== 1 ? "s" : ""}
          {result.truncated && (
            <span className="text-amber-700 dark:text-amber-400 ml-1">
              (truncated)
            </span>
          )}
        </p>
        <div className="flex items-center gap-3">
          {onExport && (
            <ExportButton
              formats={SQL_EXPORT_FORMATS}
              onExport={onExport}
              disabled={result.row_count === 0}
              isPending={isExporting}
            />
          )}
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <Clock className="h-3 w-3" />
            {result.execution_time_ms.toFixed(1)} ms
          </div>
        </div>
      </div>

      {/* Truncation warning */}
      {result.truncated && (
        <div className="flex items-center gap-2 px-3 py-1.5 bg-amber-50 dark:bg-amber-900/20 border-x border-border text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          Results were truncated. Add a LIMIT clause to control output size.
        </div>
      )}

      {/* Table */}
      <div className="border border-t-0 border-border rounded-b-lg overflow-hidden">
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="w-full text-sm" data-testid="sql-results-table">
            <thead className="sticky top-0 z-10">
              <tr className="border-b bg-muted/50">
                {result.columns.map((col, i) => (
                  <th
                    key={`${col.name}-${i}`}
                    className="px-3 py-2 font-medium text-xs text-muted-foreground whitespace-nowrap text-left"
                  >
                    {col.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, ri) => (
                <tr
                  key={ri}
                  className="border-b border-border/50 hover:bg-muted/20 transition-colors"
                  data-testid="sql-result-row"
                >
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className={cn(
                        "px-3 py-1.5 whitespace-nowrap text-xs",
                        cell === null
                          ? "text-muted-foreground italic"
                          : "font-mono",
                      )}
                    >
                      {cell === null ? "NULL" : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Schema sidebar ───────────────────────────────────────────────────

interface SchemaPanelProps {
  tables: SchemaTable[]
  isLoading: boolean
  isError: boolean
  onInsertTable: (name: string) => void
}

function SchemaPanel({ tables, isLoading, isError, onInsertTable }: SchemaPanelProps) {
  return (
    <div className="rounded-lg border bg-card" data-testid="schema-panel">
      <div className="flex items-center gap-2 px-3 py-2 border-b">
        <Database className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-medium">Schema</h3>
      </div>

      <div className="p-2 max-h-[500px] overflow-y-auto">
        {isLoading && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {isError && (
          <p className="text-xs text-destructive p-2">Failed to load schema.</p>
        )}

        {!isLoading && !isError && tables.length === 0 && (
          <p className="text-xs text-muted-foreground p-2">No tables found.</p>
        )}

        {tables.map((table) => (
          <SchemaTableItem
            key={table.name}
            table={table}
            onInsertTable={onInsertTable}
          />
        ))}
      </div>
    </div>
  )
}

function SchemaTableItem({
  table,
  onInsertTable,
}: {
  table: SchemaTable
  onInsertTable: (name: string) => void
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="mb-1">
      {/* Sibling interactive controls — never nest a <button> inside a <button>
          (invalid HTML; serious axe `nested-interactive` violation, #607). */}
      <div className="flex items-center gap-1.5 w-full px-2 py-1 rounded text-xs hover:bg-muted/50 transition-colors">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          aria-label={`Toggle ${table.name} columns`}
          className="flex items-center gap-1.5 shrink-0 rounded hover:text-primary transition-colors"
          data-testid="schema-table-toggle"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
          )}
          <Table2 className="h-3 w-3 shrink-0 text-primary" />
        </button>
        <button
          type="button"
          className="font-mono font-medium truncate cursor-pointer hover:text-primary bg-transparent border-none p-0 text-left text-inherit text-xs"
          onClick={() => onInsertTable(table.name)}
          title={`Click to insert "${table.name}" into editor`}
        >
          {table.name}
        </button>
        <span className="text-muted-foreground ml-auto shrink-0">
          {table.columns.length}
        </span>
      </div>

      {expanded && (
        <div className="ml-5 pl-2 border-l border-border/50">
          {table.columns.map((col) => (
            <div
              key={col.name}
              className="flex items-center justify-between px-2 py-0.5 text-xs"
              data-testid="schema-column"
            >
              <span className="font-mono text-foreground truncate">{col.name}</span>
              <span className="text-muted-foreground ml-2 shrink-0 uppercase text-[10px]">
                {col.type || "—"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
