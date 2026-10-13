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

  it("file picker advertises .bed, .vcf, and bounded .vcf.gz support (#1364)", () => {
    mockOverlaysFetch()
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    const input = document.querySelector('input[type="file"]') as HTMLInputElement | null
    expect(input).not.toBeNull()
    expect(input?.accept).toBe(".bed,.vcf,.vcf.gz")
  })

  it("accepts a dropped .vcf.gz and sends it to preview (#1364)", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url === "/api/overlays") {
        return {
          ok: true,
          json: async () => MOCK_OVERLAYS,
          text: async () => JSON.stringify(MOCK_OVERLAYS),
        }
      }
      if (url === "/api/overlays/parse") {
        return {
          ok: true,
          json: async () => ({
            file_type: "vcf",
            column_names: ["AF"],
            record_count: 1,
            warnings: [],
          }),
          text: async () => "",
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    const dropZone = screen
      .getByText("Drop a BED, VCF, or gzipped VCF file here, or click to browse")
      .closest("button") as HTMLElement
    const fetchCallsBeforeDrop = mockFetch.mock.calls.length
    const gzFile = new File(["x"], "track.vcf.gz", { type: "application/gzip" })
    fireEvent.drop(dropZone, { dataTransfer: { files: [gzFile] } })

    expect(screen.getByText(/Selected: track\.vcf\.gz/)).toBeInTheDocument()
    await waitFor(() => expect(mockFetch.mock.calls.length).toBe(fetchCallsBeforeDrop + 1))
    expect(mockFetch).toHaveBeenLastCalledWith(
      "/api/overlays/parse",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) })
    )
    const previewCall = mockFetch.mock.calls.at(-1)
    const previewOptions = previewCall?.[1] as RequestInit | undefined
    const sentFile = (previewOptions?.body as FormData).get("file")
    expect(sentFile).toBeInstanceOf(File)
    expect((sentFile as File).name).toBe("track.vcf.gz")
    expect((sentFile as File).type).toBe("application/gzip")
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
        screen.getByText(
          "No overlays uploaded yet. Upload a BED, VCF, or gzipped VCF file above to get started."
        )
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
      screen.getByText("Drop a BED, VCF, or gzipped VCF file here, or click to browse")
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

// ── Upload gated on the parse preview (#2098) ──────────────────────

const PREVIEW_OK = { file_type: "bed", column_names: ["score"], record_count: 3, warnings: [] }

type PreviewReply = "ok" | "fail" | "pending"

/** Route every overlay request; `previews` answers successive parse calls in order. */
function mockUploadFlow(previews: PreviewReply[], uploadOk = true) {
  const uploads: string[] = []
  const queue = [...previews]
  mockFetch.mockImplementation(async (url: string) => {
    if (url === "/api/overlays") {
      return { ok: true, json: async () => ({ items: [], total: 0 }), text: async () => "" }
    }
    if (url === "/api/overlays/parse") {
      const reply = queue.shift() ?? "ok"
      if (reply === "pending") return new Promise(() => {})
      if (reply === "fail") {
        return {
          ok: false,
          status: 422,
          text: async () => JSON.stringify({ detail: "No valid BED records found" }),
        }
      }
      return { ok: true, json: async () => PREVIEW_OK, text: async () => "" }
    }
    if (url.startsWith("/api/overlays/upload")) {
      uploads.push(url)
      if (!uploadOk) {
        return { ok: false, status: 500, text: async () => JSON.stringify({ detail: "disk full" }) }
      }
      return {
        ok: true,
        json: async () => ({ ...MOCK_OVERLAYS.items[1], id: 9, name: "good" }),
        text: async () => "",
      }
    }
    return { ok: false, status: 404, text: async () => "Not found" }
  })
  return uploads
}

function dropFile(name: string) {
  const dropZone = screen
    .getAllByRole("button")
    .find((b) => b.textContent?.includes("click to browse") || b.textContent?.includes("Selected:"))
  expect(dropZone).toBeDefined()
  fireEvent.drop(dropZone as HTMLElement, {
    dataTransfer: { files: [new File(["chr1\tABC\t100\n"], name, { type: "text/plain" })] },
  })
}

const uploadButton = () => screen.getByRole("button", { name: /Upload & Save/ })

describe("OverlaysView upload is gated on the parse preview (#2098)", () => {
  it("keeps Upload & Save disabled, with a visible reason, when the preview fails", async () => {
    const uploads = mockUploadFlow(["fail"])
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    dropFile("malformed.bed")

    await waitFor(() => expect(screen.getByText(/Parse failed/)).toBeInTheDocument())
    // The name auto-fills, so the name is not what blocks the submit.
    expect(screen.getByLabelText("Name")).toHaveValue("malformed")
    expect(uploadButton()).toBeDisabled()
    const reason = screen.getByText(/could not be read/)
    expect(uploadButton()).toHaveAttribute("aria-describedby", reason.id)

    fireEvent.click(uploadButton())
    expect(uploads).toHaveLength(0)
  })

  it("keeps Upload & Save disabled while the preview is still running", async () => {
    const uploads = mockUploadFlow(["pending"])
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    dropFile("slow.bed")

    await waitFor(() => expect(screen.getByText(/Checking the file/)).toBeInTheDocument())
    expect(uploadButton()).toBeDisabled()
    fireEvent.click(uploadButton())
    expect(uploads).toHaveLength(0)
  })

  it("enables Upload & Save once the preview succeeds, and uploads", async () => {
    const uploads = mockUploadFlow(["ok"])
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    dropFile("good.bed")

    await waitFor(() => expect(screen.getByText("Format:")).toBeInTheDocument())
    expect(uploadButton()).toBeEnabled()
    expect(uploadButton()).not.toHaveAttribute("aria-describedby")
    expect(screen.queryByText(/could not be read/)).not.toBeInTheDocument()

    fireEvent.click(uploadButton())
    await waitFor(() => expect(uploads).toHaveLength(1))
    expect(uploads[0]).toContain("name=good")
  })

  it("re-enables Upload & Save when a valid file replaces a rejected one", async () => {
    mockUploadFlow(["fail", "ok"])
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    dropFile("malformed.bed")
    await waitFor(() => expect(uploadButton()).toBeDisabled())
    await waitFor(() => expect(screen.getByText(/could not be read/)).toBeInTheDocument())

    dropFile("fixed.bed")
    await waitFor(() => expect(uploadButton()).toBeEnabled())
    expect(screen.queryByText(/could not be read/)).not.toBeInTheDocument()
  })

  it("clears a previous upload failure when another file is chosen", async () => {
    mockUploadFlow(["ok", "ok"], false)
    renderWithRoute(<OverlaysView />, ["/overlays?sample_id=1"])
    dropFile("first.bed")
    await waitFor(() => expect(uploadButton()).toBeEnabled())
    fireEvent.click(uploadButton())
    await waitFor(() => expect(screen.getByText(/Upload failed/)).toBeInTheDocument())

    dropFile("second.bed")
    await waitFor(() => expect(screen.getByText(/Selected: second\.bed/)).toBeInTheDocument())
    expect(screen.queryByText(/Upload failed/)).not.toBeInTheDocument()
  })
})
