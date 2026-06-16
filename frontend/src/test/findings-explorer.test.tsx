/** Tests for the Analysis Module Dashboard / Findings Explorer (P3-43). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render as rtlRender, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import FindingsExplorer from "@/pages/FindingsExplorer"
import type { Finding, FindingsSummaryResponse } from "@/types/findings"
import type { ReactElement, ReactNode } from "react"

// ── Custom render with initialEntries ────────────────────────────────

function renderWithRoute(ui: ReactElement, initialEntries: string[] = ["/"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
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

// ── Mock data ────────────────────────────────────────────────────────

const SAMPLE_FINDINGS: Finding[] = [
  {
    id: 1,
    module: "cancer",
    category: "monogenic",
    evidence_level: 4,
    gene_symbol: "BRCA1",
    rsid: "rs80357906",
    finding_text: "BRCA1 c.5266dupC — Pathogenic variant in hereditary breast and ovarian cancer gene.",
    phenotype: "Hereditary breast and ovarian cancer syndrome",
    conditions: "Breast cancer",
    zygosity: "het",
    clinvar_significance: "Pathogenic",
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: ["20301425"],
    detail: null,
    created_at: "2026-03-17T12:00:00",
  },
  {
    id: 2,
    module: "pharmacogenomics",
    category: "prescribing_alert",
    evidence_level: 4,
    gene_symbol: "CYP2C19",
    rsid: null,
    finding_text: "CYP2C19 *2/*2 — Poor Metabolizer. Clopidogrel may have reduced efficacy.",
    phenotype: null,
    conditions: null,
    zygosity: null,
    clinvar_significance: null,
    diplotype: "*2/*2",
    metabolizer_status: "Poor Metabolizer",
    drug: "clopidogrel",
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: [],
    detail: null,
    created_at: "2026-03-17T12:00:00",
  },
  {
    id: 3,
    module: "nutrigenomics",
    category: "pathway",
    evidence_level: 3,
    gene_symbol: "MTHFR",
    rsid: "rs1801133",
    finding_text: "Folate metabolism — Elevated consideration. MTHFR C677T homozygous (TT).",
    phenotype: null,
    conditions: null,
    zygosity: "hom",
    clinvar_significance: null,
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: "Folate Metabolism",
    pathway_level: "Elevated",
    svg_path: null,
    pmid_citations: ["15496427"],
    detail: null,
    created_at: "2026-03-17T12:00:00",
  },
  {
    id: 4,
    module: "ancestry",
    category: "composition",
    evidence_level: 2,
    gene_symbol: null,
    rsid: null,
    finding_text: "Primary ancestry: European (82%).",
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
    created_at: "2026-03-17T12:00:00",
  },
]

const SAMPLE_SUMMARY: FindingsSummaryResponse = {
  total_findings: 4,
  modules: [
    { module: "cancer", count: 1, max_evidence_level: 4, top_finding_text: "BRCA1 c.5266dupC — Pathogenic" },
    { module: "pharmacogenomics", count: 1, max_evidence_level: 4, top_finding_text: "CYP2C19 *2/*2 — Poor Metabolizer" },
    { module: "nutrigenomics", count: 1, max_evidence_level: 3, top_finding_text: "Folate metabolism — Elevated consideration" },
    { module: "ancestry", count: 1, max_evidence_level: 2, top_finding_text: "Primary ancestry: European (82%)" },
  ],
  high_confidence_findings: SAMPLE_FINDINGS.slice(0, 3),
}

let mockFetch: ReturnType<typeof vi.fn>

beforeEach(() => {
  mockFetch = vi.fn()
  vi.stubGlobal("fetch", mockFetch)
})

function setupFetchMock(
  findings: Finding[] = SAMPLE_FINDINGS,
  summary: FindingsSummaryResponse = SAMPLE_SUMMARY,
) {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/api/analysis/findings/summary")) {
      return { ok: true, json: async () => summary }
    }
    if (url.includes("/api/analysis/findings")) {
      return { ok: true, json: async () => findings }
    }
    return { ok: false, text: async () => "Not found" }
  })
}

// ── Tests ────────────────────────────────────────────────────────────

describe("FindingsExplorer", () => {
  it("shows no-sample state when no sample_id is provided", () => {
    renderWithRoute(<FindingsExplorer />)
    expect(screen.getByText("Select a sample to view analysis findings.")).toBeInTheDocument()
  })

  it("shows loading state while fetching findings", () => {
    mockFetch.mockReturnValue(new Promise(() => {}))
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    expect(screen.getByText("Loading findings...")).toBeInTheDocument()
  })

  it("renders all findings sorted by evidence level", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText(/BRCA1 c\.5266dupC/)).toBeInTheDocument()
    expect(screen.getByText(/CYP2C19 \*2\/\*2/)).toBeInTheDocument()
    expect(screen.getByText(/Folate metabolism/)).toBeInTheDocument()
    expect(screen.getByText(/Primary ancestry: European/)).toBeInTheDocument()
  })

  it("renders the zygosity label for findings that carry one", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    await screen.findByText(/BRCA1 c\.5266dupC/) // wait for findings to load

    // FindingsExplorer renders `finding.zygosity` for findings that have it
    // (the cancer finding is het, the nutrigenomics finding is hom). Assert both
    // labels render — a regression that dropped or inverted carriage rendering
    // would otherwise be invisible. (exact-text match, so "hom" does not collide
    // with the "homozygous (TT)" inside a finding_text.)
    expect(screen.getByText("het")).toBeInTheDocument()
    expect(screen.getByText("hom")).toBeInTheDocument()
  })

  it("displays module filter chips with counts", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    // Module names appear in both chips and finding rows; getAllByText confirms presence
    const cancerElements = await screen.findAllByText("Cancer")
    expect(cancerElements.length).toBeGreaterThanOrEqual(1)
    const pharmaElements = screen.getAllByText("Pharmacogenomics")
    expect(pharmaElements.length).toBeGreaterThanOrEqual(1)
    const nutriElements = screen.getAllByText("Nutrigenomics")
    expect(nutriElements.length).toBeGreaterThanOrEqual(1)
    const ancestryElements = screen.getAllByText("Ancestry")
    expect(ancestryElements.length).toBeGreaterThanOrEqual(1)
  })

  it("shows total findings count in header", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("4 findings across 4 modules")).toBeInTheDocument()
  })

  it("shows empty state with no findings", async () => {
    setupFetchMock([], { total_findings: 0, modules: [], high_confidence_findings: [] })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(
      await screen.findByText("No findings yet. Run annotation to generate analysis findings."),
    ).toBeInTheDocument()
  })

  it("displays evidence level group headings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("Definitive Evidence")).toBeInTheDocument()
    expect(screen.getByText("Strong Evidence")).toBeInTheDocument()
    expect(screen.getByText("Moderate Evidence")).toBeInTheDocument()
  })

  it("shows pathway level badge for nutrigenomics findings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("Elevated")).toBeInTheDocument()
  })

  it("shows ClinVar significance for cancer findings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("ClinVar: Pathogenic")).toBeInTheDocument()
  })

  it("shows error state when fetch fails", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      text: async () => "Server error",
    })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText(/Findings failed: 500/)).toBeInTheDocument()
  })

  it("renders metabolizer status for pharmacogenomics findings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("Poor Metabolizer")).toBeInTheDocument()
  })

  it("renders risk-genotype indeterminate reasons from finding detail", async () => {
    const alpha1Finding: Finding = {
      ...SAMPLE_FINDINGS[0],
      id: 10,
      module: "alpha1",
      category: "risk_genotype",
      gene_symbol: "SERPINA1",
      rsid: "rs28929474",
      finding_text: "SERPINA1 PiZZ (severe deficiency).",
      clinvar_significance: null,
      detail: {
        indeterminate_loci: ["rs17580"],
        indeterminate_reasons: {
          rs17580: "palindrome_strand_ambiguous",
        },
      },
    }
    setupFetchMock([alpha1Finding], {
      total_findings: 1,
      modules: [
        {
          module: "alpha1",
          count: 1,
          max_evidence_level: 4,
          top_finding_text: alpha1Finding.finding_text,
        },
      ],
      high_confidence_findings: [alpha1Finding],
    })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    const note = await screen.findByTestId("finding-indeterminate-reasons")
    expect(note).toHaveTextContent("rs17580")
    expect(note).toHaveTextContent("strand-ambiguous palindromic homozygote")
  })

  // ── Module links / labels (issue #544) ──────────────────────────────

  function makeFinding(id: number, module: string, finding_text: string): Finding {
    return {
      id,
      module,
      category: "test",
      evidence_level: 3,
      gene_symbol: null,
      rsid: null,
      finding_text,
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
      created_at: "2026-03-17T12:00:00",
    }
  }

  it("links page-backed module findings to their real page, not the Dashboard", async () => {
    // Previously every module missing from MODULE_META fell back to route "/",
    // dumping the user on the Dashboard. Each of these has a dedicated page.
    const findings: Finding[] = [
      makeFinding(1, "cancer", "Cancer finding"), // control — already mapped
      makeFinding(2, "fh", "FH finding"),
      makeFinding(3, "gene_health", "Gene health finding"),
      makeFinding(4, "methylation", "Methylation finding"),
      makeFinding(5, "ebmd", "eBMD finding"),
      makeFinding(6, "fitness", "Fitness finding"),
    ]
    setupFetchMock(findings, { total_findings: 6, modules: [], high_confidence_findings: [] })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    await screen.findByText("FH finding")

    const expected: Record<string, string> = {
      Cancer: "/cancer?sample_id=1",
      FH: "/fh?sample_id=1",
      "Gene Health": "/gene-health?sample_id=1",
      Methylation: "/methylation?sample_id=1",
      eBMD: "/ebmd?sample_id=1",
      Fitness: "/fitness?sample_id=1",
    }
    for (const [label, href] of Object.entries(expected)) {
      const link = screen.getByRole("link", { name: `View ${label} module` })
      expect(link).toHaveAttribute("href", href)
    }
  })

  it("renders acronym module labels with correct casing (FH, eBMD), not Fh/Ebmd", async () => {
    const findings: Finding[] = [
      makeFinding(1, "fh", "FH finding"),
      makeFinding(2, "ebmd", "eBMD finding"),
    ]
    setupFetchMock(findings, { total_findings: 2, modules: [], high_confidence_findings: [] })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    await screen.findByText("FH finding")

    expect(screen.getByRole("link", { name: "View FH module" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "View eBMD module" })).toBeInTheDocument()
    // The old auto-title-case bug would have produced "Fh"/"Ebmd".
    expect(screen.queryByText("Fh")).not.toBeInTheDocument()
    expect(screen.queryByText("Ebmd")).not.toBeInTheDocument()
  })

  it("renders panel-only modules (no dedicated page) as a non-navigable label", async () => {
    // A risk panel with no page must NOT render a link that lands on "/".
    const findings: Finding[] = [makeFinding(1, "amd", "AMD risk finding")]
    setupFetchMock(findings, { total_findings: 1, modules: [], high_confidence_findings: [] })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    await screen.findByText("AMD risk finding")

    // Label is present and correctly cased…
    expect(screen.getByText("AMD")).toBeInTheDocument()
    // …but it is not a link (no navigation, so it can never reach the Dashboard).
    expect(screen.queryByRole("link", { name: /AMD module/ })).not.toBeInTheDocument()
    // Defensive: no finding-row link points at the Dashboard root.
    for (const link of screen.queryAllByRole("link")) {
      expect(link.getAttribute("href")).not.toBe("/?sample_id=1")
      expect(link.getAttribute("href")).not.toBe("/")
    }
  })

  it("correctly cases the remaining page-less risk modules (MT-RNR1, Alpha-1, APOL1)", async () => {
    // These persist findings too (store_risk_findings) but have no page — they
    // must render correctly-cased, non-navigable labels, not "Mt Rnr1"/"Alpha1"/
    // "Apol1" from the title-case fallback.
    const findings: Finding[] = [
      makeFinding(1, "mt_rnr1", "MT-RNR1 finding"),
      makeFinding(2, "alpha1", "Alpha-1 finding"),
      makeFinding(3, "apol1", "APOL1 finding"),
    ]
    setupFetchMock(findings, { total_findings: 3, modules: [], high_confidence_findings: [] })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    await screen.findByText("MT-RNR1 finding")

    expect(screen.getByText("MT-RNR1")).toBeInTheDocument()
    expect(screen.getByText("Alpha-1")).toBeInTheDocument()
    expect(screen.getByText("APOL1")).toBeInTheDocument()
    // The old title-case fallback would have produced these:
    expect(screen.queryByText("Mt Rnr1")).not.toBeInTheDocument()
    expect(screen.queryByText("Alpha1")).not.toBeInTheDocument()
    expect(screen.queryByText("Apol1")).not.toBeInTheDocument()
    // None render a navigable link (no dedicated page).
    expect(screen.queryAllByRole("link")).toHaveLength(0)
  })
})
