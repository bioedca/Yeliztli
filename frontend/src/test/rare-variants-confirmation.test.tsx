import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Link, MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import RareVariantsView from "@/pages/RareVariantsView"
import type { RareVariant, RareVariantFindingsListResponse } from "@/types/rare-variants"

// Synthetic API responses exercise the production page, hooks and gate. The
// category/count are the backend's primary-P/LP decision, not a UI substring test.
const cases = [
  { significance: "Pathogenic", category: "clinvar_pathogenic", count: 1 },
  { significance: "Likely pathogenic", category: "clinvar_pathogenic", count: 1 },
  { significance: "Pathogenic|drug response", category: "clinvar_pathogenic", count: 1 },
  { significance: "Pathogenic", category: "clinvar_pathogenic_low_confidence", count: 1 },
  { significance: "Benign", category: "rare", count: 0 },
  { significance: "Uncertain significance", category: "rare", count: 0 },
  { significance: "Conflicting classifications of pathogenicity", category: "ensemble_pathogenic", count: 0 },
  { significance: "Pathogenic, low penetrance", category: "clinvar_low_penetrance_or_risk_allele", count: 0 },
  { significance: "Pathogenic|Established risk allele", category: "clinvar_low_penetrance_or_risk_allele", count: 0 },
  { significance: null, category: "ensemble_pathogenic", count: 0 },
]

function storedResult(entry = cases[0]): RareVariantFindingsListResponse {
  return {
    items: [{
      rsid: "synthetic-confirmation-1", gene_symbol: "SYNTH1",
      category: entry.category, clinvar_significance: entry.significance,
      evidence_level: 2, finding_text: "Synthetic finding", zygosity: "het",
      conditions: null, detail: {},
    }],
    total: 1,
  }
}

function searchResult(entry: (typeof cases)[number] | null = cases[0]) {
  const variant: RareVariant = {
    rsid: "synthetic-search-1", chrom: "1", pos: 100, ref: "A", alt: "G",
    genotype: "AG", zygosity: "het", gene_symbol: "SYNTH1",
    consequence: "missense_variant", hgvs_coding: null, hgvs_protein: null,
    gnomad_af_global: 0.001, gnomad_af_afr: null, gnomad_af_amr: null,
    gnomad_af_eas: null, gnomad_af_eur: null, gnomad_af_fin: null, gnomad_af_sas: null,
    is_novel: false, clinvar_significance: entry?.significance ?? null,
    clinvar_review_stars: 0, clinvar_accession: null, clinvar_conditions: null,
    cadd_phred: null, revel: null, deleterious_count: null, deleterious_total_assessed: null,
    ensemble_pathogenic: entry?.category === "ensemble_pathogenic", evidence_conflict: false,
    evidence_level: 2, disease_name: null, inheritance_pattern: null,
  }
  return {
    items: entry ? [variant] : [], total: entry ? 1 : 0,
    total_variants_scanned: 10, novel_count: 0, pathogenic_count: entry?.count ?? 0,
    genes_with_findings: entry ? ["SYNTH1"] : [], filters_applied: {},
  }
}

const json = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200, headers: { "Content-Type": "application/json" },
})
const gateName = "Clinical confirmation required before acting on these results"
const gate = () => screen.queryByRole("note", { name: gateName })

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/rare-variants?sample_id=1"]}>
        <Link to="/rare-variants?sample_id=2">Switch to sample 2</Link>
        <RareVariantsView />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockApi(
  findings: (url: URL) => Response | Promise<Response>,
  search: (init?: RequestInit) => Response | Promise<Response> = () => json(searchResult()),
) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost")
    if (url.pathname === "/api/panels") return Promise.resolve(json({ items: [] }))
    if (url.pathname.endsWith("/findings")) return Promise.resolve(findings(url))
    if (url.pathname.endsWith("/search")) return Promise.resolve(search(init))
    throw new Error(`Unexpected request: ${url.pathname}`)
  }))
}

afterEach(() => vi.unstubAllGlobals())

describe.each(["stored", "search"] as const)("Rare Variant confirmation: %s results", (source) => {
  it.each(cases)("uses the primary classification for $significance ($category)", async (entry) => {
    mockApi(() => json(source === "stored" ? storedResult(entry) : { items: [], total: 0 }),
      () => json(searchResult(entry)))
    renderPage()
    if (source === "search") {
      await userEvent.click(screen.getByTestId("search-button"))
      expect(await screen.findByTestId("total-found")).toHaveTextContent("1")
      expect(screen.getByText("synthetic-search-1")).toBeInTheDocument()
    } else {
      expect(await screen.findByTestId("finding-row")).toHaveTextContent("synthetic-confirmation-1")
    }
    if (entry.count) {
      const notice = screen.getByRole("note", { name: gateName })
      expect(notice).toHaveTextContent("Confirm before acting — not a clinical diagnosis")
      expect(notice).toHaveTextContent("CLIA/accredited laboratory")
      expect(within(notice).queryByRole("button")).not.toBeInTheDocument()
    } else {
      expect(gate()).not.toBeInTheDocument()
    }
  })
})

it("replaces the notice with each completed filter search, including empty results", async () => {
  const responses = [searchResult(cases[4]), searchResult(cases[1]), searchResult(null)]
  const filters: unknown[] = []
  mockApi(() => json(storedResult()), (init) => {
    filters.push(JSON.parse(String(init?.body)))
    return json(responses.shift())
  })
  renderPage()
  const user = userEvent.setup()
  await screen.findByTestId("finding-row")
  expect(gate()).toBeInTheDocument()
  await user.type(screen.getByTestId("gene-panel-input"), "SYNTH2")
  await user.click(screen.getByTestId("search-button"))
  await screen.findByTestId("total-found")
  expect(gate()).not.toBeInTheDocument()
  expect(filters[0]).toMatchObject({ gene_symbols: ["SYNTH2"] })
  await user.clear(screen.getByTestId("gene-panel-input"))
  await user.click(screen.getByTestId("search-button"))
  await waitFor(() => expect(gate()).toBeInTheDocument())
  await user.click(screen.getByTestId("search-button"))
  await waitFor(() => expect(screen.getByTestId("total-found")).toHaveTextContent("0"))
  expect(gate()).not.toBeInTheDocument()
  expect(screen.queryByTestId("finding-row")).not.toBeInTheDocument()
  expect(filters).toHaveLength(3)
})

it("does not carry stored findings or their notice into a loading or empty sample", async () => {
  let finish!: (response: Response) => void
  const pending = new Promise<Response>((resolve) => { finish = resolve })
  mockApi((url) => url.searchParams.get("sample_id") === "1" ? json(storedResult()) : pending)
  renderPage()
  await screen.findByTestId("finding-row")
  await userEvent.click(screen.getByRole("link", { name: "Switch to sample 2" }))
  expect(screen.queryByTestId("finding-row")).not.toBeInTheDocument()
  expect(gate()).not.toBeInTheDocument()
  await act(async () => finish(json({ items: [], total: 0 })))
  expect(await screen.findByText("No rare variant findings yet.")).toBeInTheDocument()
  expect(gate()).not.toBeInTheDocument()
})

it("keeps the notice while loading more findings for the same sample", async () => {
  let finish!: (response: Response) => void
  const pending = new Promise<Response>((resolve) => { finish = resolve })
  mockApi((url) => url.searchParams.get("limit") === "200"
    ? json({ ...storedResult(), total: 201 }) : pending)
  renderPage()
  await screen.findByTestId("finding-row")
  await userEvent.click(screen.getByRole("button", { name: "Load more findings" }))
  expect(screen.getByTestId("finding-row")).toBeInTheDocument()
  expect(gate()).toBeInTheDocument()
  await act(async () => finish(json(storedResult())))
  expect(gate()).toBeInTheDocument()
})

it("ignores a previous sample's late P/LP search response", async () => {
  let finish!: (response: Response) => void
  const pending = new Promise<Response>((resolve) => { finish = resolve })
  mockApi(() => json({ items: [], total: 0 }), () => pending)
  renderPage()
  await screen.findByText("No rare variant findings yet.")
  await userEvent.click(screen.getByTestId("search-button"))
  await userEvent.click(screen.getByRole("link", { name: "Switch to sample 2" }))
  await act(async () => finish(json(searchResult())))
  expect(await screen.findByText("No rare variant findings yet.")).toBeInTheDocument()
  expect(screen.queryByTestId("total-found")).not.toBeInTheDocument()
  expect(gate()).not.toBeInTheDocument()
})

it("clears a previous sample's open P/LP detail with its search results and notice", async () => {
  mockApi(() => json({ items: [], total: 0 }))
  renderPage()
  await userEvent.click(screen.getByTestId("search-button"))
  await screen.findByTestId("total-found")
  await userEvent.click(screen.getByTestId("result-row"))
  expect(screen.getByRole("dialog", { name: "SYNTH1 variant detail" })).toBeInTheDocument()
  expect(gate()).toBeInTheDocument()
  await userEvent.click(screen.getByRole("link", { name: "Switch to sample 2" }))
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByTestId("total-found")).not.toBeInTheDocument()
  expect(gate()).not.toBeInTheDocument()
  await screen.findByText("No rare variant findings yet.")
})
