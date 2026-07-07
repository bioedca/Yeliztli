/** ConcordanceReport page unit tests (Step 70 / MRG-06; Plan §10.6, §10.7).
 *
 * Covers the four states the page surfaces:
 *
 *   1. Happy path — provenance + first page render the summary card
 *      buckets, the per-page range string, and the discordant-loci table
 *      with gene-context columns.
 *   2. Pagination — Next button advances `offset` by `PAGE_SIZE`; Prev
 *      goes back; both fire a follow-on GET with the new offset.
 *   3. Empty state — `total_discordant=0` renders the "no discordant
 *      loci on this page" PageEmpty instead of the table.
 *   4. 423 stale-sample handling — `require_fresh_sample` payload routes
 *      to the stale banner with a re-annotate CTA pointing at the
 *      `reannotate_url` from the FastAPI dependency.
 *   5. 404 (not-merged sample) — empty state pointing back to dashboard.
 *
 * Step 87 (MRG-12) is the canonical Phase 3 coverage gate; this file
 * lands the test surface in the same PR as the page implementation per
 * CLAUDE.md DoD ("Land new/changed tests in this same step.").
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render as rtlRender } from "@testing-library/react"
import { ThemeProvider } from "@/lib/ThemeContext"
import { fireEvent, screen, waitFor } from "./test-utils"

import ConcordanceReport from "@/pages/ConcordanceReport"

// ── Test data ────────────────────────────────────────────────────────────

const SAMPLE_ID = 99

const PROVENANCE_PAYLOAD = {
  merged_at: "2026-05-20T15:30:00",
  strategy: "flag_only",
  source_sample_ids: [11, 22],
  source_file_hashes: ["sha-a", "sha-b"],
  concordance_summary: {
    match: 412_345,
    filled_nocall: 1_234,
    discordant: 87,
    unique_S1: 5_000,
    unique_S2: 6_500,
    collapsed_rsid: 19,
  },
}

const REPORT_PAGE_1 = {
  concordance_summary: PROVENANCE_PAYLOAD.concordance_summary,
  total_discordant: 87,
  limit: 50,
  offset: 0,
  discordant_loci: [
    {
      rsid: "rs429358",
      chrom: "19",
      pos: 44_908_684,
      genotype: "??",
      discordant_alt_genotype: "S1=CT;S2=TT",
      alt_rsid: "",
      gene_symbol: "APOE",
      consequence: "missense_variant",
      clinvar_significance: "Pathogenic",
    },
    {
      rsid: "rs1801133",
      chrom: "1",
      pos: 11_856_378,
      genotype: "??",
      discordant_alt_genotype: "S1=GA;S2=GG",
      alt_rsid: "rs1801131",
      gene_symbol: "MTHFR",
      consequence: "missense_variant",
      clinvar_significance: null,
    },
  ],
}

const REPORT_PAGE_2 = {
  concordance_summary: PROVENANCE_PAYLOAD.concordance_summary,
  total_discordant: 87,
  limit: 50,
  offset: 50,
  discordant_loci: [
    {
      rsid: "rs7412",
      chrom: "19",
      pos: 44_908_822,
      genotype: "??",
      discordant_alt_genotype: "S1=CC;S2=CT",
      alt_rsid: "",
      gene_symbol: "APOE",
      consequence: "missense_variant",
      clinvar_significance: "Pathogenic",
    },
  ],
}

const REPORT_EMPTY = {
  concordance_summary: {
    match: 1_000,
    filled_nocall: 0,
    discordant: 0,
    unique_S1: 0,
    unique_S2: 0,
    collapsed_rsid: 0,
  },
  total_discordant: 0,
  limit: 50,
  offset: 0,
  discordant_loci: [],
}

const STALE_PAYLOAD = {
  detail: {
    sample_id: SAMPLE_ID,
    installed_version: "v2.0.0",
    required_version: "v2.0.0",
    update_url: "/settings/updates",
    reannotate_url: `/api/annotation/${SAMPLE_ID}`,
    message: "Re-annotate to view this report.",
  },
}

// ── fetch mock ───────────────────────────────────────────────────────────

const mockFetch = vi.fn()

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this
    },
  } as unknown as Response
}

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// The shared `test-utils` wrapper mounts `<MemoryRouter>` at "/" with no
// way to thread `initialEntries`, so `useParams` never sees `:id`. Render
// our own provider stack here that seeds the router with the report URL.
function renderReport() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[`/samples/${SAMPLE_ID}/concordance`]}>
          <Routes>
            <Route
              path="/samples/:id/concordance"
              element={<ConcordanceReport />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

function provenanceUrl() {
  return `/api/samples/${SAMPLE_ID}/merge-provenance`
}

function reportUrl(offset: number) {
  return `/api/samples/${SAMPLE_ID}/concordance-report?limit=50&offset=${offset}`
}

// ═══════════════════════════════════════════════════════════════════════
// Happy path
// ═══════════════════════════════════════════════════════════════════════

describe("ConcordanceReport — happy path", () => {
  it("renders header, summary buckets, and discordant-loci table", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl()) return Promise.resolve(jsonResponse(PROVENANCE_PAYLOAD))
      if (url === reportUrl(0)) return Promise.resolve(jsonResponse(REPORT_PAGE_1))
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    // Header
    await waitFor(() => {
      expect(screen.getByTestId("concordance-strategy")).toHaveTextContent(
        "Flag discordant",
      )
    })
    expect(screen.getByTestId("concordance-source-count")).toHaveTextContent("2")

    // Summary buckets — formatted with thousands separators
    await waitFor(() => {
      expect(screen.getByTestId("concordance-bucket-match")).toHaveTextContent(
        "412,345",
      )
    })
    expect(
      screen.getByTestId("concordance-bucket-discordant"),
    ).toHaveTextContent("87")
    expect(
      screen.getByTestId("concordance-bucket-collapsed_rsid"),
    ).toHaveTextContent("19")

    // Total + page range
    expect(
      screen.getByTestId("concordance-total-discordant"),
    ).toHaveTextContent("87")
    expect(screen.getByTestId("concordance-page-range")).toHaveTextContent(
      "1–50 of 87",
    )

    // Gene-context columns rendered for each row
    expect(
      screen.getByTestId("concordance-locus-rs429358"),
    ).toHaveTextContent("APOE")
    expect(
      screen.getByTestId("concordance-locus-rs429358"),
    ).toHaveTextContent("missense_variant")
    expect(
      screen.getByTestId("concordance-locus-rs429358"),
    ).toHaveTextContent("Pathogenic")

    // Source-call cell preserves the S1/S2 split
    expect(
      screen.getByTestId("concordance-locus-rs1801133"),
    ).toHaveTextContent("S1=GA;S2=GG")
    // alt_rsid column carries the rsid-collapse loser
    expect(
      screen.getByTestId("concordance-locus-rs1801133"),
    ).toHaveTextContent("rs1801131")
  })

  it("renders sample-preference strategy labels", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl()) {
        return Promise.resolve(
          jsonResponse({ ...PROVENANCE_PAYLOAD, strategy: "prefer_s2" }),
        )
      }
      if (url === reportUrl(0)) return Promise.resolve(jsonResponse(REPORT_PAGE_1))
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    await waitFor(() => {
      expect(screen.getByTestId("concordance-strategy")).toHaveTextContent(
        "Prefer S2",
      )
    })
  })
})

// ═══════════════════════════════════════════════════════════════════════
// Pagination
// ═══════════════════════════════════════════════════════════════════════

describe("ConcordanceReport — pagination", () => {
  it("Next advances offset by PAGE_SIZE and refetches", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl()) return Promise.resolve(jsonResponse(PROVENANCE_PAYLOAD))
      if (url === reportUrl(0)) return Promise.resolve(jsonResponse(REPORT_PAGE_1))
      if (url === reportUrl(50)) return Promise.resolve(jsonResponse(REPORT_PAGE_2))
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    await waitFor(() => {
      expect(screen.getByTestId("concordance-locus-rs429358")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("concordance-next-button"))

    await waitFor(() => {
      expect(screen.getByTestId("concordance-locus-rs7412")).toBeInTheDocument()
    })
    expect(
      mockFetch.mock.calls.some((call) => call[0] === reportUrl(50)),
    ).toBe(true)
    expect(screen.getByTestId("concordance-page-range")).toHaveTextContent(
      "51–87 of 87",
    )

    // Prev returns to page 1.
    fireEvent.click(screen.getByTestId("concordance-prev-button"))
    await waitFor(() => {
      expect(screen.getByTestId("concordance-page-range")).toHaveTextContent(
        "1–50 of 87",
      )
    })
  })

  it("disables Prev on first page", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl()) return Promise.resolve(jsonResponse(PROVENANCE_PAYLOAD))
      if (url === reportUrl(0)) return Promise.resolve(jsonResponse(REPORT_PAGE_1))
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    await waitFor(() => {
      expect(screen.getByTestId("concordance-locus-rs429358")).toBeInTheDocument()
    })
    expect(screen.getByTestId("concordance-prev-button")).toBeDisabled()
  })
})

// ═══════════════════════════════════════════════════════════════════════
// Empty state
// ═══════════════════════════════════════════════════════════════════════

describe("ConcordanceReport — empty discordant loci", () => {
  it("renders the PageEmpty when total_discordant=0", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl())
        return Promise.resolve(
          jsonResponse({
            ...PROVENANCE_PAYLOAD,
            concordance_summary: REPORT_EMPTY.concordance_summary,
          }),
        )
      if (url === reportUrl(0)) return Promise.resolve(jsonResponse(REPORT_EMPTY))
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    await waitFor(() => {
      expect(
        screen.getByText(/No discordant loci on this page/i),
      ).toBeInTheDocument()
    })
    expect(
      screen.getByText(/Sources agree on every overlapping call/i),
    ).toBeInTheDocument()
    expect(screen.getByTestId("concordance-page-range")).toHaveTextContent(
      "0 of 0",
    )
    expect(screen.getByTestId("concordance-prev-button")).toBeDisabled()
    expect(screen.getByTestId("concordance-next-button")).toBeDisabled()
  })
})

// ═══════════════════════════════════════════════════════════════════════
// Stale-sample (HTTP 423) handling
// ═══════════════════════════════════════════════════════════════════════

describe("ConcordanceReport — 423 stale-sample handling", () => {
  it("surfaces the stale banner with the dependency's reannotate_url CTA", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl())
        return Promise.resolve(jsonResponse(STALE_PAYLOAD, 423))
      if (url === reportUrl(0))
        return Promise.resolve(jsonResponse(STALE_PAYLOAD, 423))
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    await waitFor(() => {
      expect(screen.getByTestId("concordance-stale-banner")).toBeInTheDocument()
    })

    expect(
      screen.getByText(/Re-annotate to view this report/i),
    ).toBeInTheDocument()
    const cta = screen.getByTestId("concordance-reannotate-cta") as HTMLAnchorElement
    expect(cta.getAttribute("href")).toBe(`/api/annotation/${SAMPLE_ID}`)

    // Summary + table must NOT render under a stale gate.
    expect(screen.queryByTestId("concordance-summary")).not.toBeInTheDocument()
    expect(
      screen.queryByTestId("concordance-discordant-table"),
    ).not.toBeInTheDocument()
  })
})

// ═══════════════════════════════════════════════════════════════════════
// 404 (not a merged sample) handling
// ═══════════════════════════════════════════════════════════════════════

describe("ConcordanceReport — 404 not-merged handling", () => {
  it("renders the not-merged empty state and skips the table", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl())
        return Promise.resolve(
          jsonResponse(
            { detail: `Sample ${SAMPLE_ID} has no merge provenance.` },
            404,
          ),
        )
      // The report query is enabled too; respond with 404 to mirror the
      // backend behaviour even though the page short-circuits on the
      // provenance error before reading the report.
      if (url === reportUrl(0))
        return Promise.resolve(
          jsonResponse(
            { detail: `Sample ${SAMPLE_ID} has no merge provenance.` },
            404,
          ),
        )
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    await waitFor(() => {
      expect(
        screen.getByText(/No merge provenance for this sample/i),
      ).toBeInTheDocument()
    })
    expect(
      screen.queryByTestId("concordance-discordant-table"),
    ).not.toBeInTheDocument()
  })
})

// ═══════════════════════════════════════════════════════════════════════
// Report-query failure (provenance OK, report 500)
// ═══════════════════════════════════════════════════════════════════════

describe("ConcordanceReport — report-query 5xx handling", () => {
  it("renders the table-area PageError when only the report query fails", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === provenanceUrl())
        return Promise.resolve(jsonResponse(PROVENANCE_PAYLOAD))
      if (url === reportUrl(0))
        return Promise.resolve(
          jsonResponse({ detail: "internal server error" }, 500),
        )
      throw new Error(`unexpected fetch: ${url}`)
    })

    renderReport()

    // Header + summary still render — only the table area degrades.
    await waitFor(() => {
      expect(screen.getByTestId("concordance-bucket-match")).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.getByText(/internal server error/i)).toBeInTheDocument()
    })
    expect(
      screen.queryByTestId("concordance-discordant-table"),
    ).not.toBeInTheDocument()
  })
})
