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
import { fireEvent, render, screen, within, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes } from "react-router-dom"

import IndividualDetail from "@/pages/IndividualDetail"

const mockFetch = vi.fn()

type EventSourceListener = (event: MessageEvent) => void

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  listeners: Record<string, EventSourceListener[]> = {}
  readyState = 0

  constructor(url: string) {
    this.url = url
    this.readyState = 1
    MockEventSource.instances.push(this)
  }

  addEventListener(event: string, listener: EventSourceListener) {
    if (!this.listeners[event]) this.listeners[event] = []
    this.listeners[event].push(listener)
  }

  close() {
    this.readyState = 2
  }
}

beforeEach(() => {
  mockFetch.mockReset()
  MockEventSource.instances = []
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
  is_merged?: boolean
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
            is_merged: s.is_merged ?? s.file_format === "merged_v1",
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

function failFindingsSummaryBeforeSuccess(
  failedSampleId: number,
  failedAttempts = 1,
): Map<number, number> {
  const successfulFetch = mockFetch.getMockImplementation()
  if (!successfulFetch) {
    throw new Error("installMocks must run before configuring summary failures")
  }

  const summaryCalls = new Map<number, number>()
  mockFetch.mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString()
    const summaryMatch =
      /^\/api\/analysis\/findings\/summary\?sample_id=(\d+)/.exec(url)
    if (summaryMatch) {
      const sampleId = Number(summaryMatch[1])
      const calls = (summaryCalls.get(sampleId) ?? 0) + 1
      summaryCalls.set(sampleId, calls)
      if (sampleId === failedSampleId && calls <= failedAttempts) {
        return Promise.resolve(jsonResponse({ detail: "summary unavailable" }, 500))
      }
    }
    return successfulFetch(input)
  })

  return summaryCalls
}

function individualPayload(individual: MockIndividual) {
  return {
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
      is_merged: s.is_merged ?? s.file_format === "merged_v1",
      created_at: "2026-05-01T00:00:00",
      updated_at: null,
    })),
    aggregated_findings_count: individual.aggregated_findings_count ?? 0,
  }
}

describe("IndividualDetail page", () => {
  it("keeps the merge wizard mounted when commit refetches detail with the merged sample", async () => {
    vi.stubGlobal("EventSource", MockEventSource)
    const initialSamples: MockLinkedSample[] = [
      {
        id: 11,
        name: "eve_23andme.txt",
        file_format: "23andme_v5",
        vendor: "23andme",
        variantCount: 612345,
        highConfidenceFindings: [],
      },
      {
        id: 12,
        name: "eve_ancestry.txt",
        file_format: "ancestrydna_v2.0",
        vendor: "ancestrydna",
        variantCount: 720000,
        highConfidenceFindings: [],
      },
    ]
    const mergedSample: MockLinkedSample = {
      id: 99,
      name: "Eve (merged)",
      file_format: "merged_v1",
      vendor: "merged",
      variantCount: 950000,
      highConfidenceFindings: [],
    }
    let detailRequests = 0
    const allSamples = [...initialSamples, mergedSample]

    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString()

      if (url === "/api/individuals/10") {
        detailRequests += 1
        return Promise.resolve(
          jsonResponse(
            individualPayload({
              id: 10,
              display_name: "Eve",
              linked_samples:
                detailRequests === 1 ? initialSamples : allSamples,
            }),
          ),
        )
      }

      if (url === "/api/individuals/10/merge/preview") {
        return Promise.resolve(
          jsonResponse({
            concordance_summary: {
              match: 412345,
              filled_nocall: 1234,
              discordant: 87,
              unique_S1: 5000,
              unique_S2: 6500,
              collapsed_rsid: 19,
            },
            est_duration_seconds: 8,
          }),
        )
      }

      if (url === "/api/individuals/10/merge") {
        return Promise.resolve(
          jsonResponse({ merged_sample_id: 99, job_id: "merge-job-1" }, 201),
        )
      }

      const countMatch = /^\/api\/variants\/count\?sample_id=(\d+)/.exec(url)
      if (countMatch) {
        const sid = Number(countMatch[1])
        const sample = allSamples.find((s) => s.id === sid)
        return Promise.resolve(jsonResponse({ total: sample?.variantCount ?? 0 }))
      }

      const summaryMatch =
        /^\/api\/analysis\/findings\/summary\?sample_id=(\d+)/.exec(url)
      if (summaryMatch) {
        return Promise.resolve(
          jsonResponse({
            total_findings: 0,
            modules: [],
            high_confidence_findings: [],
          }),
        )
      }

      return Promise.resolve(jsonResponse({ detail: "not mocked" }, 500))
    })

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/10"]),
    })

    fireEvent.click(await screen.findByTestId("merge-samples-button"))
    expect(screen.getByTestId("merge-wizard-overlay")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /^Preview$/ }))
    expect(await screen.findByTestId("merge-preview-summary")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }))
    fireEvent.click(screen.getByRole("button", { name: /^Merge$/ }))

    expect(await screen.findByTestId("merge-progress")).toBeInTheDocument()
    await waitFor(() => expect(detailRequests).toBeGreaterThan(1))
    const mergedRow = await screen.findByTestId("linked-sample-row-99")
    expect(within(mergedRow).getByText("Merged v1")).toBeInTheDocument()
    expect(screen.getByTestId("merge-wizard-overlay")).toBeInTheDocument()
    expect(screen.getByTestId("merge-source-pair")).toHaveTextContent(
      "eve_23andme.txt",
    )
    expect(screen.getByTestId("merge-source-pair")).toHaveTextContent(
      "eve_ancestry.txt",
    )
    expect(MockEventSource.instances[0].url).toBe(
      "/api/annotation/status/merge-job-1",
    )
  })

  it("offers the merge action when two source samples and one merged sample are linked", async () => {
    installMocks({
      id: 11,
      display_name: "Eve",
      linked_samples: [
        {
          id: 11,
          name: "eve_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 612345,
          highConfidenceFindings: [],
        },
        {
          id: 12,
          name: "eve_ancestry.txt",
          file_format: "ancestrydna_v2.0",
          vendor: "ancestrydna",
          variantCount: 720000,
          highConfidenceFindings: [],
        },
        {
          id: 99,
          name: "Eve (merged)",
          file_format: "merged_v1",
          vendor: "merged",
          variantCount: 950000,
          highConfidenceFindings: [],
        },
      ],
    })

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/11"]),
    })

    expect(await screen.findByTestId("linked-sample-row-99")).toBeInTheDocument()

    const mergeButton = screen.getByTestId("merge-samples-button")
    expect(mergeButton).toBeInTheDocument()

    fireEvent.click(mergeButton)
    expect(screen.getByTestId("merge-source-pair")).toHaveTextContent(
      "eve_23andme.txt",
    )
    expect(screen.getByTestId("merge-source-pair")).toHaveTextContent(
      "eve_ancestry.txt",
    )
    expect(screen.getByTestId("merge-source-pair")).not.toHaveTextContent(
      "Eve (merged)",
    )
  })

  it("hides the merge action when more than two source samples are linked", async () => {
    installMocks({
      id: 12,
      display_name: "Frank",
      linked_samples: [
        {
          id: 31,
          name: "frank_23andme_v4.txt",
          file_format: "23andme_v4",
          vendor: "23andme",
          variantCount: 580000,
          highConfidenceFindings: [],
        },
        {
          id: 32,
          name: "frank_23andme_v5.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 612345,
          highConfidenceFindings: [],
        },
        {
          id: 33,
          name: "frank_ancestry.txt",
          file_format: "ancestrydna_v2.0",
          vendor: "ancestrydna",
          variantCount: 720000,
          highConfidenceFindings: [],
        },
      ],
    })

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/12"]),
    })

    expect(await screen.findByTestId("linked-sample-row-31")).toBeInTheDocument()
    expect(screen.queryByTestId("merge-samples-button")).not.toBeInTheDocument()
  })

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
    expect(within(row11).getByText("23andMe v5")).toBeInTheDocument()
    expect(within(row12).getByText("AncestryDNA")).toBeInTheDocument()
    expect(within(row12).getByText("AncestryDNA v2.0")).toBeInTheDocument()

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
    expect(
      within(apoeRow).getByTestId("aggregated-finding-module-rsid:rs429358"),
    ).toHaveTextContent(/^APOE$/)
    // APOE row carries provenance chips for both source samples.
    expect(
      within(apoeRow).getByTestId("provenance-chip-rsid:rs429358-21"),
    ).toHaveTextContent("bob_23andme.txt")
    expect(
      within(apoeRow).getByTestId("provenance-chip-rsid:rs429358-22"),
    ).toHaveTextContent("bob_ancestry.txt")

    const cypRow = screen.getByTestId("aggregated-finding-rsid:rs1057910")
    expect(
      within(cypRow).getByTestId("aggregated-finding-module-rsid:rs1057910"),
    ).toHaveTextContent(/^Pharmacogenomics$/)
    expect(within(cypRow).getByText("bob_23andme.txt")).toBeInTheDocument()
    expect(
      within(cypRow).queryByText("bob_ancestry.txt"),
    ).not.toBeInTheDocument()

    const cftrRow = screen.getByTestId("aggregated-finding-rsid:rs113993960")
    expect(
      within(cftrRow).getByTestId("aggregated-finding-module-rsid:rs113993960"),
    ).toHaveTextContent(/^Carrier Status$/)
    expect(within(cftrRow).getByText("bob_ancestry.txt")).toBeInTheDocument()
    expect(
      within(cftrRow).queryByText("bob_23andme.txt"),
    ).not.toBeInTheDocument()
  })

  it("shows a named error instead of the benign empty state and retries the failed summary", async () => {
    installMocks({
      id: 15,
      display_name: "Erin",
      aggregated_findings_count: 1,
      linked_samples: [
        {
          id: 41,
          name: "erin_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 600000,
          highConfidenceFindings: [
            {
              id: 4101,
              module: "apoe",
              rsid: "rs429358",
              finding_text: "APOE ε4 carrier",
              evidence_level: 4,
              gene_symbol: "APOE",
            },
          ],
        },
      ],
    })
    const summaryCalls = failFindingsSummaryBeforeSuccess(41)

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/15"]),
    })

    const section = await screen.findByRole("region", {
      name: "Aggregated high-confidence findings",
    })
    const alert = await within(section).findByRole("alert")
    expect(alert).toHaveTextContent("Couldn’t load high-confidence findings")
    expect(alert).toHaveTextContent("erin_23andme.txt")
    expect(
      within(section).queryByText("No high-confidence findings yet"),
    ).not.toBeInTheDocument()

    fireEvent.click(within(alert).getByRole("button", { name: /retry/i }))

    expect(
      await within(section).findByTestId("aggregated-finding-rsid:rs429358"),
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(within(section).queryByRole("alert")).not.toBeInTheDocument()
    })
    expect(summaryCalls.get(41)).toBe(2)
  })

  it("offers re-annotation for a stale linked sample instead of a dead retry", async () => {
    const individual: MockIndividual = {
      id: 17,
      display_name: "Gina",
      aggregated_findings_count: 1,
      linked_samples: [
        {
          id: 61,
          name: "gina_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 600000,
          highConfidenceFindings: [],
        },
      ],
    }
    const rawDiagnostic = "sqlite:///private/data/gina.db: stale bundle"
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString()
      if (url === "/api/individuals/17") {
        return Promise.resolve(jsonResponse(individualPayload(individual)))
      }
      if (url === "/api/variants/count?sample_id=61") {
        return Promise.resolve(jsonResponse({ detail: rawDiagnostic }, 423))
      }
      if (url === "/api/analysis/findings/summary?sample_id=61") {
        return Promise.resolve(jsonResponse({ detail: rawDiagnostic }, 423))
      }
      if (url === "/api/annotation/61" && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({ job_id: "annotation-61", sample_id: 61, status: "pending" }, 202),
        )
      }
      return Promise.resolve(jsonResponse({ detail: "not mocked" }, 500))
    })

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity },
        mutations: { retry: false },
      },
    })
    queryClient.setQueryData(["pharma-genes", 61], { staleResult: true })
    queryClient.setQueryData(["pharma-genes", 62], { otherSample: true })
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/individuals/17"]}>
          <Routes>
            <Route path="/individuals/:id" element={children} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )

    render(<IndividualDetail />, { wrapper: Wrapper })

    const staleGate = await screen.findByTestId("individual-stale-sample-gate")
    expect(staleGate).toHaveTextContent("gina_23andme.txt")
    expect(staleGate).toHaveTextContent("Re-annotate")
    expect(screen.getByTestId("linked-sample-reannotate-61")).toHaveTextContent(
      "Re-annotation required",
    )
    expect(document.body).not.toHaveTextContent(rawDiagnostic)
    expect(screen.queryByRole("button", { name: /^retry$/i })).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId("individual-reannotate-61"))
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/annotation/61",
        expect.objectContaining({ method: "POST" }),
      )
    })
    await waitFor(() => {
      expect(queryClient.getQueryState(["pharma-genes", 61])?.isInvalidated).toBe(true)
    })
    expect(queryClient.getQueryState(["pharma-genes", 62])?.isInvalidated).not.toBe(true)
    queryClient.clear()
  })

  it("revalidates cached linked findings before rendering a returning individual page", async () => {
    const individual: MockIndividual = {
      id: 19,
      display_name: "Iris",
      aggregated_findings_count: 1,
      linked_samples: [
        {
          id: 64,
          name: "iris_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 600000,
          highConfidenceFindings: [
            {
              id: 901,
              module: "pharmacogenomics",
              rsid: "rs4244285",
              finding_text: "Cached finding must not survive a stale probe.",
              evidence_level: 4,
            },
          ],
        },
      ],
    }
    installMocks(individual)
    const successfulFetch = mockFetch.getMockImplementation()
    if (!successfulFetch) throw new Error("installMocks must configure fetch")

    let stale = false
    let resolveCount: (() => void) | undefined
    let resolveSummary: (() => void) | undefined
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString()
      if (stale && url === "/api/variants/count?sample_id=64") {
        return new Promise<Response>((resolve) => {
          resolveCount = () => resolve(jsonResponse({ detail: "stale" }, 423))
        })
      }
      if (stale && url === "/api/analysis/findings/summary?sample_id=64") {
        return new Promise<Response>((resolve) => {
          resolveSummary = () => resolve(jsonResponse({ detail: "stale" }, 423))
        })
      }
      return successfulFetch(input, init)
    })

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity },
        mutations: { retry: false },
      },
    })
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/individuals/19"]}>
          <Routes>
            <Route path="/individuals/:id" element={children} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )

    const firstVisit = render(<IndividualDetail />, { wrapper: Wrapper })
    expect(await screen.findByTestId("aggregated-finding-rsid:rs4244285")).toBeInTheDocument()
    firstVisit.unmount()

    stale = true
    render(<IndividualDetail />, { wrapper: Wrapper })
    await waitFor(() => {
      expect(resolveCount).toBeDefined()
      expect(resolveSummary).toBeDefined()
    })

    const row = screen.getByTestId("linked-sample-row-64")
    expect(within(row).getByText("Loading…")).toBeInTheDocument()
    expect(screen.queryByTestId("aggregated-finding-rsid:rs4244285")).not.toBeInTheDocument()

    resolveCount?.()
    resolveSummary?.()

    expect(await screen.findByTestId("individual-stale-sample-gate")).toHaveTextContent(
      "iris_23andme.txt",
    )
    expect(screen.queryByTestId("aggregated-finding-rsid:rs4244285")).not.toBeInTheDocument()
    expect(
      within(screen.getByTestId("linked-sample-row-64")).getByText("Re-annotation required"),
    ).toBeInTheDocument()
    queryClient.clear()
  })

  it("invalidates the target cache when re-annotation is already running", async () => {
    const individual: MockIndividual = {
      id: 18,
      display_name: "Hana",
      linked_samples: [
        {
          id: 63,
          name: "hana_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 600000,
          highConfidenceFindings: [],
        },
      ],
    }
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString()
      if (url === "/api/individuals/18") {
        return Promise.resolve(jsonResponse(individualPayload(individual)))
      }
      if (url === "/api/variants/count?sample_id=63") {
        return Promise.resolve(jsonResponse({ detail: "stale" }, 423))
      }
      if (url === "/api/analysis/findings/summary?sample_id=63") {
        return Promise.resolve(jsonResponse({ detail: "stale" }, 423))
      }
      if (url === "/api/annotation/63" && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ detail: "Already in progress" }, 409))
      }
      return Promise.resolve(jsonResponse({ detail: "not mocked" }, 500))
    })

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity },
        mutations: { retry: false },
      },
    })
    queryClient.setQueryData(["pharma-genes", 63], { staleResult: true })
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/individuals/18"]}>
          <Routes>
            <Route path="/individuals/:id" element={children} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )

    render(<IndividualDetail />, { wrapper: Wrapper })
    await screen.findByTestId("individual-stale-sample-gate")
    fireEvent.click(screen.getByTestId("individual-reannotate-63"))

    await waitFor(() => {
      expect(queryClient.getQueryState(["pharma-genes", 63])?.isInvalidated).toBe(true)
    })
    expect(screen.getByRole("link", { name: "Track re-annotation" })).toBeInTheDocument()
    queryClient.clear()
  })

  it("surfaces a named partial failure without hiding successful findings", async () => {
    installMocks({
      id: 16,
      display_name: "Fran",
      aggregated_findings_count: 2,
      linked_samples: [
        {
          id: 51,
          name: "fran_23andme.txt",
          file_format: "23andme_v5",
          vendor: "23andme",
          variantCount: 600000,
          highConfidenceFindings: [
            {
              id: 5101,
              module: "pharmacogenomics",
              rsid: "rs1057910",
              finding_text: "CYP2C9 intermediate metabolizer",
              evidence_level: 3,
              gene_symbol: "CYP2C9",
            },
          ],
        },
        {
          id: 52,
          name: "fran_ancestry.txt",
          file_format: "ancestrydna_v2.0",
          vendor: "ancestrydna",
          variantCount: 700000,
          highConfidenceFindings: [
            {
              id: 5201,
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
    const summaryCalls = failFindingsSummaryBeforeSuccess(52)

    render(<IndividualDetail />, {
      wrapper: createWrapper(["/individuals/16"]),
    })

    const section = await screen.findByRole("region", {
      name: "Aggregated high-confidence findings",
    })
    const alert = await within(section).findByRole("alert")
    expect(alert).toHaveTextContent("fran_ancestry.txt")
    expect(alert).not.toHaveTextContent("fran_23andme.txt")
    expect(
      await within(section).findByTestId("aggregated-finding-rsid:rs1057910"),
    ).toBeInTheDocument()
    expect(within(section).queryByText("No high-confidence findings yet")).toBeNull()
    expect(within(section).getByText("1 loaded (partial)")).toBeInTheDocument()
    expect(
      within(section).queryByTestId("aggregated-findings-overflow"),
    ).not.toBeInTheDocument()

    fireEvent.click(within(alert).getByRole("button", { name: /retry/i }))

    expect(
      await within(section).findByTestId("aggregated-finding-rsid:rs113993960"),
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(within(section).queryByRole("alert")).not.toBeInTheDocument()
    })
    expect(summaryCalls.get(51)).toBe(1)
    expect(summaryCalls.get(52)).toBe(2)
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
