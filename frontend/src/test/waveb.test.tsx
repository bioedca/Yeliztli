/** Tests for Wave B frontends (SW-B5/B6/B7/B8). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import { toGaugePrs } from "@/types/metabolic"
import type { MetabolicAnchor, MetabolicPRS } from "@/types/metabolic"
import { AnchorCard } from "@/pages/MetabolicView"

// ── toGaugePrs adapter ────────────────────────────────────────────────

const BASE_PRS: MetabolicPRS = {
  trait: "type_2_diabetes",
  name: "T2D",
  calibrated: false,
  percentile: null,
  snps_used: 65000,
  snps_total: 183830,
  coverage_fraction: 0.354,
  is_sufficient: false,
  source_ancestry: "EUR",
  source_study: "Sinnott-Armstrong 2021",
  source_pmid: "33462484",
  sample_size: 223327,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 1,
  research_use_only: true,
  pgs_id: "PGS000713",
  pgs_license: "CC-BY-4.0",
  development_method: "snpnet (Lasso)",
  genome_build: "GRCh37",
  variants_number: 183830,
  source_url: "https://www.pgscatalog.org/score/PGS000713/",
}

describe("toGaugePrs", () => {
  it("maps a coverage-reported PRS to the gauge shape with provenance", () => {
    const g = toGaugePrs(BASE_PRS)
    expect(g.pgs_id).toBe("PGS000713")
    expect(g.development_method).toBe("snpnet (Lasso)")
    expect(g.calibrated).toBe(false)
    expect(g.percentile).toBeNull()
    // Uncalibrated scores carry no CI / z-score for the gauge.
    expect(g.bootstrap_ci_lower).toBeNull()
    expect(g.z_score).toBeNull()
    // No monogenic exclusion is implied by the adapter.
    expect(g.monogenic_genes).toEqual([])
    expect(g.research_use_only).toBe(true)
  })
})

// ── MetabolicView AnchorCard (#138 strand resolution) ─────────────────

const BASE_ANCHOR: MetabolicAnchor = {
  trait: "body_mass_index",
  trait_label: "Body mass index / obesity",
  gene: "FTO",
  rsid: "rs9939609",
  effect_allele: "A",
  genotype: "AT",
  dosage: 1,
  indeterminate: false,
  summary: "FTO — the strongest common BMI/adiposity locus.",
  evidence_level: 2,
  pmids: ["17434869"],
}

describe("MetabolicView AnchorCard", () => {
  it("renders a directional effect-allele dosage for a strand-resolved anchor", () => {
    render(<AnchorCard anchor={{ ...BASE_ANCHOR, genotype: "AA", dosage: 2 }} />)
    const card = screen.getByTestId("metabolic-anchor-card")
    expect(card).toHaveTextContent("2")
    expect(card).toHaveTextContent("effect allele")
    expect(screen.queryByTestId("anchor-indeterminate")).not.toBeInTheDocument()
  })

  it("suppresses the copy-count for a strand-ambiguous palindromic homozygote", () => {
    render(
      <AnchorCard anchor={{ ...BASE_ANCHOR, genotype: "TT", dosage: null, indeterminate: true }} />,
    )
    expect(screen.getByTestId("anchor-indeterminate")).toBeInTheDocument()
    const card = screen.getByTestId("metabolic-anchor-card")
    expect(card).toHaveTextContent("dosage not reported")
    // No inverted directional "× A effect allele" claim is shown.
    expect(card).not.toHaveTextContent("effect allele")
  })
})

// ── AbsoluteRiskOverlay (SW-B8 opt-in) ────────────────────────────────

const mockUseAbsoluteRisk = vi.fn()
const mockMutate = vi.fn()

vi.mock("@/api/cancer", () => ({
  useAbsoluteRisk: (sampleId: number | null) => mockUseAbsoluteRisk(sampleId),
  useSetAbsoluteRiskConsent: () => ({ mutate: mockMutate, isPending: false }),
}))

import AbsoluteRiskOverlay from "@/components/cancer/AbsoluteRiskOverlay"

describe("AbsoluteRiskOverlay", () => {
  beforeEach(() => {
    mockUseAbsoluteRisk.mockReset()
    mockMutate.mockReset()
  })

  it("shows the opt-in prompt + no figures before consent", () => {
    mockUseAbsoluteRisk.mockReturnValue({
      isLoading: false,
      data: {
        consented: false,
        opt_in_required: true,
        opt_in_prompt: "This optional overlay places your breast-cancer genetics…",
        disclaimer: "Not a clinical FH diagnosis.",
      },
    })
    render(<AbsoluteRiskOverlay sampleId={1} />)
    expect(screen.getByTestId("absolute-risk-optin")).toBeInTheDocument()
    expect(screen.queryByTestId("absolute-risk-overlay")).not.toBeInTheDocument()
  })

  it("fires consent mutation on opt-in click", async () => {
    mockUseAbsoluteRisk.mockReturnValue({
      isLoading: false,
      data: { consented: false, opt_in_required: true, opt_in_prompt: "x", disclaimer: "y" },
    })
    render(<AbsoluteRiskOverlay sampleId={1} />)
    await userEvent.click(screen.getByTestId("absolute-risk-optin-button"))
    expect(mockMutate).toHaveBeenCalledWith(true)
  })

  it("shows baseline + carrier penetrance after consent", () => {
    mockUseAbsoluteRisk.mockReturnValue({
      isLoading: false,
      data: {
        consented: true,
        opt_in_required: false,
        population_baseline: {
          lifetime_risk_pct: 12.9,
          source: "NCI SEER",
          source_url: "https://seer.cancer.gov/statfacts/html/breast.html",
          note: "~1 in 8",
        },
        has_monogenic: true,
        monogenic: [{ gene: "BRCA1", cumulative_risk_to_80_pct: 72, ci: "65-79", pmid: "28632866" }],
        prs_note: "coverage-limited",
        canrisk: { tool: "CanRisk / BOADICEA", url: "https://www.canrisk.org", pmid: "30643217", note: "use it" },
        disclaimer: "Not clinical.",
      },
    })
    render(<AbsoluteRiskOverlay sampleId={1} />)
    const overlay = screen.getByTestId("absolute-risk-overlay")
    expect(overlay).toHaveTextContent("12.9%")
    expect(screen.getByTestId("absolute-risk-monogenic")).toHaveTextContent("BRCA1")
    expect(screen.getByTestId("absolute-risk-monogenic")).toHaveTextContent("72%")
    expect(screen.getByRole("link", { name: /CanRisk/ })).toHaveAttribute(
      "href",
      "https://www.canrisk.org",
    )
  })
})
