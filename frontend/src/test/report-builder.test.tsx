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
import ReportBuilder, {
  MAX_REPORT_FINDINGS,
  MAX_INLINE_PREVIEW_HTML_CHARS,
} from "@/pages/ReportBuilder"
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
  return { ...rtlRender(ui, { wrapper: Wrapper }), queryClient }
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

const LARGE_SUMMARY: FindingsSummaryResponse = {
  total_findings: MAX_REPORT_FINDINGS + 2,
  modules: [
    {
      module: "rare_variants",
      count: MAX_REPORT_FINDINGS + 1,
      max_evidence_level: 1,
      top_finding_text: "Large rare-variant inventory",
    },
    { module: "carrier", count: 1, max_evidence_level: 3, top_finding_text: "CFTR carrier" },
  ],
  high_confidence_findings: [],
}

const FHIR_ELIGIBLE = {
  exportable: true,
  max_observations: 1_000,
  observation_count: 4,
  reason: null,
}

const FHIR_TOO_LARGE = {
  exportable: false,
  max_observations: 1_000,
  observation_count: null,
  reason: "too_large",
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
    if (typeof url === "string" && url.includes("/api/export/fhir/eligibility")) {
      return {
        ok: true,
        json: async () => FHIR_ELIGIBLE,
        text: async () => JSON.stringify(FHIR_ELIGIBLE),
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

  it("enables FHIR only after the actual export scope is verified", async () => {
    mockSummaryFetch()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByLabelText("Export FHIR R4 Bundle")).toBeEnabled()
    })
  })

  it("keeps FHIR export enabled while a background eligibility refetch is in flight", async () => {
    // Gating on `isFetching` disabled the button for the duration of every
    // background refetch, including the one TanStack Query fires on window
    // focus, so the button flickered and the "being verified" banner flashed on
    // each alt-tab. Hold the second request open to prove the enabled state
    // survives an in-flight refetch; a refetch that fails is a separate case,
    // covered by the test below.
    let eligibilityRequests = 0
    let releaseRefetch: (() => void) | undefined
    const refetchInFlight = new Promise<void>((resolve) => {
      releaseRefetch = resolve
    })
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return {
          ok: true,
          json: async () => MOCK_SUMMARY,
          text: async () => JSON.stringify(MOCK_SUMMARY),
        }
      }
      if (typeof url === "string" && url.includes("/api/export/fhir/eligibility")) {
        eligibilityRequests += 1
        if (eligibilityRequests > 1) await refetchInFlight
        return {
          ok: true,
          json: async () => FHIR_ELIGIBLE,
          text: async () => JSON.stringify(FHIR_ELIGIBLE),
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })
    const { queryClient } = renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    const fhirButton = await screen.findByLabelText("Export FHIR R4 Bundle")
    await waitFor(() => expect(fhirButton).toBeEnabled())

    const refetch = queryClient.refetchQueries({ queryKey: ["fhir-export-eligibility", 1] })
    await waitFor(() => expect(eligibilityRequests).toBe(2))

    // The refetch has not resolved yet: the previous verified result still
    // stands, so the action must not disappear from under the user.
    expect(fhirButton).toBeEnabled()
    expect(
      screen.queryByText(/FHIR export is disabled while its size is being verified/),
    ).not.toBeInTheDocument()

    releaseRefetch?.()
    await refetch
    expect(fhirButton).toBeEnabled()
  })

  it("says the bundle will be empty when the selected modules carry no variants", async () => {
    // Annotated but nothing carried is a valid zero-Observation bundle, not a
    // blocked export -- that is the distinction against a never-annotated
    // sample. It stays downloadable, but the user is told before they download
    // an empty file rather than after.
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return {
          ok: true,
          json: async () => MOCK_SUMMARY,
          text: async () => JSON.stringify(MOCK_SUMMARY),
        }
      }
      if (typeof url === "string" && url.includes("/api/export/fhir/eligibility")) {
        const eligibility = {
          exportable: true,
          max_observations: 1_000,
          observation_count: 0,
          reason: null,
        }
        return {
          ok: true,
          json: async () => eligibility,
          text: async () => JSON.stringify(eligibility),
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    const fhirButton = await screen.findByLabelText("Export FHIR R4 Bundle")
    await waitFor(() => expect(fhirButton).toBeEnabled())
    expect(
      screen.getByText(
        /The selected modules carry no variants, so the FHIR bundle will contain 0 Observations/,
      ),
    ).toBeInTheDocument()
  })

  it("fails closed when a verified FHIR eligibility result cannot be refreshed", async () => {
    let eligibilityRequests = 0
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return {
          ok: true,
          json: async () => MOCK_SUMMARY,
          text: async () => JSON.stringify(MOCK_SUMMARY),
        }
      }
      if (typeof url === "string" && url.includes("/api/export/fhir/eligibility")) {
        eligibilityRequests += 1
        if (eligibilityRequests === 1) {
          return {
            ok: true,
            json: async () => FHIR_ELIGIBLE,
            text: async () => JSON.stringify(FHIR_ELIGIBLE),
          }
        }
        return { ok: false, status: 503, text: async () => "Unavailable" }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })
    const { queryClient } = renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    const fhirButton = await screen.findByLabelText("Export FHIR R4 Bundle")
    await waitFor(() => expect(fhirButton).toBeEnabled())

    // The key now carries the module scope, so match by prefix rather than
    // pinning a shape that changes whenever the selection does.
    await queryClient.refetchQueries({ queryKey: ["fhir-export-eligibility", 1] })

    expect(eligibilityRequests).toBe(2)
    await waitFor(() => expect(fhirButton).toBeDisabled())
    expect(
      screen.getByText(/FHIR export is disabled because its size could not be verified/),
    ).toBeInTheDocument()
  })

  it("keeps FHIR disabled when the sample has no annotated variants", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return {
          ok: true,
          json: async () => MOCK_SUMMARY,
          text: async () => JSON.stringify(MOCK_SUMMARY),
        }
      }
      if (typeof url === "string" && url.includes("/api/export/fhir/eligibility")) {
        const eligibility = {
          exportable: false,
          max_observations: 1_000,
          observation_count: 0,
          reason: "no_annotated_variants",
        }
        return {
          ok: true,
          json: async () => eligibility,
          text: async () => JSON.stringify(eligibility),
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    const fhirButton = await screen.findByLabelText("Export FHIR R4 Bundle")
    await waitFor(() =>
      expect(
        screen.getByText(/this sample has no annotated variants. Run annotation first/),
      ).toBeInTheDocument(),
    )
    expect(fhirButton).toBeDisabled()
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

  it("blocks every report action until an oversized selection is reduced", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return { ok: true, json: async () => LARGE_SUMMARY, text: async () => JSON.stringify(LARGE_SUMMARY) }
      }
      if (typeof url === "string" && url.includes("/api/reports/preview")) {
        return { ok: true, text: async () => "<html><body>Too large</body></html>" }
      }
      if (typeof url === "string" && url.includes("/api/export/fhir/eligibility")) {
        return {
          ok: true,
          json: async () => FHIR_TOO_LARGE,
          text: async () => JSON.stringify(FHIR_TOO_LARGE),
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    expect(await screen.findByText("Rare Variant Finder")).toBeInTheDocument()
    expect(
      screen.getByText(/Report actions are disabled for selections with more than/),
    ).toBeInTheDocument()

    const previewButton = screen.getByLabelText("Preview report")
    const downloadButton = screen.getByLabelText("Download PDF report")
    const fhirButton = screen.getByLabelText("Export FHIR R4 Bundle")
    expect(previewButton).toBeDisabled()
    expect(downloadButton).toBeDisabled()
    expect(fhirButton).toBeDisabled()

    await user.click(previewButton)
    await user.click(downloadButton)
    await user.click(fhirButton)

    expect(
      mockFetch.mock.calls.some(
        ([url]) =>
          typeof url === "string" &&
          (url.includes("/api/reports/preview") ||
            url.includes("/api/reports/generate") ||
            (url.includes("/api/export/fhir") && !url.includes("/eligibility"))),
      ),
    ).toBe(false)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    await user.click(
      screen.getByLabelText(
        `Rare Variant Finder: ${MAX_REPORT_FINDINGS + 1} findings`,
      ),
    )

    expect(
      screen.queryByText(/Report actions are disabled for selections with more than/),
    ).not.toBeInTheDocument()
    expect(previewButton).not.toBeDisabled()
    expect(downloadButton).not.toBeDisabled()
    expect(fhirButton).toBeDisabled()
    expect(
      screen.getByText(/FHIR export is disabled because the selected modules would create more than/),
    ).toBeInTheDocument()
    // The FHIR selection is full-sample and ignores the module checkboxes
    // (#2100), so the banner has to say that reducing the selection will not
    // help. Without this the copy reads as if the user could act on it.
    expect(screen.getByText(/Select fewer modules/)).toBeInTheDocument()
  })

  it("does not mount the preview iframe when returned HTML exceeds the inline limit", async () => {
    const oversizedPreview = "x".repeat(MAX_INLINE_PREVIEW_HTML_CHARS + 1)
    mockFetch.mockImplementation(async (url: string) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return { ok: true, json: async () => MOCK_SUMMARY, text: async () => JSON.stringify(MOCK_SUMMARY) }
      }
      if (typeof url === "string" && url.includes("/api/reports/preview")) {
        return { ok: true, text: async () => oversizedPreview }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await screen.findByText("Cancer Predisposition")
    await user.click(screen.getByLabelText("Preview report"))

    await waitFor(() => {
      expect(screen.getByText(/rendered preview is too large to display safely/)).toBeInTheDocument()
    })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(screen.queryByTitle("Report preview")).not.toBeInTheDocument()
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

  it("disables every report action, FHIR included, when no modules are selected", async () => {
    // This inverts a previous assertion, deliberately. FHIR used to stay enabled
    // at zero modules because its selection was full-sample and ignored the
    // checkboxes entirely -- the complaint in #2100. The Report Summary read
    // "Selected modules 0 / Total findings 0" while the one live button handed
    // back every annotated variant. Now that the bundle is scoped by the same
    // selection as the PDF, an empty selection means an empty export.
    mockSummaryFetch()
    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    await waitFor(() => {
      expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument()
    })

    await user.click(screen.getByText("Clear all"))

    expect(screen.getByLabelText("Preview report")).toBeDisabled()
    expect(screen.getByLabelText("Download PDF report")).toBeDisabled()
    expect(screen.getByLabelText("Export FHIR R4 Bundle")).toBeDisabled()
    // ...and it must not blame the sample for the user's own empty selection.
    expect(
      screen.queryByText(/carry no variants, so the FHIR bundle will contain 0 Observations/),
    ).not.toBeInTheDocument()
  })

  it("scopes the FHIR request and its preflight to the selected modules", async () => {
    // The defect in #2100 was that handleFhirExport always sent
    // `include_all: true` with no module field, so a curated selection still
    // exported every annotated variant. Assert on the wire, not on the button.
    const eligibilityUrls: string[] = []
    let fhirBody: { sample_id: number; modules: string[] } | null = null
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (typeof url === "string" && url.includes("/api/analysis/findings/summary")) {
        return {
          ok: true,
          json: async () => MOCK_SUMMARY,
          text: async () => JSON.stringify(MOCK_SUMMARY),
        }
      }
      if (typeof url === "string" && url.includes("/api/export/fhir/eligibility")) {
        eligibilityUrls.push(url)
        return {
          ok: true,
          json: async () => FHIR_ELIGIBLE,
          text: async () => JSON.stringify(FHIR_ELIGIBLE),
        }
      }
      if (typeof url === "string" && url.includes("/api/export/fhir")) {
        fhirBody = JSON.parse(String(init?.body))
        return { ok: true, blob: async () => new Blob(["{}"]) }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })
    const user = userEvent.setup()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])

    const fhirButton = await screen.findByLabelText("Export FHIR R4 Bundle")
    await waitFor(() => expect(fhirButton).toBeEnabled())

    // The preflight asks about the selection, not about the whole sample.
    await waitFor(() => expect(eligibilityUrls.at(-1)).toContain("modules="))
    const latest = eligibilityUrls.at(-1) as string
    expect(latest).toContain("modules=cancer")
    expect(latest).toContain("modules=pharmacogenomics")
    expect(latest).toContain("modules=nutrigenomics")

    await user.click(fhirButton)

    await waitFor(() => expect(fhirBody).not.toBeNull())
    const sent = fhirBody as unknown as { sample_id: number; modules: string[] }
    expect(sent.sample_id).toBe(1)
    expect([...sent.modules].sort()).toEqual([
      "cancer",
      "nutrigenomics",
      "pharmacogenomics",
    ])
  })
})

// ── Module completeness / drift-proofing (#596) ─────────────────────

const COMPLETE_SUMMARY: FindingsSummaryResponse = {
  total_findings: 15,
  modules: [
    { module: "cancer", count: 2, max_evidence_level: 4, top_finding_text: "BRCA1 pathogenic variant" },
    // module value the backend actually writes is "carrier" (not "carrier_status")
    { module: "carrier", count: 3, max_evidence_level: 3, top_finding_text: "CFTR carrier" },
    { module: "metabolic", count: 1, max_evidence_level: 2, top_finding_text: "T2D risk" },
    { module: "fh", count: 1, max_evidence_level: 3, top_finding_text: "FH variant" },
    { module: "ebmd", count: 1, max_evidence_level: 2, top_finding_text: "Low BMD" },
    { module: "fitness", count: 1, max_evidence_level: 2, top_finding_text: "Fitness result" },
    { module: "sleep", count: 1, max_evidence_level: 2, top_finding_text: "Sleep result" },
    { module: "skin", count: 1, max_evidence_level: 2, top_finding_text: "Skin result" },
    { module: "allergy", count: 1, max_evidence_level: 2, top_finding_text: "Allergy result" },
    // panel-only module: absent from the local display-name map, present in MODULE_META
    { module: "amd", count: 1, max_evidence_level: 2, top_finding_text: "AMD risk" },
    // truly unmapped module: absent from MODULE_ORDER, the display-name map, and MODULE_META
    { module: "research_panel", count: 2, max_evidence_level: 2, top_finding_text: "Research risk" },
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

  it("uses canonical registry labels for panel-only modules missing from the local label map", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    expect(screen.getByText("AMD")).toBeInTheDocument()
    expect(screen.queryByText("Amd")).not.toBeInTheDocument()
  })

  it("uses canonical registry labels for the shared pathway modules (#2039)", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    for (const label of ["Fitness", "Sleep", "Skin", "Allergy"]) {
      expect(screen.getByText(label)).toBeInTheDocument()
      expect(screen.queryByText(`Gene ${label}`)).not.toBeInTheDocument()
    }
  })

  it("renders a truly unknown finding module with a humanized label (drift-proof)", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    expect(screen.getByText("Research Panel")).toBeInTheDocument()
  })

  it("counts every module's findings (no silent undercount)", async () => {
    mockCompleteSummary()
    renderWithRoute(<ReportBuilder />, ["/reports?sample_id=1"])
    await waitFor(() => expect(screen.getByText("Cancer Predisposition")).toBeInTheDocument())
    // All 11 modules auto-selected; total findings = 2+3+(7×1)+2 = 15.
    expect(screen.getByText("Modules (11 of 11 selected)")).toBeInTheDocument()
    expect(screen.getByText("15")).toBeInTheDocument()
  })
})
