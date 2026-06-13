/** Tests for the Ancestry UI (P3-27, P3-34, AMv2 Steps 5-6). */

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "./test-utils"
import AncestryResultCard from "@/components/ancestry/AncestryResultCard"
import AdmixtureBar from "@/components/ancestry/AdmixtureBar"
import PCAScatter from "@/components/ancestry/PCAScatter"
import HaplogroupCard from "@/components/ancestry/HaplogroupCard"
import AnalysisDetails from "@/components/ancestry/AnalysisDetails"
import ChromosomePainting from "@/components/charts/ChromosomePainting"
import AncestryPieChart from "@/components/charts/AncestryPieChart"
import type {
  AncestryFindingResponse,
  ChromosomePaintingSegment,
  HaplogroupAssignment,
  LAIGlobalAncestryEntry,
  PCACoordinatesResponse,
} from "@/types/ancestry"

// Mock react-plotly.js to avoid canvas issues in tests
vi.mock("react-plotly.js", () => ({
  default: (props: { data: unknown[]; "data-testid"?: string }) => (
    <div data-testid="plotly-chart" data-traces={JSON.stringify(props.data)} />
  ),
}))

// ── Fixtures ──────────────────────────────────────────────────────────

const ANCESTRY_FINDING: AncestryFindingResponse = {
  top_population: "EUR",
  pc_scores: [0.012, -0.004, 0.001, 0.002, -0.001, 0.000, 0.003, -0.002],
  population_distances: {
    AFR: 0.85,
    AMR: 0.32,
    CSA: 0.45,
    EAS: 0.71,
    EUR: 0.04,
    MID: 0.56,
    OCE: 0.92,
  },
  admixture_fractions: {
    EUR: 0.82,
    AMR: 0.11,
    EAS: 0.04,
    CSA: 0.02,
    AFR: 0.01,
    MID: 0.00,
    OCE: 0.00,
  },
  population_ranking: [
    { population: "EUR", distance: 0.04 },
    { population: "AMR", distance: 0.32 },
    { population: "CSA", distance: 0.45 },
    { population: "MID", distance: 0.56 },
    { population: "EAS", distance: 0.71 },
    { population: "AFR", distance: 0.85 },
    { population: "OCE", distance: 0.92 },
  ],
  snps_used: 4500,
  snps_total: 5000,
  coverage_fraction: 0.9,
  projection_time_ms: 45.3,
  is_sufficient: true,
  evidence_level: 2,
  finding_text: "Inferred ancestry: EUR 82%, AMR 11%, EAS 4% (4,500/5,000 markers, 90% coverage)",
  confidence: 0.95,
  missing_aim_rate: 0.1,
  admixture_method: "nnls",
  n_pcs_used: 8,
  nnls_fractions: { EUR: 0.82, AMR: 0.11, EAS: 0.04, CSA: 0.02, AFR: 0.01, MID: 0.00, OCE: 0.00 },
  knn_fractions: { EUR: 0.80, AMR: 0.13, EAS: 0.04, CSA: 0.02, AFR: 0.01, MID: 0.00, OCE: 0.00 },
  nnls_ci_low: { EUR: 0.78, AMR: 0.08, EAS: 0.02, CSA: 0.00, AFR: 0.00, MID: 0.00, OCE: 0.00 },
  nnls_ci_high: { EUR: 0.86, AMR: 0.14, EAS: 0.06, CSA: 0.04, AFR: 0.02, MID: 0.01, OCE: 0.01 },
}

const LOW_COVERAGE_FINDING: AncestryFindingResponse = {
  ...ANCESTRY_FINDING,
  snps_used: 500,
  snps_total: 5000,
  coverage_fraction: 0.1,
  is_sufficient: false,
  missing_aim_rate: 0.9,
  confidence: 0.3,
  finding_text: "Primary ancestry component: European (70.0%) — low coverage",
}

const PCA_COORDINATES: PCACoordinatesResponse = {
  user: [0.012, -0.004, 0.001, 0.002, -0.001, 0.000, 0.003, -0.002],
  reference_samples: {
    EUR: [
      [0.01, -0.005, 0.001, 0.002, -0.001, 0.000, 0.003, -0.002],
      [0.015, -0.003, 0.002, 0.001, -0.002, 0.001, 0.002, -0.001],
    ],
    AFR: [
      [-0.02, 0.03, -0.01, 0.005, 0.001, -0.002, 0.003, 0.001],
      [-0.025, 0.035, -0.012, 0.006, 0.002, -0.001, 0.002, 0.002],
    ],
    CSA: [
      [0.005, -0.01, 0.008, -0.003, 0.002, 0.001, -0.001, 0.003],
    ],
    MID: [
      [0.008, -0.006, 0.004, -0.001, 0.001, 0.002, 0.001, 0.001],
    ],
  },
  centroids: {
    EUR: [0.012, -0.004, 0.001, 0.002, -0.001, 0.000, 0.003, -0.002],
    AFR: [-0.022, 0.032, -0.011, 0.005, 0.001, -0.001, 0.002, 0.001],
    CSA: [0.005, -0.01, 0.008, -0.003, 0.002, 0.001, -0.001, 0.003],
    MID: [0.008, -0.006, 0.004, -0.001, 0.001, 0.002, 0.001, 0.001],
  },
  population_labels: {
    EUR: "European",
    AFR: "African",
    CSA: "Central/South Asian",
    MID: "Middle Eastern",
  },
  n_components: 8,
  pc_labels: ["PC1", "PC2", "PC3", "PC4", "PC5", "PC6", "PC7", "PC8"],
  top_population: "EUR",
}

// ── AncestryResultCard tests ─────────────────────────────────────────

describe("AncestryResultCard", () => {
  it("renders top population badge", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByTestId("top-population-badge")).toHaveTextContent("European")
  })

  it("renders finding text", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(
      screen.getByText(/Inferred ancestry: EUR 82%/),
    ).toBeInTheDocument()
  })

  it("renders SNP coverage stats", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByText(/4,500 \/ 5,000 AIMs used \(90%\)/)).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByLabelText("2 of 4 stars evidence")).toBeInTheDocument()
  })

  it("shows high confidence badge", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByTestId("confidence-badge")).toHaveTextContent("High confidence")
  })

  it("shows moderate confidence badge when confidence < 90%", () => {
    const modConf = { ...ANCESTRY_FINDING, confidence: 0.75 }
    render(<AncestryResultCard finding={modConf} />)
    expect(screen.getByTestId("confidence-badge")).toHaveTextContent("Moderate confidence")
  })

  it("does not show confidence badge when confidence is 0", () => {
    const noConf = { ...ANCESTRY_FINDING, confidence: 0 }
    render(<AncestryResultCard finding={noConf} />)
    expect(screen.queryByTestId("confidence-badge")).not.toBeInTheDocument()
  })

  it("shows missing AIM indicator", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByTestId("missing-aim-indicator")).toHaveTextContent("10% AIMs missing")
  })

  it("does not show missing AIM indicator when rate is 0", () => {
    const noMissing = { ...ANCESTRY_FINDING, missing_aim_rate: 0 }
    render(<AncestryResultCard finding={noMissing} />)
    expect(screen.queryByTestId("missing-aim-indicator")).not.toBeInTheDocument()
  })

  it("shows low coverage warning when insufficient", () => {
    render(<AncestryResultCard finding={LOW_COVERAGE_FINDING} />)
    expect(
      screen.getByText("Low coverage — results may be unreliable"),
    ).toBeInTheDocument()
  })

  it("does not show low coverage warning when sufficient", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(
      screen.queryByText("Low coverage — results may be unreliable"),
    ).not.toBeInTheDocument()
  })

  it("renders population ranking with 7 populations", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByText("Population Ranking")).toBeInTheDocument()
    expect(screen.getAllByText("European").length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText("Admixed American")).toBeInTheDocument()
    expect(screen.getByText("Central/South Asian")).toBeInTheDocument()
    expect(screen.getByText("East Asian")).toBeInTheDocument()
    expect(screen.getByText("Middle Eastern")).toBeInTheDocument()
    expect(screen.getByText("African")).toBeInTheDocument()
    expect(screen.getByText("Oceanian")).toBeInTheDocument()
  })

  it("renders population distances", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByText("0.0400")).toBeInTheDocument()
    expect(screen.getByText("0.3200")).toBeInTheDocument()
  })

  it("labels the ranking metric and its direction (#532)", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    // A caption must name the metric (distance to centroid) and state which
    // direction is "better", so the increasing-down-the-list numbers don't read
    // backwards as "bigger = stronger match".
    expect(
      screen.getByText(/distance to population centroid/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/lower is closer/i)).toBeInTheDocument()
  })

  it("shows an ordinal rank so best-first ordering is explicit (#532)", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByText("#1")).toBeInTheDocument()
    expect(screen.getByText("#7")).toBeInTheDocument()
  })

  it("gives each distance an accessible label with direction (#532)", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    // Best match (European, 0.0400) is rank 1 and carries the lower-is-closer hint.
    expect(
      screen.getByLabelText(
        /European: rank 1, distance 0\.0400 \(lower is closer\)/,
      ),
    ).toBeInTheDocument()
  })

  it("has accessible test id", () => {
    render(<AncestryResultCard finding={ANCESTRY_FINDING} />)
    expect(screen.getByTestId("ancestry-result-card")).toBeInTheDocument()
  })
})

// ── AdmixtureBar tests ──────────────────────────────────────────────

describe("AdmixtureBar", () => {
  it("renders the chart container", () => {
    render(
      <AdmixtureBar admixture_fractions={ANCESTRY_FINDING.admixture_fractions} />,
    )
    expect(screen.getByTestId("admixture-bar")).toBeInTheDocument()
  })

  it("renders plotly chart with traces for 7 populations", () => {
    render(
      <AdmixtureBar admixture_fractions={ANCESTRY_FINDING.admixture_fractions} />,
    )
    const chart = screen.getByTestId("plotly-chart")
    const traces = JSON.parse(chart.getAttribute("data-traces") ?? "[]")
    // EUR, AMR, EAS, CSA should appear (above 0.001 threshold)
    expect(traces.length).toBeGreaterThanOrEqual(4)
  })

  it("shows empty state when no fractions", () => {
    render(<AdmixtureBar admixture_fractions={{}} />)
    expect(
      screen.getByText("No admixture data available."),
    ).toBeInTheDocument()
  })

  it("filters out near-zero fractions", () => {
    render(
      <AdmixtureBar admixture_fractions={{ EUR: 0.99, AFR: 0.0005 }} />,
    )
    const chart = screen.getByTestId("plotly-chart")
    const traces = JSON.parse(chart.getAttribute("data-traces") ?? "[]")
    expect(traces).toHaveLength(1)
    expect(traces[0].name).toBe("European")
  })
})

// ── PCAScatter tests ────────────────────────────────────────────────

describe("PCAScatter", () => {
  it("renders the chart container", () => {
    render(<PCAScatter pcaData={PCA_COORDINATES} />)
    expect(screen.getByTestId("pca-scatter")).toBeInTheDocument()
  })

  it("renders plotly chart", () => {
    render(<PCAScatter pcaData={PCA_COORDINATES} />)
    expect(screen.getByTestId("plotly-chart")).toBeInTheDocument()
  })

  it("includes reference population traces", () => {
    render(<PCAScatter pcaData={PCA_COORDINATES} />)
    const chart = screen.getByTestId("plotly-chart")
    const traces = JSON.parse(chart.getAttribute("data-traces") ?? "[]")
    const names = traces.map((t: { name: string }) => t.name)
    expect(names).toContain("European")
    expect(names).toContain("African")
    expect(names).toContain("Central/South Asian")
    expect(names).toContain("Middle Eastern")
  })

  it("includes user sample trace", () => {
    render(<PCAScatter pcaData={PCA_COORDINATES} />)
    const chart = screen.getByTestId("plotly-chart")
    const traces = JSON.parse(chart.getAttribute("data-traces") ?? "[]")
    const names = traces.map((t: { name: string }) => t.name)
    expect(names).toContain("You")
  })

  it("includes centroids trace", () => {
    render(<PCAScatter pcaData={PCA_COORDINATES} />)
    const chart = screen.getByTestId("plotly-chart")
    const traces = JSON.parse(chart.getAttribute("data-traces") ?? "[]")
    const names = traces.map((t: { name: string }) => t.name)
    expect(names).toContain("Centroids")
  })

  it("renders PC selection dropdowns when n_components > 2", () => {
    render(<PCAScatter pcaData={PCA_COORDINATES} />)
    expect(screen.getByTestId("pc-selectors")).toBeInTheDocument()
    expect(screen.getByTestId("pc-x-select")).toBeInTheDocument()
    expect(screen.getByTestId("pc-y-select")).toBeInTheDocument()
  })

  it("does not render PC selectors when n_components <= 2", () => {
    const twoPC: PCACoordinatesResponse = {
      ...PCA_COORDINATES,
      n_components: 2,
      pc_labels: ["PC1", "PC2"],
    }
    render(<PCAScatter pcaData={twoPC} />)
    expect(screen.queryByTestId("pc-selectors")).not.toBeInTheDocument()
  })

  it("updates chart axes when PC selection changes", () => {
    render(<PCAScatter pcaData={PCA_COORDINATES} />)
    const ySelect = screen.getByTestId("pc-y-select")
    fireEvent.change(ySelect, { target: { value: "2" } })
    // User trace should now use PC3 (index 2) for y-axis
    const chart = screen.getByTestId("plotly-chart")
    const traces = JSON.parse(chart.getAttribute("data-traces") ?? "[]")
    const userTrace = traces.find((t: { name: string }) => t.name === "You")
    expect(userTrace?.y[0]).toBe(PCA_COORDINATES.user[2])
  })
})

// ── AnalysisDetails tests ──────────────────────────────────────────

describe("AnalysisDetails", () => {
  it("renders collapsed by default", () => {
    render(<AnalysisDetails finding={ANCESTRY_FINDING} />)
    expect(screen.getByTestId("analysis-details")).toBeInTheDocument()
    expect(screen.queryByTestId("analysis-details-content")).not.toBeInTheDocument()
  })

  it("expands when clicked", () => {
    render(<AnalysisDetails finding={ANCESTRY_FINDING} />)
    fireEvent.click(screen.getByTestId("analysis-details-toggle"))
    expect(screen.getByTestId("analysis-details-content")).toBeInTheDocument()
  })

  it("shows AIMs used when expanded", () => {
    render(<AnalysisDetails finding={ANCESTRY_FINDING} />)
    fireEvent.click(screen.getByTestId("analysis-details-toggle"))
    expect(screen.getByText(/4,500 \/ 5,000/)).toBeInTheDocument()
  })

  it("shows PCs used when expanded", () => {
    render(<AnalysisDetails finding={ANCESTRY_FINDING} />)
    fireEvent.click(screen.getByTestId("analysis-details-toggle"))
    expect(screen.getByText("8")).toBeInTheDocument()
  })

  it("shows method description when expanded", () => {
    render(<AnalysisDetails finding={ANCESTRY_FINDING} />)
    fireEvent.click(screen.getByTestId("analysis-details-toggle"))
    expect(screen.getByText(/Non-negative least squares/)).toBeInTheDocument()
  })

  it("shows reference panel info when expanded", () => {
    render(<AnalysisDetails finding={ANCESTRY_FINDING} />)
    fireEvent.click(screen.getByTestId("analysis-details-toggle"))
    expect(screen.getByText(/3,419 single-ancestry samples/)).toBeInTheDocument()
  })

  it("shows missing AIM rate when expanded", () => {
    render(<AnalysisDetails finding={ANCESTRY_FINDING} />)
    fireEvent.click(screen.getByTestId("analysis-details-toggle"))
    expect(screen.getByText("10%")).toBeInTheDocument()
  })
})

// ── HaplogroupCard tests (P3-34) ──────────────────────────────────

const MT_ASSIGNMENT: HaplogroupAssignment = {
  type: "mt",
  haplogroup: "H1a",
  confidence: 0.904,
  defining_snps_present: 47,
  defining_snps_total: 52,
  traversal_path: [
    { haplogroup: "L3", snps_present: 3, snps_total: 3 },
    { haplogroup: "N", snps_present: 5, snps_total: 6 },
    { haplogroup: "R", snps_present: 2, snps_total: 2 },
    { haplogroup: "R0", snps_present: 1, snps_total: 1 },
    { haplogroup: "HV", snps_present: 4, snps_total: 5 },
    { haplogroup: "H", snps_present: 8, snps_total: 9 },
    { haplogroup: "H1", snps_present: 6, snps_total: 7 },
    { haplogroup: "H1a", snps_present: 18, snps_total: 19 },
  ],
  finding_text: "Mitochondrial haplogroup: H1a (47/52 defining SNPs matched, 90% confidence)",
}

const Y_ASSIGNMENT: HaplogroupAssignment = {
  type: "Y",
  haplogroup: "R1b1a",
  confidence: 0.846,
  defining_snps_present: 11,
  defining_snps_total: 13,
  traversal_path: [
    { haplogroup: "CT", snps_present: 2, snps_total: 2 },
    { haplogroup: "F", snps_present: 1, snps_total: 1 },
    { haplogroup: "K", snps_present: 1, snps_total: 2 },
    { haplogroup: "R", snps_present: 2, snps_total: 2 },
    { haplogroup: "R1b", snps_present: 3, snps_total: 3 },
    { haplogroup: "R1b1a", snps_present: 2, snps_total: 3 },
  ],
  finding_text: "Y-chromosome haplogroup: R1b1a (11/13 defining SNPs matched, 85% confidence)",
}

describe("HaplogroupCard", () => {
  it("renders empty state when no assignments", () => {
    render(<HaplogroupCard assignments={[]} />)
    expect(screen.getByTestId("haplogroup-card")).toBeInTheDocument()
    expect(
      screen.getByText(/No haplogroup assignments available/),
    ).toBeInTheDocument()
  })

  it("renders mt assignment with haplogroup name", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    expect(screen.getByTestId("haplogroup-assignment-mt")).toBeInTheDocument()
    expect(screen.getByTestId("haplogroup-name")).toHaveTextContent("H1a")
  })

  it("renders confidence badge", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    expect(screen.getByTestId("haplogroup-confidence-badge")).toHaveTextContent("90% confidence")
  })

  it("renders defining SNP match fraction", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    expect(screen.getByText("47 / 52 defining SNPs matched")).toBeInTheDocument()
  })

  it("renders traversal path with all nodes", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    const path = screen.getByTestId("haplogroup-traversal-path")
    expect(path).toBeInTheDocument()
    expect(screen.getByText("L3")).toBeInTheDocument()
    expect(screen.getByText("N")).toBeInTheDocument()
    expect(path.textContent).toContain("R")
    expect(path.textContent).toContain("HV")
    expect(path.textContent).toContain("H1")
    expect(path.textContent).toContain("H1a")
  })

  it("shows per-node SNP counts in traversal", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    expect(screen.getByText("3/3")).toBeInTheDocument() // L3
    expect(screen.getByText("5/6")).toBeInTheDocument() // N
    expect(screen.getByText("18/19")).toBeInTheDocument() // H1a
  })

  it("renders finding text", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    expect(
      screen.getByText(/Mitochondrial haplogroup: H1a/),
    ).toBeInTheDocument()
  })

  it("renders both mt and Y assignments", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT, Y_ASSIGNMENT]} />)
    expect(screen.getByTestId("haplogroup-assignment-mt")).toBeInTheDocument()
    expect(screen.getByTestId("haplogroup-assignment-Y")).toBeInTheDocument()
    expect(screen.getByText("Mitochondrial (mtDNA)")).toBeInTheDocument()
    expect(screen.getByText("Y-Chromosome")).toBeInTheDocument()
  })

  it("shows correct tree labels", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    expect(screen.getByText("Mitochondrial (mtDNA)")).toBeInTheDocument()
  })

  it("renders low confidence with warning color", () => {
    const lowConf: HaplogroupAssignment = {
      ...MT_ASSIGNMENT,
      confidence: 0.35,
    }
    render(<HaplogroupCard assignments={[lowConf]} />)
    expect(screen.getByTestId("haplogroup-confidence-badge")).toHaveTextContent("35% confidence")
  })

  it("highlights terminal haplogroup in traversal path", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    const path = screen.getByTestId("haplogroup-traversal-path")
    const highlighted = path.querySelectorAll("[data-highlighted]")
    expect(highlighted.length).toBeGreaterThanOrEqual(1)
  })

  it("has accessible card test id", () => {
    render(<HaplogroupCard assignments={[MT_ASSIGNMENT]} />)
    expect(screen.getByTestId("haplogroup-card")).toBeInTheDocument()
  })
})

// ── ChromosomePainting tests (AMv2 Step 6) ──────────────────────────

const PAINTING_SEGMENT: ChromosomePaintingSegment = {
  start: 10_000_000,
  end: 50_000_000,
  n_snps: 0,
  hap0: "EUR",
  hap1: "AFR",
  hap0_color: "#3B82F6",
  hap1_color: "#F59E0B",
}

const PAINTING_SEGMENT_2: ChromosomePaintingSegment = {
  start: 50_000_000,
  end: 100_000_000,
  n_snps: 0,
  hap0: "EAS",
  hap1: "EUR",
  hap0_color: "#10B981",
  hap1_color: "#3B82F6",
}

const SAMPLE_PAINTING: Record<string, ChromosomePaintingSegment[]> = Object.fromEntries(
  Array.from({ length: 22 }, (_, i) => [
    `chr${i + 1}`,
    [PAINTING_SEGMENT, PAINTING_SEGMENT_2],
  ]),
)

describe("ChromosomePainting", () => {
  it("renders the painting container", () => {
    render(<ChromosomePainting painting={SAMPLE_PAINTING} />)
    expect(screen.getByTestId("chromosome-painting")).toBeInTheDocument()
  })

  it("renders 22 chromosomes", () => {
    render(<ChromosomePainting painting={SAMPLE_PAINTING} />)
    for (let i = 1; i <= 22; i++) {
      expect(screen.getByTestId(`painting-chr${i}`)).toBeInTheDocument()
    }
  })

  it("renders population legend", () => {
    render(<ChromosomePainting painting={SAMPLE_PAINTING} />)
    const legend = screen.getByTestId("painting-legend")
    expect(legend).toBeInTheDocument()
    expect(legend.textContent).toContain("European")
    expect(legend.textContent).toContain("African")
    expect(legend.textContent).toContain("East Asian")
  })

  it("handles empty painting data", () => {
    render(<ChromosomePainting painting={{}} />)
    expect(screen.getByTestId("chromosome-painting")).toBeInTheDocument()
    // Should still render 22 empty chromosome tracks
    expect(screen.getByTestId("painting-chr1")).toBeInTheDocument()
  })

  it("renders chromosome labels", () => {
    render(<ChromosomePainting painting={SAMPLE_PAINTING} />)
    const svg = screen.getByTestId("chromosome-painting").querySelector("svg")
    expect(svg).toBeInTheDocument()
    // Check for label text elements
    const textElements = svg!.querySelectorAll("text")
    expect(textElements.length).toBe(22) // One label per chromosome
  })
})

// ── AncestryPieChart tests (AMv2 Step 6) ────────────────────────────

const SAMPLE_GLOBAL_ANCESTRY: Record<string, LAIGlobalAncestryEntry> = {
  EUR: { fraction: 0.75, percentage: 75.0, display_name: "European", color: "#3B82F6" },
  AMR: { fraction: 0.15, percentage: 15.0, display_name: "Admixed American", color: "#EF4444" },
  AFR: { fraction: 0.08, percentage: 8.0, display_name: "African", color: "#F59E0B" },
  EAS: { fraction: 0.02, percentage: 2.0, display_name: "East Asian", color: "#10B981" },
  CSA: { fraction: 0.0, percentage: 0.0, display_name: "Central/South Asian", color: "#8B5CF6" },
  MID: { fraction: 0.0, percentage: 0.0, display_name: "Middle Eastern", color: "#14B8A6" },
  OCE: { fraction: 0.0, percentage: 0.0, display_name: "Oceanian", color: "#EC4899" },
}

describe("AncestryPieChart", () => {
  it("renders the chart container", () => {
    render(<AncestryPieChart globalAncestry={SAMPLE_GLOBAL_ANCESTRY} />)
    expect(screen.getByTestId("ancestry-pie-chart")).toBeInTheDocument()
  })

  it("renders plotly chart with populations above threshold", () => {
    render(<AncestryPieChart globalAncestry={SAMPLE_GLOBAL_ANCESTRY} />)
    const chart = screen.getByTestId("plotly-chart")
    const traces = JSON.parse(chart.getAttribute("data-traces") ?? "[]")
    expect(traces).toHaveLength(1)
    // Should include EUR, AMR, AFR, EAS (>= 0.1%) but not CSA, MID, OCE (0%)
    expect(traces[0].labels).toEqual(["African", "Admixed American", "East Asian", "European"])
  })

  it("shows empty state when all fractions near zero", () => {
    const empty: Record<string, LAIGlobalAncestryEntry> = {
      EUR: { fraction: 0.0, percentage: 0.0, display_name: "European", color: "#3B82F6" },
    }
    render(<AncestryPieChart globalAncestry={empty} />)
    expect(screen.getByText("No LAI ancestry data available.")).toBeInTheDocument()
  })

  it("shows chromosome painting label", () => {
    render(<AncestryPieChart globalAncestry={SAMPLE_GLOBAL_ANCESTRY} />)
    expect(screen.getByText("Based on chromosome painting analysis")).toBeInTheDocument()
  })
})
