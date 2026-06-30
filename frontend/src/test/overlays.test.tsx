/** Tests for the Annotation Overlays page (P4-12).
 *
 * Covers:
 * - No sample selected -> empty state
 * - Overlay list rendering
 * - Upload flow (file selection, preview, save)
 * - Apply overlay action
 * - Results table rendering
 * - Delete overlay
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render as rtlRender, screen, waitFor, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import OverlaysView from "@/pages/OverlaysView"
import type { ReactElement, ReactNode } from "react"

// ── Custom render ──────────────────────────────────────────────────

function renderWithRoute(ui: ReactElement, initialEntries: string[] = ["/"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return rtlRender(ui, { wrapper: Wrapper })
}

// ── Mock data ──────────────────────────────────────────────────────

const MOCK_OVERLAYS = {
  items: [
    {
      id: 1,
      name: "ClinVar Custom",
      description: "Custom ClinVar annotations",
      file_type: "vcf",
      column_names: ["CUSTOM_AF", "CUSTOM_SIG"],
      region_count: 100,
      created_at: "2026-03-23T10:00:00",
    },
    {
      id: 2,
      name: "Regulatory Regions",
      description: "ENCODE cCREs BED overlay",
      file_type: "bed",
      column_names: ["region_type", "score"],
      region_count: 500,
      created_at: "2026-03-22T10:00:00",
    },
  ],
  total: 2,
}

const MOCK_RESULTS = {
  overlay_id: 1,
  overlay_name: "ClinVar Custom",
  results: [
    { rsid: "rs12345", overlay_id: 1, CUSTOM_AF: 0.03, CUSTOM_SIG: "pathogenic" },
    { rsid: "rs429358", overlay_id: 1, CUSTOM_AF: 0.15, CUSTOM_SIG: "risk_factor" },
  ],
  total: 2,
}

const originalFetch = globalThis.fetch
const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  globalThis.fetch = mockFetch
})

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.restoreAllMocks()
})

function mockOverlaysFetch() {
  mockFetch.mockImplementation(async (url: string) => {
    if (typeof url === "string" && url === "/api/overlays") {
      return {
        ok: true,
        json: async () => MOCK_OVERLAYS,
        text: async () => JSON.stringify(MOCK_OVERLAYS),
      }
    }
    if (typeof url === "string" && url.includes("/api/overlays/1/results")) {
      return {
        ok: true,
        json: async () => MOCK_RESULTS,
        text: async () => JSON.stringify(MOCK_RESULTS),
      }
    }
    return { ok: false, status: 404, text: async () => "Not found" }
  })
}

// ── Tests ──────────────────────────────────────────────────────────

describe("OverlaysView", () => {
  it("shows empty state when no sample selected", () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays"])
    expect(
      screen.getByText("Select a sample from the top nav to manage annotation overlays.")
    ).toBeInTheDocument()
  })

  it("renders overlay list when sample is selected", async () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("ClinVar Custom")).toBeInTheDocument()
    })
    expect(screen.getByText("Regulatory Regions")).toBeInTheDocument()
  })

  it("shows upload panel with title", () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    expect(screen.getByText("Upload Overlay File")).toBeInTheDocument()
  })

  it("file picker advertises only plain-text .bed/.vcf (no .vcf.gz — backend has no gzip path, #1299)", () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    const input = document.querySelector('input[type="file"]') as HTMLInputElement | null
    expect(input).not.toBeNull()
    // Must match the backend, which decodes uploads as UTF-8 text and cannot
    // parse gzip — advertising .vcf.gz produced a confusing 400 on upload.
    expect(input?.accept).toBe(".bed,.vcf")
  })

  it("rejects an unsupported dropped file (.vcf.gz) client-side, not via a backend 400 (#1299)", () => {
    // The picker `accept` does not constrain drag-and-drop, so the common
    // handler must reject unsupported extensions before any upload.
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    const dropZone = screen
      .getByText("Drop a BED or VCF file here, or click to browse")
      .closest("button") as HTMLElement
    const fetchCallsBeforeDrop = mockFetch.mock.calls.length
    const gzFile = new File(["x"], "track.vcf.gz", { type: "application/gzip" })
    fireEvent.drop(dropZone, { dataTransfer: { files: [gzFile] } })
    expect(
      screen.getByText("Only plain-text .bed and .vcf files are supported.")
    ).toBeInTheDocument()
    // Rejected before any network call — no preview/upload request is issued.
    expect(mockFetch.mock.calls).toHaveLength(fetchCallsBeforeDrop)
    // The file is not accepted for upload.
    expect(screen.queryByText(/Selected: track\.vcf\.gz/)).not.toBeInTheDocument()
  })

  it("shows page heading", () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    expect(screen.getByText("Annotation Overlays")).toBeInTheDocument()
  })

  it("renders empty overlay list message when no overlays", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url === "/api/overlays") {
        return {
          ok: true,
          json: async () => ({ items: [], total: 0 }),
          text: async () => JSON.stringify({ items: [], total: 0 }),
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])

    await waitFor(() => {
      expect(
        screen.getByText("No overlays uploaded yet. Upload a BED or VCF file above to get started.")
      ).toBeInTheDocument()
    })
  })

  it("shows overlay file type badges", async () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("VCF")).toBeInTheDocument()
      expect(screen.getByText("BED")).toBeInTheDocument()
    })
  })

  it("shows drop zone text", () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    expect(
      screen.getByText("Drop a BED or VCF file here, or click to browse")
    ).toBeInTheDocument()
  })

  it("applies an overlay and renders the results table", async () => {
    const applyResponse = {
      overlay_id: 1,
      overlay_name: "ClinVar Custom",
      variants_matched: 2,
      records_checked: 100,
    }
    mockFetch.mockImplementation(async (url: string, opts?: { method?: string }) => {
      if (url === "/api/overlays") {
        return { ok: true, json: async () => MOCK_OVERLAYS, text: async () => "" }
      }
      if (url.includes("/api/overlays/1/apply") && opts?.method === "POST") {
        return { ok: true, json: async () => applyResponse, text: async () => "" }
      }
      if (url.includes("/api/overlays/1/results")) {
        return { ok: true, json: async () => MOCK_RESULTS, text: async () => "" }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    await waitFor(() => expect(screen.getByText("ClinVar Custom")).toBeInTheDocument())

    fireEvent.click(screen.getAllByRole("button", { name: "Apply" })[0])

    // The applied overlay's results table renders the matched annotations.
    await waitFor(() => expect(screen.getByText("rs12345")).toBeInTheDocument())
    expect(screen.getByText("rs429358")).toBeInTheDocument()
    expect(screen.getByText("pathogenic")).toBeInTheDocument()
    expect(screen.getByText("risk_factor")).toBeInTheDocument()
  })

  it("deletes an overlay after confirmation", async () => {
    // jsdom has no window.confirm, so install a stub (not a spy).
    const originalConfirm = window.confirm
    const confirmFn = vi.fn(() => true)
    window.confirm = confirmFn
    let deleteMethod: string | undefined
    mockFetch.mockImplementation(async (url: string, opts?: { method?: string }) => {
      if (url === "/api/overlays") {
        return { ok: true, json: async () => MOCK_OVERLAYS, text: async () => "" }
      }
      if (url === "/api/overlays/1" && opts?.method === "DELETE") {
        deleteMethod = opts?.method
        return { ok: true, json: async () => ({}), text: async () => "" }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    await waitFor(() => expect(screen.getByText("ClinVar Custom")).toBeInTheDocument())

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0])

    // Delete must prompt for confirmation and then issue the DELETE request.
    expect(confirmFn).toHaveBeenCalledWith('Delete overlay "ClinVar Custom"?')
    await waitFor(() => expect(deleteMethod).toBe("DELETE"))
    window.confirm = originalConfirm
  })
})
