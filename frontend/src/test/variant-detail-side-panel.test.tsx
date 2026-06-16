import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "./test-utils"
import userEvent from "@testing-library/user-event"
import VariantTable from "@/components/variant-table/VariantTable"
import VariantDetailSidePanel from "@/components/variant-detail/VariantDetailSidePanel"
import type { VariantPage, ChromosomeSummary, ColumnPreset } from "@/types/variants"
import type { VariantDetail } from "@/types/variant-detail"

const mockFetch = vi.fn()

const defaultPresets: ColumnPreset[] = [
  {
    name: "Clinical",
    columns: ["genotype", "gene_symbol", "consequence", "clinvar_significance", "clinvar_review_stars"],
    predefined: true,
  },
]

function makeVariantPage(count: number): VariantPage {
  return {
    items: Array.from({ length: count }, (_, i) => ({
      rsid: `rs${100 + i}`,
      chrom: "17",
      pos: 43000000 + i * 100,
      genotype: "AG",
      ref: "A",
      alt: "G",
      zygosity: "het",
      gene_symbol: "BRCA1",
      consequence: "missense_variant",
      clinvar_significance: "Pathogenic",
      clinvar_review_stars: 3,
      gnomad_af_global: 0.0003,
      rare_flag: true,
      cadd_phred: 28.4,
      sift_score: 0.001,
      sift_pred: "D",
      polyphen2_hsvar_score: 0.99,
      polyphen2_hsvar_pred: "D",
      revel: 0.85,
      annotation_coverage: 0b111111,
      evidence_conflict: i === 0,
      ensemble_pathogenic: i === 0,
      chrom_grch38: null,
      pos_grch38: null,
    })),
    next_cursor_chrom: null,
    next_cursor_pos: null,
    has_more: false,
    limit: 100,
  }
}

const mockVariantDetail: VariantDetail = {
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
  mutpred2: null,
  vest4: null,
  metasvm: null,
  metalr: null,
  gerp_rs: null,
  phylop: null,
  mpc: null,
  primateai: null,
  dbsnp_build: null,
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
  ],
  gene_phenotypes: [
    {
      gene_symbol: "BRCA1",
      disease_name: "Hereditary breast and ovarian cancer syndrome",
      disease_id: "OMIM:604370",
      source: "omim",
      hpo_terms: null,
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
      "ClinVar classifies this variant as Pathogenic (3-star review). 4 of 5 in-silico tools predict deleterious (CADD: 28.4). This may reflect a variant under active clinical investigation.",
  },
}

const defaultChromCounts: ChromosomeSummary[] = [
  { chrom: "17", count: 5000 },
]

function setupFetchMock(page: VariantPage, detail: VariantDetail = mockVariantDetail) {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/api/column-presets")) {
      return { ok: true, json: async () => ({ presets: defaultPresets }) }
    }
    if (url.includes("/api/variants/chromosomes")) {
      return { ok: true, json: async () => defaultChromCounts }
    }
    if (url.includes("/api/variants/count")) {
      return { ok: true, json: async () => ({ total: page.items.length, filtered: false }) }
    }
    // Variant detail endpoint: /api/variants/{rsid}?sample_id=...
    if (url.match(/\/api\/variants\/rs\d+\?/)) {
      return { ok: true, json: async () => detail }
    }
    if (url.includes("/api/variants")) {
      return { ok: true, json: async () => page }
    }
    return { ok: false, status: 404, text: async () => "Not found" }
  })
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch)
  mockFetch.mockReset()
  window.history.replaceState({}, "", window.location.pathname)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("VariantDetailSidePanel (P2-21)", () => {
  it("does not render when rsid is null", () => {
    render(
      <VariantDetailSidePanel rsid={null} sampleId={1} onClose={() => {}} />,
    )
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("renders variant detail after loading", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    // Should show loading first
    expect(screen.getByText("Loading variant detail...")).toBeInTheDocument()

    // Then show the variant data
    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Check key fields are displayed
    expect(screen.getByText(/BRCA1/)).toBeInTheDocument()
    expect(screen.getByText("Pathogenic")).toBeInTheDocument()
    expect(screen.getByText("missense variant")).toBeInTheDocument()
    expect(screen.getByText("MANE Select")).toBeInTheDocument()
  })

  it("maps the single-char PolyPhen-2 code 'D' to 'Probably Damaging' in the damaging colour (#680)", async () => {
    // The backend stores the raw dbNSFP code ("D"), not "probably_damaging";
    // pre-fix the full-word check never matched, so a probably-damaging call
    // rendered as a bare "D" in the benign green colour.
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    const polyphen = screen.getByText(/Probably Damaging/)
    expect(polyphen).toHaveClass("text-red-700")
    // The raw single-letter code must not leak through as the label.
    expect(polyphen.textContent).not.toBe("D")
  })

  it("shows evidence conflict section when conflict exists", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByText("Evidence Conflict")).toBeInTheDocument()
    })
    expect(screen.getByText(/4 of 5 in-silico tools predict deleterious/)).toBeInTheDocument()
  })

  it("shows gene-phenotype associations", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(
        screen.getAllByText("Hereditary breast and ovarian cancer syndrome").length,
      ).toBeGreaterThanOrEqual(1)
    })
    // Check OMIM label appears in gene-phenotype section
    expect(screen.getAllByText(/OMIM/).length).toBeGreaterThanOrEqual(1)
  })

  it("has 'Open full detail' link pointing to correct URL", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      const link = screen.getByRole("link", { name: /open full detail/i })
      expect(link).toHaveAttribute("href", "/variants/rs100?sample_id=1")
    })
  })

  it("calls onClose when escape is pressed", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    const onClose = vi.fn()
    const user = userEvent.setup()

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={onClose} />,
    )

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    })

    await user.keyboard("{Escape}")
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("calls onClose when close button is clicked", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    const onClose = vi.fn()
    const user = userEvent.setup()

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={onClose} />,
    )

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    await user.click(screen.getByLabelText("Close variant detail panel"))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("shows error state when API call fails", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: false,
      status: 404,
      text: async () => "Variant not found",
    }))

    render(
      <VariantDetailSidePanel rsid="rs999" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByText("Failed to load variant detail")).toBeInTheDocument()
    })
  })

  it("shows ClinVar review stars in header", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      // 3-star = ★★★☆
      expect(screen.getAllByText("\u2605\u2605\u2605\u2606").length).toBeGreaterThan(0)
    })
  })

  it("shows 'Rare' badge when variant is rare", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByText("Rare")).toBeInTheDocument()
    })
  })

  it("calls onClose when clicking the overlay", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    const onClose = vi.fn()
    const user = userEvent.setup()

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={onClose} />,
    )

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    })

    // Click the overlay (outside the panel)
    const overlay = screen.getByLabelText("Close panel")
    await user.click(overlay)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("shows ensemble pathogenic indicator", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: true,
      json: async () => mockVariantDetail,
    }))

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByText(/Ensemble pathogenic/)).toBeInTheDocument()
    })
  })
})

describe("VariantTable row click → side panel (P2-21)", () => {
  it("opens side panel when a variant row is clicked", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page)

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Click the first row
    await user.click(screen.getByText("rs100"))

    // Side panel should open with the variant detail
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    })
  })

  it("highlights the selected row", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page)

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    const row = screen.getByText("rs100").closest("tr")
    await user.click(row!)

    // Row should have the selected class
    expect(row).toHaveClass("bg-accent")
  })

  it("closes side panel when clicking a different row", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page)

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Click first row
    await user.click(screen.getByText("rs100"))

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    })

    // Click second row — side panel should update (still open)
    await user.click(screen.getByText("rs101"))

    // The dialog should still be present (with new rsid)
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    })
  })

  it("rows have cursor-pointer class for clickability", async () => {
    const page = makeVariantPage(1)
    setupFetchMock(page)

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    const row = screen.getByText("rs100").closest("tr")
    expect(row).toHaveClass("cursor-pointer")
  })
})
