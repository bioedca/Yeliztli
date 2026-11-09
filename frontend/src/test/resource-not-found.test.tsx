/** Real API hooks must preserve status and safe copy through page rendering (#2433). */

import { afterEach, describe, expect, it, vi } from "vitest"
import { Route, Routes } from "react-router-dom"
import { fireEvent, render, screen, waitFor, within } from "./test-utils"
import PharmacogenomicsView from "@/pages/PharmacogenomicsView"
import VariantDetailPage from "@/pages/VariantDetailPage"
import ConcordanceReport from "@/pages/ConcordanceReport"
import IndividualDetail from "@/pages/IndividualDetail"

vi.mock("@/components/igv-browser", () => ({ IgvBrowser: () => null }))
vi.mock("@/components/igv-browser/tracks", () => ({ buildDefaultTracks: () => [] }))

const PRIVATE_BODY = "server-only diagnostic must stay hidden"
const NOT_FOUND = "The requested data wasn't found. Check the link or select another sample."

const PAGES = [
  {
    name: "individual",
    route: "/individuals/99999",
    path: "/individuals/:id",
    endpoint: "/api/individuals/99999",
    element: <IndividualDetail />,
  },
  {
    name: "pharmacogenomics sample",
    route: "/pharmacogenomics?sample_id=99999",
    path: "/pharmacogenomics",
    endpoint: "/api/analysis/pharma/genes?sample_id=99999",
    element: <PharmacogenomicsView />,
  },
  {
    name: "variant detail",
    route: "/variants/rs00000000?sample_id=2",
    path: "/variants/:rsid",
    endpoint: "/api/variants/rs00000000?sample_id=2",
    element: <VariantDetailPage />,
  },
  {
    name: "concordance sample",
    route: "/samples/99999/concordance",
    path: "/samples/:id/concordance",
    endpoint: "/api/samples/99999/merge-provenance",
    element: <ConcordanceReport />,
  },
]

afterEach(() => vi.unstubAllGlobals())

describe.each(PAGES)("missing $name", ({ route, path, endpoint, element }) => {
  function mockFailure(status: number | "network") {
    const fetch = vi.fn(async (url: string) => {
      if (url === endpoint) {
        if (status === "network") throw new TypeError("Network unavailable")
        return new Response(JSON.stringify({ detail: PRIVATE_BODY }), { status })
      }
      if (url === "/api/analysis/pharma/drugs") {
        return new Response(JSON.stringify({ items: [], total: 0 }))
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetch)
    render(<Routes><Route path={path} element={element} /></Routes>, { route })
    return fetch
  }

  it("shows a fixed not-found message without retrying or revealing the body", async () => {
    const fetch = mockFailure(404)
    const alert = await screen.findByRole("alert")
    expect(within(alert).getByText("Data not found")).toBeInTheDocument()
    expect(within(alert).getByText(NOT_FOUND)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
    expect(screen.queryByText(/Please try again/)).not.toBeInTheDocument()
    expect(screen.queryByText(PRIVATE_BODY)).not.toBeInTheDocument()
    expect(fetch.mock.calls.filter(([url]) => url === endpoint)).toHaveLength(1)
    expect(screen.queryByText(/No merge provenance for this sample/)).not.toBeInTheDocument()
    if (path === "/variants/:rsid") {
      expect(screen.getByRole("link", { name: /Back to Variant Explorer/ }))
        .toHaveAttribute("href", "/variants?sample_id=2")
    }
  })

  it.each([500, "network"] as const)("keeps a working Retry for %s failures", async (status) => {
    const fetch = mockFailure(status)
    const retry = await screen.findByRole("button", { name: "Retry" })
    expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    expect(screen.queryByText(NOT_FOUND)).not.toBeInTheDocument()
    expect(screen.queryByText(PRIVATE_BODY)).not.toBeInTheDocument()
    expect(fetch.mock.calls.filter(([url]) => url === endpoint)).toHaveLength(1)
    fireEvent.click(retry)
    await waitFor(() => {
      expect(fetch.mock.calls.filter(([url]) => url === endpoint)).toHaveLength(2)
    })
  })
})
