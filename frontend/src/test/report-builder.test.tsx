/** Tests for the Report Builder UI (P4-10).
 *
 * Covers:
 * - No sample selected → empty state
 * - Loading state
 * - Module selection (toggle, select all, clear all)
 * - Preview triggers API call and shows modal
 * - Download triggers API call and blob download
 * - Error handling for generation failures
 * - Report summary panel updates with selection
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render as rtlRender, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import ReportBuilder from "@/pages/ReportBuilder"
import type { FindingsSummaryResponse } from "@/types/findings"
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

const MOCK_SUMMARY: FindingsSummaryResponse = {
  total_findings: 12,
  modules: [
    { module: "cancer", count: 3, max_evidence_level: 4, top_finding_text: "BRCA1 pathogenic variant" },
    { module: "pharmacogenomics", count: 5, max_evidence_level: 4, top_finding_text: "CYP2C19 poor metabolizer" },
    { module: "nutrigenomics", count: 4, max_evidence_level: 2, top_finding_text: "Vitamin D metabolism" },
  ],
  high_confidence_findings: [],
}

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

function mockSummaryFetch() {
  mockFetch.mockImplementation(async (url: string) => {
    if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
      return {
        ok: true,
        json: async () => MOCK_SUMMARY,
        text: async () => JSON.stringify(MOCK_SUMMARY),
      }
    }
    return { ok: false, status: 404, text: async () => "Not found" }
  })
}

// ── Tests ──────────────────────────────────────────────────────────

describe("ReportBuilder", () => {
  it("shows empty state when no sample selected", () => {
    renderWithRoute(<ReportBuilder />, ["/reports"])
    expect(screen.getByText("Select a sample to build a report.")).toBeInTheDocument()
  })

  it("shows loading state while fetching", () => {
    mockFetch.mockImplementation(() => new Promise(() => {})) // never resolves
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    expect(screen.getByText("Loading findings...")).toBeInTheDocument()
  })

  it("renders module selection cards after loading", async () => {
    mockSummaryFetch()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    expect(screen.getByText("Pharmacogenomics")).toBeInTheDocument()
    expect(screen.getByText("Nutrigenomics")).toBeInTheDocument()
  })

  it("auto-selects all modules on load", async () => {
    mockSummaryFetch()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    // All 3 modules selected → summary shows 3
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.getByText("12")).toBeInTheDocument() // total findings
  })

  it("toggles module selection on click", async () => {
    mockSummaryFetch()
    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    // Deselect cancer
    const cancerBtn = screen.getByLabelText("Cancer Predisposition: 3 findings")
    await user.click(cancerBtn)

    // Selected count should now be 2, total findings 9
    await waitFor(() => {
      expect(screen.getByText("2")).toBeInTheDocument()
      expect(screen.getByText("9")).toBeInTheDocument()
    })
  })

  it("clears all modules on 'Clear all' click", async () => {
    mockSummaryFetch()
    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByText("Clear all"))

    // Download button should be disabled
    const downloadBtn = screen.getByLabelText("Download PDF report")
    expect(downloadBtn).toBeDisabled()
  })

  it("selects all modules on 'Select all' click", async () => {
    mockSummaryFetch()
    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByText("Clear all"))
    await user.click(screen.getByText("Select all"))

    // Download button should be enabled
    const downloadBtn = screen.getByLabelText("Download PDF report")
    expect(downloadBtn).not.toBeDisabled()
  })

  it("calls preview API and shows modal", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return { ok: true, json: async () => MOCK_SUMMARY, text: async () => JSON.stringify(MOCK_SUMMARY) }
      }
      if (typeof url === "string" && url.includes("/api/reports/preview")) {
        return { ok: true, text: async () => "<html><body>Report Preview</body></html>" }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByLabelText("Preview report"))

    await waitFor(() => {
      expect(screen.getByText("Report Preview")).toBeInTheDocument()
    })

    // Modal should have close button
    expect(screen.getByLabelText("Close preview")).toBeInTheDocument()
  })

  it("closes preview modal on close button click", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return { ok: true, json: async () => MOCK_SUMMARY, text: async () => JSON.stringify(MOCK_SUMMARY) }
      }
      if (typeof url === "string" && url.includes("/api/reports/preview")) {
        return { ok: true, text: async () => "<html><body>Preview Content</body></html>" }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByLabelText("Preview report"))

    await waitFor(() => {
      expect(screen.getByLabelText("Close preview")).toBeInTheDocument()
    })

    await user.click(screen.getByLabelText("Close preview"))

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    })
  })

  it("triggers PDF download on download button click", async () => {
    const mockBlob = new Blob(["pdf content"], { type: "application/pdf" })
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return { ok: true, json: async () => MOCK_SUMMARY, text: async () => JSON.stringify(MOCK_SUMMARY) }
      }
      if (typeof url === "string" && url.includes("/api/reports/generate")) {
        return { ok: true, blob: async () => mockBlob }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    // Mock URL.createObjectURL and revokeObjectURL
    const mockUrl = "blob:test-url"
    const createObjectURL = vi.fn(() => mockUrl)
    const revokeObjectURL = vi.fn()
    globalThis.URL.createObjectURL = createObjectURL
    globalThis.URL.revokeObjectURL = revokeObjectURL

    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByLabelText("Download PDF report"))

    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalledWith(mockBlob)
    })
  })

  it("shows error when PDF generation fails", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return { ok: true, json: async () => MOCK_SUMMARY, text: async () => JSON.stringify(MOCK_SUMMARY) }
      }
      if (typeof url === "string" && url.includes("/api/reports/generate")) {
        return { ok: false, status: 503, text: async () => "Playwright not installed" }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByLabelText("Download PDF report"))

    await waitFor(() => {
      expect(screen.getByText(/Report generation failed/)).toBeInTheDocument()
    })
  })

  it("shows report title input with default value", async () => {
    mockSummaryFetch()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    const titleInput = screen.getByLabelText("Report Title") as HTMLInputElement
    expect(titleInput.value).toBe("Yeliztli Genomic Report")
  })

  it("shows no findings empty state when sample has no findings", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return {
          ok: true,
          json: async () => ({ total_findings: 0, modules: [], high_confidence_findings: [] }),
          text: async () => JSON.stringify({ total_findings: 0, modules: [], high_confidence_findings: [] }),
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText(/No analysis findings available/)).toBeInTheDocument()
    })
  })

  it("displays evidence stars for modules", async () => {
    mockSummaryFetch()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    // Should show evidence stars (4-star for cancer)
    const stars = screen.getAllByRole("img", { name: /stars evidence/ })
    expect(stars.length).toBeGreaterThan(0)
  })

  it("displays finding counts per module", async () => {
    mockSummaryFetch()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    expect(screen.getByText("3 findings")).toBeInTheDocument()
    expect(screen.getByText("5 findings")).toBeInTheDocument()
    expect(screen.getByText("4 findings")).toBeInTheDocument()
  })

  it("disables preview and download buttons when no modules selected", async () => {
    mockSummaryFetch()
    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByText("Clear all"))

    expect(screen.getByLabelText("Preview report")).toBeDisabled()
    expect(screen.getByLabelText("Download PDF report")).toBeDisabled()
  })
})

// ── Module completeness / drift-proofing (#596) ─────────────────────

const COMPLETE_SUMMARY: FindingsSummaryResponse = {
  total_findings: 10,
  modules: [
    { module: "cancer", count: 2, max_evidence_level: 4, top_finding_text: "BRCA1 pathogenic variant" },
    // module value the backend actually writes is "carrier" (not "carrier_status")
    { module: "carrier", count: 3, max_evidence_level: 3, top_finding_text: "CFTR carrier" },
    { module: "metabolic", count: 1, max_evidence_level: 2, top_finding_text: "T2D risk" },
    { module: "fh", count: 1, max_evidence_level: 3, top_finding_text: "FH variant" },
    { module: "ebmd", count: 1, max_evidence_level: 2, top_finding_text: "Low BMD" },
    // an unknown / risk-panel module not in MODULE_ORDER or the display-name map
    { module: "gout", count: 2, max_evidence_level: 2, top_finding_text: "Gout risk" },
  ],
  high_confidence_findings: [],
}

function mockCompleteSummary() {
  mockFetch.mockImplementation(async (url: string) => {
    if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
      return {
        ok: true,
        json: async () => COMPLETE_SUMMARY,
        text: async () => JSON.stringify(COMPLETE_SUMMARY),
      }
    }
    return { ok: false, status: 404, text: async () => "Not found" }
  })
}

describe("ReportBuilder — module completeness (#596)", () => {
  it("offers Carrier Status (module='carrier', not 'carrier_status')", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    // Pre-#596 the "carrier_status" key never matched the "carrier" summary key,
    // so Carrier Status was never selectable.
    expect(screen.getByText("Carrier Status")).toBeInTheDocument()
  })

  it("offers Metabolic, FH and eBMD modules (previously absent from MODULE_ORDER)", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    expect(screen.getByText("Metabolic (T2D & Obesity)")).toBeInTheDocument()
    expect(screen.getByText("Familial Hypercholesterolemia")).toBeInTheDocument()
    expect(screen.getByText("Bone Density (eBMD)")).toBeInTheDocument()
  })

  it("renders an unknown finding module with a humanized label (drift-proof)", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    // "gout" is in neither MODULE_ORDER nor the display-name map, yet it must
    // still appear (data-driven), title-cased.
    expect(screen.getByText("Gout")).toBeInTheDocument()
  })

  it("counts every module's findings (no silent undercount)", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    // All 6 modules auto-selected; total findings = 2+3+1+1+1+2 = 10.
    expect(screen.getByText("Modules (6 of 6 selected)")).toBeInTheDocument()
    expect(screen.getByText("10")).toBeInTheDocument()
  })
})
