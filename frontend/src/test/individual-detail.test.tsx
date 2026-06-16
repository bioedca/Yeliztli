/** Tests for the `/individuals/{id}` page (Step 50 / IND-06; Plan §9.5).
 *
 * Covers:
 *   - metadata header renders display_name, biological_sex, notes
 *   - linked-samples table renders one row per linked sample with
 *     vendor + format + variant count + status
 *   - aggregated high-confidence findings union across linked samples
 *     are deduplicated by rsid with multi-source provenance chips
 *   - empty state when no samples are linked
 *   - PageError when the API rejects
 */

import type { ReactNode } from "react"
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, within, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes } from "react-router-dom"

import IndividualDetail from "@/pages/IndividualDetail"

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    clone() {
      return this
    },
  } as unknown as Response
}

function createWrapper(initialEntries: string[]) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={initialEntries}>
          <Routes>
            <Route path="/individuals/:id" element={children} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

interface MockLinkedSample {
  id: number
  name: string
  file_format: string
  vendor: string
  variantCount: number | null
  highConfidenceFindings: Array<{
    id: number
    module: string
    rsid: string | null
    finding_text: string
    evidence_level: number
    gene_symbol?: string | null
  }>
}

interface MockIndividual {
  id: number
  display_name: string
  notes?: string | null
  biological_sex?: "XX" | "XY" | null
  aggregated_findings_count?: number
  linked_samples: MockLinkedSample[]
}

function installMocks(individual: MockIndividual) {
  mockFetch.mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString()

    if (url === `/api/individuals/${individual.id}`) {
      return Promise.resolve(
        jsonResponse({
          id: individual.id,
          display_name: individual.display_name,
          notes: individual.notes ?? null,
          biological_sex: individual.biological_sex ?? null,
          created_at: "2026-05-01T00:00:00",
          updated_at: null,
          linked_samples: individual.linked_samples.map((s) => ({
            id: s.id,
            name: s.name,
            file_format: s.file_format,
            vendor: s.vendor,
            created_at: "2026-05-01T00:00:00",
            updated_at: null,
          })),
          aggregated_findings_count: individual.aggregated_findings_count ?? 0,
        }),
      )
    }

    const countMatch = /^\/api\/variants\/count\?sample_id=(\d+)/.exec(url)
    if (countMatch) {
      const sid = Number(countMatch[1])
      const sample = individual.linked_samples.find((s) => s.id === sid)
      return Promise.resolve(
        jsonResponse({ total: sample?.variantCount ?? 0 }),
      )
    }

    const summaryMatch = /^\/api\/analysis\/findings\/summary\?sample_id=(\d+)/.exec(url)
    if (summaryMatch) {
      const sid = Number(summaryMatch[1])
      const sample = individual.linked_samples.find((s) => s.id === sid)
      const findings = sample?.highConfidenceFindings ?? []
      return Promise.resolve(
        jsonResponse({
          total_findings: findings.length,
          modules: [],
          high_confidence_findings: findings.map((f) => ({
            id: f.id,
            module: f.module,
            category: null,
            evidence_level: f.evidence_level,
            gene_symbol: f.gene_symbol ?? null,
            rsid: f.rsid,
            finding_text: f.finding_text,
            phenotype: null,
            conditions: null,
            zygosity: null,
            clinvar_significance: null,
            diplotype: null,
            metabolizer_status: null,
            drug: null,
            haplogroup: null,
            prs_score: null,
            prs_percentile: null,
            pathway: null,
            pathway_level: null,
            svg_path: null,
            pmid_citations: [],
            detail: null,
            created_at: null,
          })),
        }),
      )
    }

    return Promise.resolve(jsonResponse({ detail: "not mocked" }, 500))
  })
}

describe("IndividualDetail page", () => {
  it("renders metadata, linked samples table, and per-sample variant counts", async () => {
    installMocks({
      id: 7,
      display_name: "Alice",
      notes: "Sibling cohort",
      biological_sex: "XX",
      aggregated_findings_count: 3,
      linked_samples: [
        {
          id: 11,
          name: "alice_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 612345,
          highConfidenceFindings: [],
        },
        {
          id: 12,
          name: "alice_ancestry.txt",
          file_format: "ancestrydna_v2.0",
          vendor: "ancestrydna",
          variantCount: 720000,
          highConfidenceFindings: [],
        },
      ],
    })

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/7"]),
    })

    expect(await screen.findByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Sibling cohort")).toBeInTheDocument()
    expect(screen.getByText("XX")).toBeInTheDocument()

    const row11 = await screen.findByTestId("linked-sample-row-11")
    const row12 = await screen.findByTestId("linked-sample-row-12")
    expect(within(row11).getByText("23andMe")).toBeInTheDocument()
    expect(within(row12).getByText("AncestryDNA")).toBeInTheDocument()

    await waitFor(() => {
      expect(within(row11).getByText("612,345")).toBeInTheDocument()
      expect(within(row12).getByText("720,000")).toBeInTheDocument()
    })

    expect(within(row11).getAllByText("Ready")[0]).toBeInTheDocument()
    expect(within(row12).getAllByText("Ready")[0]).toBeInTheDocument()
  })

  it("deduplicates aggregated findings by rsid and emits a provenance chip per source sample", async () => {
    installMocks({
      id: 5,
      display_name: "Bob",
      biological_sex: "XY",
      linked_samples: [
        {
          id: 21,
          name: "bob_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 600000,
          highConfidenceFindings: [
            {
              id: 1001,
              module: "apoe",
              rsid: "rs429358",
              finding_text: "APOE ε4 carrier",
              evidence_level: 4,
              gene_symbol: "APOE",
            },
            {
              id: 1002,
              module: "pharmacogenomics",
              rsid: "rs1057910",
              finding_text: "CYP2C9 intermediate metabolizer",
              evidence_level: 3,
              gene_symbol: "CYP2C9",
            },
          ],
        },
        {
          id: 22,
          name: "bob_ancestry.txt",
          file_format: "ancestrydna_v2.0",
          vendor: "ancestrydna",
          variantCount: 700000,
          highConfidenceFindings: [
            {
              id: 2001,
              module: "apoe",
              rsid: "rs429358",
              finding_text: "APOE ε4 carrier",
              evidence_level: 4,
              gene_symbol: "APOE",
            },
            {
              id: 2002,
              module: "carrier",
              rsid: "rs113993960",
              finding_text: "CFTR ΔF508 carrier",
              evidence_level: 4,
              gene_symbol: "CFTR",
            },
          ],
        },
      ],
    })

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/5"]),
    })

    // Three unique findings emerge from four per-sample findings (APOE collapses).
    await waitFor(() => {
      expect(screen.getByText("3 unique")).toBeInTheDocument()
    })

    const apoeRow = await screen.findByTestId("aggregated-finding-rsid:rs429358")
    // APOE row carries provenance chips for both source samples.
    expect(
      within(apoeRow).getByTestId("provenance-chip-rsid:rs429358-21"),
    ).toHaveTextContent("bob_23andme.txt")
    expect(
      within(apoeRow).getByTestId("provenance-chip-rsid:rs429358-22"),
    ).toHaveTextContent("bob_ancestry.txt")

    const cypRow = screen.getByTestId("aggregated-finding-rsid:rs1057910")
    expect(within(cypRow).getByText("bob_23andme.txt")).toBeInTheDocument()
    expect(
      within(cypRow).queryByText("bob_ancestry.txt"),
    ).not.toBeInTheDocument()

    const cftrRow = screen.getByTestId("aggregated-finding-rsid:rs113993960")
    expect(within(cftrRow).getByText("bob_ancestry.txt")).toBeInTheDocument()
    expect(
      within(cftrRow).queryByText("bob_23andme.txt"),
    ).not.toBeInTheDocument()
  })

  it("reconciles the header total with the capped preview via 'showing X of N' + an overflow row (#827)", async () => {
    // The /findings/summary endpoint returns a top-5-per-sample PREVIEW, while the
    // header aggregated_findings_count is the uncapped total. With 23 total but only
    // 5 preview rows, the section must say "showing 5 of 23" and surface the 18
    // hidden findings rather than presenting "5 unique" beside a header of 23.
    installMocks({
      id: 8,
      display_name: "Dave",
      aggregated_findings_count: 23,
      linked_samples: [
        {
          id: 30,
          name: "dave_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 600000,
          // The backend caps the summary at 5; mirror that here.
          highConfidenceFindings: Array.from({ length: 5 }, (_, i) => ({
            id: i + 1,
            module: "carrier_status",
            rsid: `rs${1000 + i}`,
            finding_text: `Carrier finding ${i + 1}`,
            evidence_level: 4,
            gene_symbol: `GENE${i + 1}`,
          })),
        },
      ],
    })

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/8"]),
    })

    // Header shows the true uncapped total.
    expect(await screen.findByText("Dave")).toBeInTheDocument()
    expect(screen.getByText("23")).toBeInTheDocument()

    // Section badge references the total (not the misleading "5 unique"), and the
    // 18 hidden findings are surfaced via an explicit overflow affordance.
    expect(await screen.findByText("showing 5 of 23")).toBeInTheDocument()
    const overflow = screen.getByTestId("aggregated-findings-overflow")
    expect(overflow).toHaveTextContent("18 more")
    expect(screen.queryByText("5 unique")).not.toBeInTheDocument()
  })

  it("renders an empty state when the individual has no linked samples", async () => {
    installMocks({
      id: 9,
      display_name: "Carol",
      linked_samples: [],
    })

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/9"]),
    })

    expect(await screen.findByText("Carol")).toBeInTheDocument()
    expect(screen.getByText("No samples linked yet")).toBeInTheDocument()
    expect(
      screen.getByText("Link samples to see aggregated findings"),
    ).toBeInTheDocument()
  })

  it("surfaces a retry-able error when the individuals API rejects", async () => {
    mockFetch.mockImplementation(() =>
      Promise.resolve(jsonResponse({ detail: "boom" }, 500)),
    )

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/123"]),
    })

    expect(await screen.findByText(/Failed to load data/i)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument()
  })

  it("rejects an invalid id segment with a clear error", () => {
    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/not-a-number"]),
    })

    expect(
      screen.getByText("Invalid individual id in URL."),
    ).toBeInTheDocument()
  })
})
