import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render as rtlRender, screen, waitFor } from "@testing-library/react"
import { render } from "./test-utils"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import type { ReactNode } from "react"
import RareVariantsView from "@/pages/RareVariantsView"
import FilterPanel from "@/components/rare-variants/FilterPanel"
import ResultsTable from "@/components/rare-variants/ResultsTable"
import SearchSummary from "@/components/rare-variants/SearchSummary"
import VariantDetailPanel from "@/components/rare-variants/VariantDetailPanel"
import type { RareVariant } from "@/types/rare-variants"

const mockFetch = vi.fn()

function makeMockVariant(overrides: Partial<RareVariant> = {}): RareVariant {
  return {
    rsid: "rs12345",
    chrom: "17",
    pos: 43071077,
    ref: "A",
    alt: "G",
    genotype: "AG",
    zygosity: "het",
    gene_symbol: "BRCA1",
    consequence: "missense_variant",
    hgvs_coding: "c.1234A>G",
    hgvs_protein: "p.Asp412Gly",
    gnomad_af_global: 0.00023,
    gnomad_af_afr: 0.0001,
    gnomad_af_amr: null,
    gnomad_af_eas: null,
    gnomad_af_eur: 0.0003,
    gnomad_af_fin: null,
    gnomad_af_sas: null,
    // Default is a fully catalogued variant (rs id + ClinVar), so it is NOT novel.
    is_novel: false,
    clinvar_significance: "Pathogenic",
    clinvar_review_stars: 2,
    clinvar_accession: "VCV000012345",
    clinvar_conditions: "Breast-ovarian cancer, familial 1",
    cadd_phred: 28.5,
    revel: 0.892,
    ensemble_pathogenic: true,
    evidence_conflict: false,
    evidence_level: 4,
    disease_name: "Breast cancer",
    inheritance_pattern: "AD",
    ...overrides,
  }
}

describe("FilterPanel", () => {
  it("renders all filter controls", () => {
    render(<FilterPanel onSearch={vi.fn()} isSearching={false} />)
    expect(screen.getByTestId("gene-panel-input")).toBeInTheDocument()
    expect(screen.getByTestId("af-threshold-slider")).toBeInTheDocument()
    expect(screen.getByTestId("consequence-filter")).toBeInTheDocument()
    expect(screen.getByTestId("clinvar-filter")).toBeInTheDocument()
    expect(screen.getByTestId("include-novel-checkbox")).toBeInTheDocument()
    expect(screen.getByTestId("zygosity-select")).toBeInTheDocument()
    expect(screen.getByTestId("search-button")).toBeInTheDocument()
  })

  it("calls onSearch with default filters", async () => {
    const user = userEvent.setup()
    const onSearch = vi.fn()
    render(<FilterPanel onSearch={onSearch} isSearching={false} />)

    await user.click(screen.getByTestId("search-button"))

    expect(onSearch).toHaveBeenCalledTimes(1)
    expect(onSearch).toHaveBeenCalledWith({
      gene_symbols: null,
      af_threshold: 0.01,
      consequences: null,
      clinvar_significance: null,
      include_novel: true,
      zygosity: null,
    })
  })

  it("parses gene panel text input", async () => {
    const user = userEvent.setup()
    const onSearch = vi.fn()
    render(<FilterPanel onSearch={onSearch} isSearching={false} />)

    await user.type(screen.getByTestId("gene-panel-input"), "BRCA1, TP53\nMLH1")
    await user.click(screen.getByTestId("search-button"))

    expect(onSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        gene_symbols: ["BRCA1", "TP53", "MLH1"],
      }),
    )
  })

  it("toggles consequence filters", async () => {
    const user = userEvent.setup()
    const onSearch = vi.fn()
    render(<FilterPanel onSearch={onSearch} isSearching={false} />)

    await user.click(screen.getByText("missense variant"))
    await user.click(screen.getByTestId("search-button"))

    expect(onSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        consequences: ["missense_variant"],
      }),
    )
  })

  it("disables search button when searching", () => {
    render(<FilterPanel onSearch={vi.fn()} isSearching={true} />)
    expect(screen.getByTestId("search-button")).toBeDisabled()
    expect(screen.getByText("Searching...")).toBeInTheDocument()
  })

  it("resets filters when reset button clicked", async () => {
    const user = userEvent.setup()
    const onSearch = vi.fn()
    render(<FilterPanel onSearch={onSearch} isSearching={false} />)

    // Type genes
    await user.type(screen.getByTestId("gene-panel-input"), "BRCA1")
    expect(screen.getByTestId("gene-panel-input")).toHaveValue("BRCA1")

    // Reset
    await user.click(screen.getByLabelText("Reset filters"))
    expect(screen.getByTestId("gene-panel-input")).toHaveValue("")
  })
})

describe("ResultsTable", () => {
  it("renders empty state when no items", () => {
    render(<ResultsTable items={[]} selectedRsid={null} onSelect={vi.fn()} />)
    expect(screen.getByTestId("no-results")).toBeInTheDocument()
  })

  it("renders variant rows", () => {
    const variants = [
      makeMockVariant(),
      makeMockVariant({
        rsid: "rs67890",
        gene_symbol: "TP53",
        evidence_level: 2,
        zygosity: "hom_alt",
      }),
    ]
    render(<ResultsTable items={variants} selectedRsid={null} onSelect={vi.fn()} />)
    expect(screen.getAllByTestId("result-row")).toHaveLength(2)
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    expect(screen.getByText("TP53")).toBeInTheDocument()
    // Zygosity labels: het → "Het", hom_alt → "Hom". A label inversion would
    // render a het carrier as homozygous (a clinically wrong call).
    expect(screen.getByText("Het")).toBeInTheDocument()
    expect(screen.getByText("Hom")).toBeInTheDocument()
  })

  it("calls onSelect when row is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const variant = makeMockVariant()
    render(<ResultsTable items={[variant]} selectedRsid={null} onSelect={onSelect} />)

    await user.click(screen.getByTestId("result-row"))
    expect(onSelect).toHaveBeenCalledWith(variant)
  })

  it("shows Novel only for a genuinely uncatalogued variant (is_novel) (#866)", () => {
    // A truly novel variant: absent from gnomAD AND uncatalogued (no rs id / ClinVar).
    const variant = makeMockVariant({
      gnomad_af_global: null,
      is_novel: true,
      rsid: ".",
      clinvar_significance: null,
      clinvar_accession: null,
    })
    render(<ResultsTable items={[variant]} selectedRsid={null} onSelect={vi.fn()} />)
    expect(screen.getByText("Novel")).toBeInTheDocument()
  })

  it("shows 'Not in gnomAD' (not Novel) for a catalogued gnomAD-absent variant (#866)", () => {
    // The mislabel case: gnomAD-absent but catalogued (rs id + ClinVar) ⇒ is_novel=False.
    // Must NOT read "Novel" (which would contradict the SearchSummary novel_count).
    const variant = makeMockVariant({ gnomad_af_global: null, is_novel: false })
    render(<ResultsTable items={[variant]} selectedRsid={null} onSelect={vi.fn()} />)
    expect(screen.getByText("Not in gnomAD")).toBeInTheDocument()
    expect(screen.queryByText("Novel")).not.toBeInTheDocument()
  })

  it("renders gnomAD AF in one consistent unit across the 0.0001 threshold (#564)", () => {
    // Two near-identical frequencies straddling the old percent/fraction split.
    // Pre-fix, 0.00012 rendered as "0.012%" and 0.00009 as "9.0e-5" — a ~100x
    // unit mismatch that made the smaller value look unrelated. Both must now be
    // bare fractions (no "%") so the column is comparable by eye.
    const variants = [
      makeMockVariant({ rsid: "rs_hi", gnomad_af_global: 0.00012 }),
      makeMockVariant({ rsid: "rs_lo", gnomad_af_global: 0.00009 }),
    ]
    render(<ResultsTable items={variants} selectedRsid={null} onSelect={vi.fn()} />)
    const rows = screen.getAllByTestId("result-row")
    expect(rows).toHaveLength(2)
    for (const row of rows) {
      expect(row.textContent).not.toContain("%")
    }
    expect(screen.getByText("9.00e-5")).toBeInTheDocument()
    expect(screen.getByText("0.0001")).toBeInTheDocument()
  })

  it("shows evidence conflict indicator", () => {
    const variant = makeMockVariant({ evidence_conflict: true })
    render(<ResultsTable items={[variant]} selectedRsid={null} onSelect={vi.fn()} />)
    expect(screen.getByLabelText("Evidence conflict")).toBeInTheDocument()
  })

  it("styles combined Pathogenic/Likely pathogenic as red in the table (#687)", () => {
    const variant = makeMockVariant({
      clinvar_significance: "Pathogenic/Likely pathogenic",
    })
    render(<ResultsTable items={[variant]} selectedRsid={null} onSelect={vi.fn()} />)

    const significanceCell = screen.getByText("Pathogenic/Likely pathogenic").closest("td")
    if (!significanceCell) throw new Error("Expected ClinVar significance cell")
    expect(significanceCell).toHaveClass("text-red-600")
  })
})

describe("SearchSummary", () => {
  it("renders stats and export buttons", () => {
    render(
      <SearchSummary
        total={42}
        totalScanned={600000}
        novelCount={5}
        pathogenicCount={3}
        genesWithFindings={["BRCA1", "TP53"]}
        sampleId={1}
      />,
    )
    expect(screen.getByTestId("total-found")).toHaveTextContent("42")
    expect(screen.getByText("600,000")).toBeInTheDocument()
    expect(screen.getByTestId("export-tsv")).toHaveAttribute(
      "href",
      "/api/analysis/rare-variants/export/tsv?sample_id=1",
    )
    expect(screen.getByTestId("export-vcf")).toHaveAttribute(
      "href",
      "/api/analysis/rare-variants/export/vcf?sample_id=1",
    )
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    expect(screen.getByText("TP53")).toBeInTheDocument()
  })
})

describe("VariantDetailPanel", () => {
  it("renders variant details", () => {
    const variant = makeMockVariant()
    render(<VariantDetailPanel variant={variant} onClose={vi.fn()} />)
    expect(screen.getByTestId("variant-detail-panel")).toBeInTheDocument()
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    expect(screen.getByText("rs12345")).toBeInTheDocument()
    expect(screen.getByText("Pathogenic")).toBeInTheDocument()
    expect(screen.getByText("28.5")).toBeInTheDocument()
    expect(screen.getByText("VCV000012345")).toBeInTheDocument()
    // het default → "Heterozygous" (the zygosity label was previously unasserted)
    expect(screen.getByText("Heterozygous")).toBeInTheDocument()
  })

  it("renders the homozygous zygosity label", () => {
    const variant = makeMockVariant({ zygosity: "hom_alt" })
    render(<VariantDetailPanel variant={variant} onClose={vi.fn()} />)
    expect(screen.getByText("Homozygous")).toBeInTheDocument()
    expect(screen.queryByText("Heterozygous")).not.toBeInTheDocument()
  })

  it("calls onClose when close button clicked", async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<VariantDetailPanel variant={makeMockVariant()} onClose={onClose} />)
    await user.click(screen.getByLabelText("Close panel"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("shows novel variant state when no gnomAD data", () => {
    const variant = makeMockVariant({
      gnomad_af_global: null,
      gnomad_af_afr: null,
      gnomad_af_eur: null,
    })
    render(<VariantDetailPanel variant={variant} onClose={vi.fn()} />)
    expect(screen.getByText("Not found in gnomAD (novel variant)")).toBeInTheDocument()
  })

  it("shows evidence conflict warning", () => {
    const variant = makeMockVariant({ evidence_conflict: true })
    render(<VariantDetailPanel variant={variant} onClose={vi.fn()} />)
    expect(screen.getByText(/Evidence conflict detected/)).toBeInTheDocument()
  })
})

function createWrapper(initialEntries: string[] = ["/"]) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe("RareVariantsView", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch)
    mockFetch.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows empty state when no sample selected", () => {
    render(<RareVariantsView />)
    expect(screen.getByText("Select a sample to search for rare variants.")).toBeInTheDocument()
  })

  it("shows loading state when fetching findings", () => {
    // Never-resolving fetch to keep loading
    mockFetch.mockImplementation(() => new Promise(() => {}))
    rtlRender(<RareVariantsView />, {
      wrapper: createWrapper(["/?sample_id=1"]),
    })
    expect(screen.getByText("Rare Variant Finder")).toBeInTheDocument()
  })

  it("shows no-findings empty state when API returns empty", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ items: [], total: 0 }),
      text: () => Promise.resolve(""),
    })
    rtlRender(<RareVariantsView />, {
      wrapper: createWrapper(["/?sample_id=1"]),
    })
    await waitFor(() => {
      expect(screen.getByText("No rare variant findings yet.")).toBeInTheDocument()
    })
  })

  it("shows error state when API fails", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      text: () => Promise.resolve("Internal Server Error"),
    })
    rtlRender(<RareVariantsView />, {
      wrapper: createWrapper(["/?sample_id=1"]),
    })
    await waitFor(() => {
      expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    })
  })

  it("shows stored findings table when findings exist", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          items: [
            {
              rsid: "rs12345",
              gene_symbol: "BRCA1",
              category: "clinvar_pathogenic",
              evidence_level: 4,
              finding_text: "Pathogenic variant in BRCA1",
              zygosity: "het",
              clinvar_significance: "Pathogenic",
              conditions: "Breast cancer",
              detail: {},
            },
          ],
          total: 1,
        }),
      text: () => Promise.resolve(""),
    })
    rtlRender(<RareVariantsView />, {
      wrapper: createWrapper(["/?sample_id=1"]),
    })
    await waitFor(() => {
      expect(screen.getByTestId("findings-table")).toBeInTheDocument()
    })
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    expect(screen.getByText("Previous Findings")).toBeInTheDocument()
  })

  it("renders the 0★ clinvar_pathogenic_low_confidence category with a friendly label, not the raw key (#919)", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          items: [
            {
              rsid: "rs80357906",
              gene_symbol: "BRCA1",
              category: "clinvar_pathogenic_low_confidence",
              evidence_level: 2,
              finding_text: "Carried 0-star ClinVar P/LP variant",
              zygosity: "het",
              clinvar_significance: "Pathogenic",
              conditions: "Hereditary breast cancer",
              detail: {},
            },
          ],
          total: 1,
        }),
      text: () => Promise.resolve(""),
    })
    rtlRender(<RareVariantsView />, {
      wrapper: createWrapper(["/?sample_id=1"]),
    })
    await waitFor(() => {
      expect(screen.getByTestId("findings-table")).toBeInTheDocument()
    })
    // Friendly label, never the raw snake_case dev key.
    expect(screen.getByText("Pathogenic (low confidence)")).toBeInTheDocument()
    expect(screen.queryByText("clinvar pathogenic low confidence")).not.toBeInTheDocument()
    // Cautionary amber pill, not the neutral gray `rare`/fallback tone.
    const pill = screen.getByText("Pathogenic (low confidence)")
    expect(pill.className).toContain("amber")
    expect(pill.className).not.toContain("bg-muted")
  })

  it("renders filter panel with sample selected", () => {
    mockFetch.mockImplementation(() => new Promise(() => {}))
    rtlRender(<RareVariantsView />, {
      wrapper: createWrapper(["/?sample_id=1"]),
    })
    expect(screen.getByTestId("rare-variant-filter-panel")).toBeInTheDocument()
    expect(screen.getByTestId("search-button")).toBeInTheDocument()
  })
})
