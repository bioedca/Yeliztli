/** React Query hooks for /api/individuals (Step 48 / IND-04; Plan §9.5).
 *
 * Covers all seven hooks: cache-key stability, list/detail success,
 * mutation invalidation semantics (create/update/delete/link/unlink),
 * 409 link-conflict body surfacing, and disabled-when-id-null behavior. */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"

import {
  individualsKeys,
  useCreateIndividual,
  useDeleteIndividual,
  useIndividual,
  useIndividuals,
  useLinkSample,
  useUnlinkSample,
  useUpdateIndividual,
} from "@/api/individuals"
import {
  IndividualsApiError,
  type IndividualDetail,
  type IndividualSummary,
} from "@/types/individuals"

const mockFetch = vi.fn()
globalThis.fetch = mockFetch as unknown as typeof fetch

function jsonResponse(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  const status = init.status ?? 200
  const ok = init.ok ?? status < 400
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    clone: function () {
      return this
    },
  } as unknown as Response
}

function makeWrapper() {
  // gcTime: Infinity — each test gets a fresh client; without this,
  // setQueryData entries with no observers are eligible for synchronous
  // garbage collection and the post-mutateAsync getQueryData assertions
  // become flaky in full-suite runs.
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return { client, Wrapper }
}

const SUMMARY: IndividualSummary = {
  id: 7,
  display_name: "Subject A",
  notes: null,
  biological_sex: "XX",
  created_at: "2026-05-01T00:00:00",
  updated_at: null,
  sample_count: 2,
  vendors: ["23andme", "ancestrydna"],
  last_activity: "2026-05-15T00:00:00",
}

const DETAIL: IndividualDetail = {
  id: 7,
  display_name: "Subject A",
  notes: null,
  biological_sex: "XX",
  created_at: "2026-05-01T00:00:00",
  updated_at: null,
  linked_samples: [
    {
      id: 11,
      name: "23andme.txt",
      file_format: "23andme_v5",
      vendor: "23andme",
      is_merged: false,
      created_at: "2026-05-01T00:00:00",
      updated_at: null,
    },
  ],
  aggregated_findings_count: 4,
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe("individualsKeys", () => {
  it("uses stable cache keys per id", () => {
    expect(individualsKeys.list()).toEqual(["individuals"])
    expect(individualsKeys.detail(7)).toEqual(["individuals", 7])
    expect(individualsKeys.detail(null)).toEqual(["individuals", null])
  })
})

describe("useIndividuals (list)", () => {
  it("GETs /api/individuals and returns the summary array", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([SUMMARY]))
    const { Wrapper } = makeWrapper()
    const { result } = renderHook(() => useIndividuals(), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockFetch).toHaveBeenCalledWith("/api/individuals")
    expect(result.current.data).toEqual([SUMMARY])
  })

  it("surfaces non-2xx responses as IndividualsApiError", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ detail: "boom" }, { status: 500, ok: false }),
    )
    const { Wrapper } = makeWrapper()
    const { result } = renderHook(() => useIndividuals(), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    const err = result.current.error as IndividualsApiError
    expect(err).toBeInstanceOf(IndividualsApiError)
    expect(err.status).toBe(500)
    expect(err.message).toBe("boom")
  })
})

describe("useIndividual (detail)", () => {
  it("GETs /api/individuals/{id} and returns the detail", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(DETAIL))
    const { Wrapper } = makeWrapper()
    const { result } = renderHook(() => useIndividual(7), { wrapper: Wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mockFetch).toHaveBeenCalledWith("/api/individuals/7")
    expect(result.current.data).toEqual(DETAIL)
  })

  it("stays disabled when id is null (no fetch, no data)", async () => {
    const { Wrapper } = makeWrapper()
    const { result } = renderHook(() => useIndividual(null), { wrapper: Wrapper })

    // Give React Query a tick to settle.
    await waitFor(() => expect(result.current.fetchStatus).toBe("idle"))
    expect(mockFetch).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })
})

describe("useCreateIndividual", () => {
  it("POSTs body, invalidates list, and seeds detail cache", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(DETAIL))
    const { client, Wrapper } = makeWrapper()
    const invalidateSpy = vi.spyOn(client, "invalidateQueries")

    const { result } = renderHook(() => useCreateIndividual(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ display_name: "Subject A" })
    })

    expect(mockFetch).toHaveBeenCalledWith("/api/individuals", expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: "Subject A" }),
    }))
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: individualsKeys.list(),
    })
    expect(client.getQueryData(individualsKeys.detail(DETAIL.id))).toEqual(DETAIL)
  })
})

describe("useUpdateIndividual", () => {
  it("PATCHes body and refreshes detail cache", async () => {
    const updated: IndividualDetail = { ...DETAIL, display_name: "Subject A (renamed)" }
    mockFetch.mockResolvedValueOnce(jsonResponse(updated))
    const { client, Wrapper } = makeWrapper()

    const { result } = renderHook(() => useUpdateIndividual(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({
        individualId: 7,
        data: { display_name: "Subject A (renamed)" },
      })
    })

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/individuals/7",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ display_name: "Subject A (renamed)" }),
      }),
    )
    expect(client.getQueryData(individualsKeys.detail(7))).toEqual(updated)
  })
})

describe("useDeleteIndividual", () => {
  it("DELETEs and invalidates list + samples; removes detail cache entry", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(null, { status: 204 }))
    const { client, Wrapper } = makeWrapper()
    client.setQueryData(individualsKeys.detail(7), DETAIL)
    const invalidateSpy = vi.spyOn(client, "invalidateQueries")
    const removeSpy = vi.spyOn(client, "removeQueries")

    const { result } = renderHook(() => useDeleteIndividual(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync(7)
    })

    expect(mockFetch).toHaveBeenCalledWith("/api/individuals/7", {
      method: "DELETE",
    })
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: individualsKeys.list(),
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["samples"] })
    expect(removeSpy).toHaveBeenCalledWith({
      queryKey: individualsKeys.detail(7),
    })
  })
})

describe("useLinkSample", () => {
  it("POSTs link-sample, invalidates list/samples, and refreshes detail cache", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(DETAIL))
    const { client, Wrapper } = makeWrapper()
    const invalidateSpy = vi.spyOn(client, "invalidateQueries")

    const { result } = renderHook(() => useLinkSample(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ individualId: 7, sampleId: 11 })
    })

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/individuals/7/link-sample",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ sample_id: 11 }),
      }),
    )
    expect(client.getQueryData(individualsKeys.detail(7))).toEqual(DETAIL)
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: individualsKeys.list(),
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["samples"] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["samples", 11] })
  })

  it("surfaces 409 link-conflict body on the IndividualsApiError", async () => {
    const conflictBody = {
      detail: {
        sample_id: 11,
        individual_id: 8,
        individual_display_name: "Subject B",
        message: "Sample 11 is already linked to individual 8.",
      },
    }
    mockFetch.mockResolvedValueOnce(
      jsonResponse(conflictBody, { status: 409, ok: false }),
    )
    const { Wrapper } = makeWrapper()
    const { result } = renderHook(() => useLinkSample(), { wrapper: Wrapper })

    let caught: unknown
    await act(async () => {
      try {
        await result.current.mutateAsync({ individualId: 7, sampleId: 11 })
      } catch (e) {
        caught = e
      }
    })

    expect(caught).toBeInstanceOf(IndividualsApiError)
    const err = caught as IndividualsApiError
    expect(err.status).toBe(409)
    expect(err.isLinkConflict()).toBe(true)
    expect(err.body).toEqual(conflictBody)
  })
})

describe("useUnlinkSample", () => {
  it("POSTs unlink-sample and refreshes detail + sample caches", async () => {
    const detail: IndividualDetail = { ...DETAIL, linked_samples: [] }
    mockFetch.mockResolvedValueOnce(jsonResponse(detail))
    const { client, Wrapper } = makeWrapper()
    const invalidateSpy = vi.spyOn(client, "invalidateQueries")

    const { result } = renderHook(() => useUnlinkSample(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ individualId: 7, sampleId: 11 })
    })

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/individuals/7/unlink-sample",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ sample_id: 11 }),
      }),
    )
    expect(client.getQueryData(individualsKeys.detail(7))).toEqual(detail)
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: individualsKeys.list(),
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["samples"] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["samples", 11] })
  })
})
