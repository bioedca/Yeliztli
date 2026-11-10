/** Complete page cursors must survive the Explorer's API hook (#2449). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useVariants } from "@/api/variants"

const mockFetch = vi.fn()

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

function page(rsid: string, chrom: string, pos: number, hasMore: boolean) {
  return {
    items: [{ rsid, chrom, pos, genotype: "--" }],
    next_cursor_chrom: hasMore ? chrom : null,
    next_cursor_pos: hasMore ? pos : null,
    next_cursor_rsid: hasMore ? rsid : null,
    has_more: hasMore,
    limit: 100,
  }
}

function respondWith(...pages: ReturnType<typeof page>[]) {
  for (const body of pages) {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => body })
  }
}

function requestParams(index: number) {
  const url = new URL(mockFetch.mock.calls[index][0], "http://localhost")
  expect(url.pathname).toBe("/api/variants")
  return Object.fromEntries(url.searchParams)
}

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
})

afterEach(() => vi.unstubAllGlobals())

describe("Variant Explorer complete cursor transport", () => {
  it("forwards distinct rsIDs at the same position and preserves request filters", async () => {
    const first = "iYELIZTLI_A+opaque&1"
    const second = "rsYELIZTLI_B"
    respondWith(
      page(first, "1", 20, true),
      page(second, "1", 20, true),
      page("rsYELIZTLI_END", "2", 1, false),
    )
    const { result } = renderHook(
      () => useVariants({ sampleId: 7, filter: "chrom:1", tag: "synthetic", search: " YELIZTLI " }),
      { wrapper: makeWrapper() },
    )
    await waitFor(() => expect(result.current.data?.pages[0].items[0].rsid).toBe(first))
    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2))
    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.hasNextPage).toBe(false))

    const common = {
      sample_id: "7",
      limit: "100",
      filter: "chrom:1,annotation_coverage:notnull",
      tag: "synthetic",
      search: "YELIZTLI",
    }
    expect(requestParams(0)).toEqual(common)
    expect(requestParams(1)).toEqual({ ...common, cursor_chrom: "1", cursor_pos: "20", cursor_rsid: first })
    expect(requestParams(2)).toEqual({ ...common, cursor_chrom: "1", cursor_pos: "20", cursor_rsid: second })
    expect(result.current.data?.pages.flatMap((p) => p.items.map((item) => item.rsid))).toEqual([
      first, second, "rsYELIZTLI_END",
    ])
    expect(mockFetch).toHaveBeenCalledTimes(3)
  })

  it("starts a chromosome jump with an inclusive seek, then sends the returned complete cursor", async () => {
    respondWith(page("iYELIZTLI_X", "X", 5, true), page("rsYELIZTLI_X", "X", 5, false))
    const { result } = renderHook(() => useVariants({ sampleId: 8, startChrom: "X" }), {
      wrapper: makeWrapper(),
    })
    await waitFor(() => expect(result.current.data?.pages[0].items[0].rsid).toBe("iYELIZTLI_X"))
    expect(requestParams(0)).toEqual({
      sample_id: "8", limit: "100", filter: "annotation_coverage:notnull",
      cursor_chrom: "X", cursor_pos: "0",
    })

    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2))
    expect(requestParams(1)).toEqual({
      sample_id: "8", limit: "100", filter: "annotation_coverage:notnull",
      cursor_chrom: "X", cursor_pos: "5", cursor_rsid: "iYELIZTLI_X",
    })
    expect(result.current.hasNextPage).toBe(false)
    expect(result.current.data?.pages.flatMap((p) => p.items.map((item) => item.rsid))).toEqual([
      "iYELIZTLI_X", "rsYELIZTLI_X",
    ])
  })
})
