/** The API layer must send the typed multi-value contract (#2055 / #2059).
 *
 * The backend rejects a comma-joined string for `between` / `in` / `notIn`
 * with HTTP 422, so every hook that posts a filter normalises legacy string
 * values to arrays before the request leaves the browser.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useRunQuery, useSaveQuery, useExportQuery } from "@/api/query-builder"
import type { RuleGroupModel } from "@/types/query-builder"

const mockFetch = vi.fn()

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const LEGACY_FILTER: RuleGroupModel = {
  combinator: "and",
  not: false,
  rules: [
    { field: "gnomad_af_global", operator: "between", value: "0.1,0.5" },
    { field: "gene_symbol", operator: "in", value: "BRCA1,BRCA2" },
    { field: "chrom", operator: "notIn", value: "X,Y" },
  ],
}

const EXPECTED_RULES = [
  { field: "gnomad_af_global", operator: "between", value: ["0.1", "0.5"] },
  { field: "gene_symbol", operator: "in", value: ["BRCA1", "BRCA2"] },
  { field: "chrom", operator: "notIn", value: ["X", "Y"] },
]

function postedBody(url: string): Record<string, unknown> {
  const call = mockFetch.mock.calls.find(([calledUrl]) => calledUrl === url)
  expect(call, `no request to ${url}`).toBeDefined()
  return JSON.parse((call![1] as RequestInit).body as string)
}

beforeEach(() => {
  mockFetch.mockReset()
  mockFetch.mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ items: [], has_more: false, name: "q", filter: LEGACY_FILTER }),
    text: () => Promise.resolve(""),
    blob: () => Promise.resolve(new Blob()),
    headers: new Headers(),
  })
  vi.stubGlobal("fetch", mockFetch)
})

describe("query builder API hooks send array values for multi-value operators", () => {
  it("useRunQuery", async () => {
    const { result } = renderHook(() => useRunQuery(), { wrapper })
    await result.current.mutateAsync({ sampleId: 2, filter: LEGACY_FILTER })
    const body = postedBody("/api/query") as { filter: RuleGroupModel; sample_id: number }
    expect(body.sample_id).toBe(2)
    expect(body.filter.rules).toEqual(EXPECTED_RULES)
  })

  it("useSaveQuery", async () => {
    const { result } = renderHook(() => useSaveQuery(), { wrapper })
    await result.current.mutateAsync({ name: "rare-brca", filter: LEGACY_FILTER })
    const body = postedBody("/api/saved-queries") as { filter: RuleGroupModel; name: string }
    expect(body.name).toBe("rare-brca")
    expect(body.filter.rules).toEqual(EXPECTED_RULES)
  })

  it("useExportQuery", async () => {
    const { result } = renderHook(() => useExportQuery(), { wrapper })
    result.current.mutate({ sampleId: 2, filter: LEGACY_FILTER, format: "json" })
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    const body = postedBody("/api/export/query") as { filter: RuleGroupModel }
    expect(body.filter.rules).toEqual(EXPECTED_RULES)
  })
})
