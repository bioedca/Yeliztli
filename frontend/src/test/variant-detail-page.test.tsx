import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render as rtlRender, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import VariantDetailPage from "@/pages/VariantDetailPage"
import type { VariantDetail } from "@/types/variant-detail"

/** IGV.js is a browser-only module — stub it out. */
vi.mock("@/components/igv-browser", () => ({
  IgvBrowser: () => <div data-testid="igv-mock">IGV Browser</div>,
}))

vi.mock("@/components/igv-browser/tracks", () => ({
  buildDefaultTracks: () => [],
}))

const mockFetch = vi.fn()

const mockVariant: VariantDetail = {
  rsid: "rs100",
  chrom: "17",
  pos: 43000000,
  ref: "A",
  alt: "G",
  genotype: "AG",
  zygosity: "het",
  gene_symbol: "BRCA1",
  transcript_id: "NM_007294",
  consequence: "missense_variant",
  hgvs_coding: "c.5382insC",
  hgvs_protein: "p.Tyr1853Ter",
  strand: "+",
  exon_number: 11,
  intron_number: null,
  mane_select: true,
  clinvar_significance: "Pathogenic",
  clinvar_review_stars: 3,
  clinvar_accession: "VCV000017694",
  clinvar_conditions: "Hereditary breast and ovarian cancer syndrome",
  gnomad_af_global: 0.0003,
  gnomad_af_afr: 0.0001,
  gnomad_af_amr: 0.0002,
  gnomad_af_eas: null,
  gnomad_af_eur: 0.0004,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  gnomad_homozygous_count: 0,
  rare_flag: true,
  ultra_rare_flag: false,
  cadd_phred: 28.4,
  sift_score: 0.001,
  sift_pred: "D",
  polyphen2_hsvar_score: 0.99,
  polyphen2_hsvar_pred: "probably_damaging",
  revel: 0.85,
  mutpred2: 0.72,
  vest4: 0.88,
  metasvm: 0.95,
  metalr: 0.91,
  gerp_rs: 5.23,
  phylop: 7.41,
  mpc: 2.1,
  primateai: 0.83,
  dbsnp_build: 132,
  dbsnp_rsid_current: null,
  dbsnp_validation: null,
  disease_name: "Hereditary breast cancer",
  disease_id: "MONDO:0003582",
  phenotype_source: "mondo_hpo",
  hpo_terms: null,
  inheritance_pattern: "Autosomal dominant",
  deleterious_count: 4,
  evidence_conflict: true,
  ensemble_pathogenic: true,
  annotation_coverage: 0b111111,
  transcripts: [
    {
      transcript_id: "NM_007294",
      gene_symbol: "BRCA1",
      consequence: "missense_variant",
      hgvs_coding: "c.5382insC",
      hgvs_protein: "p.Tyr1853Ter",
      strand: "+",
      exon_number: 11,
      intron_number: null,
      mane_select: true,
    },
    {
      transcript_id: "NM_007299",
      gene_symbol: "BRCA1",
      consequence: "synonymous_variant",
      hgvs_coding: "c.100A>G",
      hgvs_protein: null,
      strand: "+",
      exon_number: 3,
      intron_number: null,
      mane_select: false,
    },
  ],
  gene_phenotypes: [
    {
      gene_symbol: "BRCA1",
      disease_name: "Hereditary breast and ovarian cancer syndrome",
      disease_id: "OMIM:604370",
      source: "omim",
      hpo_terms: ["HP:0003002", "HP:0002894"],
      inheritance: "Autosomal dominant",
      omim_link: "https://omim.org/entry/604370",
    },
  ],
  evidence_conflict_detail: {
    has_conflict: true,
    clinvar_significance: "Pathogenic",
    clinvar_review_stars: 3,
    clinvar_accession: "VCV000017694",
    deleterious_count: 4,
    total_tools_assessed: 5,
    deleterious_tools: ["SIFT", "PolyPhen-2", "MetaSVM", "REVEL"],
    cadd_phred: 28.4,
    summary:
      "ClinVar classifies this variant as Pathogenic (3-star review). 4 of 5 in-silico tools predict deleterious.",
  },
}

/** Render with route params for /variants/:rsid */
function renderPage(rsid: string, sampleId: number | null = 1) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const path = sampleId != null
    ? `/variants/${rsid}?sample_id=${sampleId}`
    : `/variants/${rsid}`

  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/variants/:rsid" element={<VariantDetailPage />} />
          <Route path="/variants" element={<div>Variant Explorer</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch)
  mockFetch.mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("VariantDetailPage (P2-21a)", () => {
  it("shows loading state initially", () => {
    mockFetch.mockImplementation(() => new Promise(() => {})) // never resolves
    renderPage("rs100")
    expect(screen.getByText("Loading variant detail...")).toBeInTheDocument()
  })

  it("shows error state when API fails", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: false,
      status: 404,
      text: async () => "Not found",
    }))

    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    })
    expect(screen.getByText(/Back to Variant Explorer/)).toBeInTheDocument()
  })

  it("renders header with rsid, gene, and MANE badge", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    // MANE Select appears in both header and overview tab
    expect(screen.getAllByText("MANE Select").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/BRCA1/).length).toBeGreaterThanOrEqual(1)
  })

  it("renders all 6 tabs", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument()
    })
    expect(screen.getByRole("tab", { name: /population/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /protein/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /clinical/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /literature/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /genome/i })).toBeInTheDocument()
  })

  it("shows Overview tab by default", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByTestId("tab-overview")).toBeInTheDocument()
    })
    expect(screen.getByText("AG")).toBeInTheDocument() // genotype
    expect(screen.getByText("het")).toBeInTheDocument() // zygosity
    expect(screen.getAllByText("missense variant").length).toBeGreaterThanOrEqual(1)
  })

  it("shows transcript table when multiple transcripts exist", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByText("NM_007299")).toBeInTheDocument()
    })
  })

  it("switches to Population tab", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /population/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /population/i }))
    expect(screen.getByTestId("tab-population")).toBeInTheDocument()

    // Population bars
    expect(screen.getByTestId("pop-bar-global")).toBeInTheDocument()
    expect(screen.getByTestId("pop-bar-afr")).toBeInTheDocument()
    expect(screen.getByTestId("pop-bar-eur")).toBeInTheDocument()

    // Per-population AF must render as a raw FRACTION (the shared
    // formatAlleleFrequency unit), not a percentage — matching the other
    // per-population views (#664). Existence alone was blind to the unit:
    // under the old formatPercent, eur=0.0004 rendered "0.0400%".
    expect(screen.getByTestId("pop-bar-eur")).toHaveTextContent("0.0004")
    expect(screen.getByTestId("pop-bar-global")).toHaveTextContent("0.0003")
    expect(screen.getByTestId("pop-bar-eur")).not.toHaveTextContent("%")
    expect(screen.getByTestId("pop-bar-afr")).not.toHaveTextContent("%")
  })

  it("shows rare variant note in Population tab", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /population/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /population/i }))

    await waitFor(() => {
      expect(screen.getByText(/This variant is rare/)).toBeInTheDocument()
    })
  })

  it("shows Protein tab with link to gene detail page", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /protein/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /protein/i }))
    expect(screen.getByTestId("tab-protein")).toBeInTheDocument()
    expect(screen.getByText("p.Tyr1853Ter")).toBeInTheDocument()
    expect(screen.getByText(/View full gene detail/)).toBeInTheDocument()
  })

  it("switches to Clinical tab with full ClinVar record", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /clinical/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /clinical/i }))
    expect(screen.getByTestId("tab-clinical")).toBeInTheDocument()

    // ClinVar accession link
    expect(screen.getByText("VCV000017694")).toBeInTheDocument()
    // All in-silico scores
    expect(screen.getByText("28.4")).toBeInTheDocument() // CADD
    expect(screen.getByText("0.850")).toBeInTheDocument() // REVEL
  })

  it("shows disease associations in Clinical tab", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /clinical/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /clinical/i }))

    // Disease association appears in both ClinVar conditions and disease list
    expect(screen.getAllByText("Hereditary breast and ovarian cancer syndrome").length).toBeGreaterThanOrEqual(1)
    // HPO terms
    expect(screen.getByText("HP:0003002")).toBeInTheDocument()
  })

  it("shows Literature stub tab", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /literature/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /literature/i }))
    expect(screen.getByTestId("tab-literature")).toBeInTheDocument()
    expect(screen.getByText(/Phase 3/)).toBeInTheDocument()
  })

  it("switches to Genome tab with IGV browser", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /genome/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /genome/i }))
    expect(screen.getByTestId("tab-genome")).toBeInTheDocument()
    expect(screen.getByTestId("igv-mock")).toBeInTheDocument()
    expect(screen.getByText(/Open full browser/)).toBeInTheDocument()
  })

  it("has back link to Variant Explorer", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100", 1)

    await waitFor(() => {
      expect(screen.getByLabelText("Back to Variant Explorer")).toBeInTheDocument()
    })
    expect(screen.getByLabelText("Back to Variant Explorer")).toHaveAttribute(
      "href",
      "/variants?sample_id=1",
    )
  })

  it("shows evidence conflict in overview tab", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByText("Evidence Conflict")).toBeInTheDocument()
    })
    expect(screen.getByText(/4 of 5 in-silico tools predict deleterious/)).toBeInTheDocument()
  })

  it("shows ensemble pathogenic indicator in overview", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByText(/Ensemble pathogenic/)).toBeInTheDocument()
    })
  })

  it("tabs have proper ARIA attributes", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariant,
    }))

    renderPage("rs100")

    await waitFor(() => {
      const overviewTab = screen.getByRole("tab", { name: /overview/i })
      expect(overviewTab).toHaveAttribute("aria-selected", "true")
    })

    const populationTab = screen.getByRole("tab", { name: /population/i })
    expect(populationTab).toHaveAttribute("aria-selected", "false")
  })

  it("handles variant with no population data", async () => {
    const noPopVariant: VariantDetail = {
      ...mockVariant,
      gnomad_af_global: null,
      gnomad_af_afr: null,
      gnomad_af_amr: null,
      gnomad_af_eas: null,
      gnomad_af_eur: null,
      gnomad_af_fin: null,
      gnomad_af_sas: null,
      gnomad_homozygous_count: null,
      rare_flag: false,
      ultra_rare_flag: false,
    }

    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => noPopVariant,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /population/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /population/i }))
    expect(screen.getByText(/No population frequency data/)).toBeInTheDocument()
  })

  it("handles variant with no ClinVar data in Clinical tab", async () => {
    const noClinVar: VariantDetail = {
      ...mockVariant,
      clinvar_significance: null,
      clinvar_review_stars: null,
      clinvar_accession: null,
      clinvar_conditions: null,
      evidence_conflict_detail: null,
    }

    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => noClinVar,
    }))

    const user = userEvent.setup()
    renderPage("rs100")

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /clinical/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("tab", { name: /clinical/i }))
    expect(screen.getByText("No ClinVar record for this variant.")).toBeInTheDocument()
  })
})
