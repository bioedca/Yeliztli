/**
 * Tests for the Gene Detail page UI (P3-42, T3-45).
 *
 * Verifies: Nightingale protein viewer rendering, variant table,
 * population AF chart, phenotypes, literature, navigation links.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render as rtlRender, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import GeneDetailPage from "@/pages/GeneDetailPage"
import { useGeneDetail } from "@/api/gene-detail"
import { ThemeProvider } from "@/lib/ThemeContext"
import { NightingaleViewer } from "@/components/gene-detail"
import { PopulationAFChart } from "@/components/gene-detail"
import type {
  GeneDetailResponse,
  ProteinDomain,
  ProteinFeature,
  GeneVariantSummary,
  PopulationAFSummary,
} from "@/types/gene-detail"

// ── Mock Nightingale custom elements (Web Components not available in jsdom) ──

beforeEach(() => {
  mockUseGeneDetail.mockReset()

  // Register mock custom elements if not already defined
  for (const tag of [
    "nightingale-manager",
    "nightingale-navigation",
    "nightingale-sequence",
    "nightingale-track",
  ]) {
    if (!customElements.get(tag)) {
      customElements.define(
        tag,
        class extends HTMLElement {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          private _data: any[] = []
          get data() {
            return this._data
          }
          set data(val) {
            this._data = val
          }
        },
      )
    }
  }
})

// Mock react-plotly.js to avoid canvas issues in jsdom
vi.mock("react-plotly.js", () => ({
  default: (props: Record<string, unknown>) => (
    <div data-testid="plotly-chart" data-props={JSON.stringify(props)} />
  ),
}))

vi.mock("@/api/gene-detail", () => ({
  useGeneDetail: vi.fn(),
}))

const mockUseGeneDetail = vi.mocked(useGeneDetail)

// ── Fixtures ──────────────────────────────────────────────────────────

const BRCA1_DOMAINS: ProteinDomain[] = [
  { type: "Domain", description: "RING-type", start: 1, end: 109 },
  { type: "Domain", description: "BRCT 1", start: 1642, end: 1736 },
  { type: "Domain", description: "BRCT 2", start: 1756, end: 1855 },
]

const BRCA1_FEATURES: ProteinFeature[] = [
  { type: "Active site", description: "Zinc-binding", position: 39, start: 39, end: 39 },
  { type: "Binding site", description: "DNA-binding", position: 65, start: 65, end: 65 },
]

const BRCA1_VARIANTS: GeneVariantSummary[] = [
  {
    rsid: "rs80357906",
    chrom: "17",
    pos: 41245466,
    genotype: "C/T",
    consequence: "frameshift_variant",
    hgvs_protein: "p.Gln1756Profs*74",
    hgvs_coding: "c.5266dupC",
    clinvar_significance: "Pathogenic",
    clinvar_review_stars: 3,
    gnomad_af_global: 0.000003,
    cadd_phred: 38.4,
    evidence_conflict: false,
    annotation_coverage: 63,
  },
  {
    rsid: "rs1799950",
    chrom: "17",
    pos: 41243190,
    genotype: "A/G",
    consequence: "missense_variant",
    hgvs_protein: "p.Arg356His",
    hgvs_coding: "c.1067G>A",
    clinvar_significance: "Uncertain significance",
    clinvar_review_stars: 1,
    gnomad_af_global: 0.0012,
    cadd_phred: 22.1,
    evidence_conflict: true,
    annotation_coverage: 63,
  },
  {
    rsid: "rs16942",
    chrom: "17",
    pos: 41244000,
    genotype: "G/G",
    consequence: "synonymous_variant",
    hgvs_protein: null,
    hgvs_coding: null,
    clinvar_significance: "Benign",
    clinvar_review_stars: 2,
    gnomad_af_global: 0.42,
    cadd_phred: 0.5,
    evidence_conflict: false,
    annotation_coverage: 63,
  },
]

const BRCA1_AF_DATA: PopulationAFSummary[] = [
  {
    rsid: "rs80357906",
    hgvs_protein: "p.Gln1756Profs*74",
    gnomad_af_global: 0.000003,
    gnomad_af_afr: 0.000001,
    gnomad_af_amr: 0.000002,
    gnomad_af_asj: 0.000004,
    gnomad_af_eas: 0.0,
    gnomad_af_eur: 0.000005,
    gnomad_af_fin: 0.0,
    gnomad_af_sas: 0.0,
  },
]

function mockGeneDetail(data: GeneDetailResponse) {
  mockUseGeneDetail.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useGeneDetail>)
}

function renderGeneDetailPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/genes/BRCA1?sample_id=1"]}>
          <Routes>
            <Route path="/genes/:symbol" element={<GeneDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

// ── GeneDetailPage tests ──────────────────────────────────────────

describe("GeneDetailPage", () => {
  it("applies ClinVar severity text colors in the variants table", () => {
    mockGeneDetail({
      gene_symbol: "BRCA1",
      uniprot: null,
      uniprot_error: null,
      phenotypes: [],
      literature: [],
      literature_errors: [],
      population_af: [],
      variants: [
        BRCA1_VARIANTS[0],
        BRCA1_VARIANTS[2],
        {
          ...BRCA1_VARIANTS[1],
          rsid: "rs-conflicting",
          clinvar_significance: "Conflicting interpretations of pathogenicity",
        },
      ],
    })

    renderGeneDetailPage()

    expect(mockUseGeneDetail).toHaveBeenCalledWith("BRCA1", 1)
    const table = screen.getByTestId("gene-variants-table")

    expect(within(table).getByText("Pathogenic")).toHaveClass("text-red-600")
    expect(within(table).getByText("Benign")).toHaveClass("text-green-600")
    expect(
      within(table).getByText("Conflicting interpretations of pathogenicity"),
    ).toHaveClass("text-yellow-600")
  })

  it("shows UniProt unavailable errors only in the protein structure placeholder", () => {
    const uniprotError = "Protein data unavailable offline."

    mockGeneDetail({
      gene_symbol: "CFTR",
      uniprot: null,
      uniprot_error: uniprotError,
      phenotypes: [],
      literature: [],
      literature_errors: [],
      population_af: [],
      variants: [],
    })

    renderGeneDetailPage()

    expect(screen.getByTestId("gene-symbol")).toHaveTextContent("CFTR")
    expect(screen.getAllByText(uniprotError)).toHaveLength(1)

    const proteinPlaceholder = screen.getByTestId("protein-structure-placeholder")
    expect(proteinPlaceholder).toHaveTextContent(uniprotError)
  })
})

// ── NightingaleViewer tests ──────────────────────────────────────────

describe("NightingaleViewer", () => {
  it("renders Nightingale components with domain and variant tracks", () => {
    render(
      <NightingaleViewer
        sequenceLength={1863}
        domains={BRCA1_DOMAINS}
        features={BRCA1_FEATURES}
        variants={BRCA1_VARIANTS}
        accession="P38398"
      />,
    )

    expect(screen.getByTestId("nightingale-viewer")).toBeInTheDocument()
    // Verify Nightingale custom elements are mounted
    expect(document.querySelector("nightingale-manager")).toBeInTheDocument()
    expect(document.querySelector("nightingale-navigation")).toBeInTheDocument()
    // Domain and variant tracks rendered
    const tracks = document.querySelectorAll("nightingale-track")
    expect(tracks.length).toBeGreaterThanOrEqual(2) // domain + variant tracks
  })

  it("renders variant legend with color indicators", () => {
    render(
      <NightingaleViewer
        sequenceLength={1863}
        domains={BRCA1_DOMAINS}
        features={BRCA1_FEATURES}
        variants={BRCA1_VARIANTS}
        accession="P38398"
      />,
    )

    // Legend contains all significance categories
    expect(screen.getAllByText("Pathogenic").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("Likely Pathogenic")).toBeInTheDocument()
    expect(screen.getByText("VUS")).toBeInTheDocument()
    expect(screen.getByText("Benign")).toBeInTheDocument()
  })

  it("shows mapped variants list for variants with HGVS protein", () => {
    render(
      <NightingaleViewer
        sequenceLength={1863}
        domains={BRCA1_DOMAINS}
        features={BRCA1_FEATURES}
        variants={BRCA1_VARIANTS}
        accession="P38398"
      />,
    )

    // Variants with hgvs_protein should appear in the mapped list
    expect(screen.getByText("rs80357906")).toBeInTheDocument()
    expect(screen.getByText("rs1799950")).toBeInTheDocument()
    // Variant without hgvs_protein (rs16942) should NOT appear in mapped list
    expect(screen.queryByText("rs16942")).not.toBeInTheDocument()
  })

  it("calls onVariantClick when a mapped variant is clicked", async () => {
    const user = userEvent.setup()
    const onVariantClick = vi.fn()

    render(
      <NightingaleViewer
        sequenceLength={1863}
        domains={BRCA1_DOMAINS}
        features={BRCA1_FEATURES}
        variants={BRCA1_VARIANTS}
        accession="P38398"
        onVariantClick={onVariantClick}
      />,
    )

    await user.click(screen.getByText("rs80357906"))
    expect(onVariantClick).toHaveBeenCalledWith(
      expect.objectContaining({ rsid: "rs80357906" }),
    )
  })

  it("shows empty state when sequence length is 0", () => {
    render(
      <NightingaleViewer
        sequenceLength={0}
        domains={[]}
        features={[]}
        variants={[]}
        accession=""
      />,
    )

    expect(screen.getByText(/No protein sequence data available/)).toBeInTheDocument()
  })

  it("sets data on Nightingale track refs via useEffect", () => {
    render(
      <NightingaleViewer
        sequenceLength={1863}
        domains={BRCA1_DOMAINS}
        features={BRCA1_FEATURES}
        variants={BRCA1_VARIANTS}
        accession="P38398"
      />,
    )

    // Verify domain track received data
    const tracks = document.querySelectorAll("nightingale-track")
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const domainTrack = tracks[0] as any
    expect(domainTrack.data).toBeDefined()
    expect(domainTrack.data.length).toBe(3)
  })
})

// ── PopulationAFChart tests ──────────────────────────────────────────

describe("PopulationAFChart", () => {
  it("renders Plotly chart with population data", () => {
    render(<PopulationAFChart data={BRCA1_AF_DATA} />)

    expect(screen.getByTestId("population-af-chart")).toBeInTheDocument()
    expect(screen.getByTestId("plotly-chart")).toBeInTheDocument()
    expect(screen.getByText("rs80357906")).toBeInTheDocument()
    expect(screen.getByTestId("plotly-chart").getAttribute("data-props")).toContain(
      "Ashkenazi Jewish",
    )
  })

  it("shows empty state when no data", () => {
    render(<PopulationAFChart data={[]} />)

    expect(screen.getByText(/No population frequency data/)).toBeInTheDocument()
  })

  it("shows selected variant when specified", () => {
    const data: PopulationAFSummary[] = [
      ...BRCA1_AF_DATA,
      {
        rsid: "rs1799950",
        hgvs_protein: "p.Arg356His",
        gnomad_af_global: 0.0012,
        gnomad_af_afr: 0.001,
        gnomad_af_amr: 0.0015,
        gnomad_af_asj: 0.0017,
        gnomad_af_eas: 0.0008,
        gnomad_af_eur: 0.0014,
        gnomad_af_fin: 0.002,
        gnomad_af_sas: 0.0009,
      },
    ]

    render(<PopulationAFChart data={data} selectedVariant="rs1799950" />)

    expect(screen.getByText("rs1799950")).toBeInTheDocument()
    expect(screen.getByText("2 variants with population frequency data")).toBeInTheDocument()
  })
})
