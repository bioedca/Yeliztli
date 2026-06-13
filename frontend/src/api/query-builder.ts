/** React Query hooks for the query builder API (P4-02) and SQL console (P4-04). */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import type {
  QueryMetaResponse,
  QueryResultPage,
  QueryExportFormat,
  SqlExportFormat,
  RuleGroupModel,
  SavedQuery,
  SavedQueryListResponse,
  SqlResult,
  SchemaTable,
  SchemaColumn,
} from "@/types/query-builder"

// ── Field metadata ──────────────────────────────────────────────────

export function useQueryFields() {
  return useQuery({
    queryKey: ["query-fields"],
    queryFn: async (): Promise<QueryMetaResponse> => {
      const res = await fetch("/api/query/fields")
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Failed to fetch query fields: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
    staleTime: Infinity,
  })
}

// ── Execute query ───────────────────────────────────────────────────

interface ExecuteQueryParams {
  sampleId: number
  filter: RuleGroupModel
}

const QUERY_PAGE_SIZE = 50

/** One-shot mutation for executing a query (used for the Run button). */
export function useRunQuery() {
  return useMutation({
    mutationFn: async ({ sampleId, filter }: ExecuteQueryParams): Promise<QueryResultPage> => {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sample_id: sampleId,
          filter,
          limit: QUERY_PAGE_SIZE,
        }),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Query execution failed: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      return res.json()
    },
  })
}

// ── Saved queries ───────────────────────────────────────────────────

export function useSavedQueries() {
  return useQuery({
    queryKey: ["saved-queries"],
    queryFn: async (): Promise<SavedQuery[]> => {
      const res = await fetch("/api/saved-queries")
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(`Failed to fetch saved queries: ${res.status}${text ? ` - ${text}` : ""}`)
      }
      const data: SavedQueryListResponse = await res.json()
      return data.queries
    },
    staleTime: 1000 * 60 * 5, // 5 min
  })
}

export function useSaveQuery() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: { name: string; filter: RuleGroupModel }): Promise<SavedQuery> => {
      const res = await fetch("/api/saved-queries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(text || "Failed to save query")
      }
      return res.json()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-queries"] }),
  })
}

export function useUpdateSavedQuery() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      name,
      ...body
    }: {
      name: string
      new_name?: string
      filter?: RuleGroupModel
    }): Promise<SavedQuery> => {
      const res = await fetch(`/api/saved-queries/${encodeURIComponent(name)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(text || "Failed to update saved query")
      }
      return res.json()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-queries"] }),
  })
}

export function useDeleteSavedQuery() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string) => {
      const res = await fetch(`/api/saved-queries/${encodeURIComponent(name)}`, {
        method: "DELETE",
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(text || "Failed to delete saved query")
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-queries"] }),
  })
}

// ── SQL Console (P4-04) ─────────────────────────────────────────────

/** Execute raw SQL against a read-only SQLite connection. */
export function useExecuteSql() {
  return useMutation({
    mutationFn: async ({
      sampleId,
      sql,
      limit,
    }: {
      sampleId: number
      sql: string
      limit?: number
    }): Promise<SqlResult> => {
      const res = await fetch("/api/query/sql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: sampleId, sql, limit }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        const detail = data?.detail || `SQL execution failed: ${res.status}`
        throw new Error(detail)
      }
      return res.json()
    },
  })
}

/** Fetch schema (tables + columns) for a sample database. */
export function useSchemaInfo(sampleId: number | null) {
  return useQuery({
    queryKey: ["sql-schema", sampleId],
    queryFn: async (): Promise<SchemaTable[]> => {
      // Step 1: Get table names
      const tablesRes = await fetch("/api/query/sql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sample_id: sampleId!,
          sql: "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
          limit: 100,
        }),
      })
      if (!tablesRes.ok) throw new Error("Failed to fetch schema")
      const tablesData: SqlResult = await tablesRes.json()
      const tableNames = tablesData.rows.map((r) => String(r[0]))

      // Step 2: Get columns for each table
      const tables: SchemaTable[] = []
      for (const tableName of tableNames) {
        // Escape double quotes in table name to prevent SQL injection
        const safeName = tableName.replace(/"/g, '""')
        const colRes = await fetch("/api/query/sql", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sample_id: sampleId!,
            sql: `PRAGMA table_info("${safeName}")`,
            limit: 200,
          }),
        })
        if (!colRes.ok) continue
        const colData: SqlResult = await colRes.json()
        const columns: SchemaColumn[] = colData.rows.map((r) => ({
          name: String(r[1]),
          type: String(r[2] || ""),
        }))
        tables.push({ name: tableName, columns })
      }

      return tables
    },
    enabled: sampleId != null,
    staleTime: 1000 * 60 * 60, // 1 hour
  })
}

// ── Export (P4-05) ───────────────────────────────────────────────────

/** Trigger a browser download from a fetch response blob. */
function triggerDownload(blob: Blob, disposition: string, fallbackName: string) {
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/)
  const filename = filenameMatch?.[1] || fallbackName
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** Export query builder results as a downloadable file. */
export function useExportQuery() {
  return useMutation({
    mutationFn: async ({
      sampleId,
      filter,
      format,
    }: {
      sampleId: number
      filter: RuleGroupModel
      format: QueryExportFormat
    }) => {
      const res = await fetch("/api/export/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: sampleId, filter, format }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        throw new Error(data?.detail || `Export failed: ${res.status}`)
      }
      const blob = await res.blob()
      const disposition = res.headers.get("content-disposition") || ""
      triggerDownload(blob, disposition, `export.${format}`)
    },
  })
}

/** Export SQL console results as a downloadable file. */
export function useExportSql() {
  return useMutation({
    mutationFn: async ({
      sampleId,
      sql,
      format,
    }: {
      sampleId: number
      sql: string
      format: SqlExportFormat
    }) => {
      const res = await fetch("/api/export/sql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample_id: sampleId, sql, format }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        throw new Error(data?.detail || `Export failed: ${res.status}`)
      }
      const blob = await res.blob()
      const disposition = res.headers.get("content-disposition") || ""
      triggerDownload(blob, disposition, `export.${format}`)
    },
  })
}
