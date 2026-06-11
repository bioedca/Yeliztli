/** Tests for the Traits & Personality UI (P3-64, T3-70). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/traits/PathwayCard"
import TraitsPRSGaugeCard from "@/components/traits/TraitsPRSGaugeCard"
import BigFiveRadarChart from "@/components/traits/BigFiveRadarChart"
import type { PathwaySummary, TraitsPRS, SNPDetail } from "@/types/traits"

// ── Fixtures ──────────────────────────────────────────────────────────

const PERSONALITY_PATHWAY: PathwaySummary = {
  pathway_id: "personality_big_five",
  pathway_name: "Big Five Personality",
  level: "Moderate",
  evidence_level: 2,
  prs_primary: false,
  called_snps: 5,
  total_snps: 5,
  missing_snps: [],
  pmids: ["29942086"],
}

const COGNITIVE_PATHWAY: PathwaySummary = {
  pathway_id: "cognitive_traits",
  pathway_name: "Cognitive Traits",
  level: "Elevated",
  evidence_level: 2,
  prs_primary: true,
  called_snps: 12,
  total_snps: 15,
  missing_snps: ["rs123", "rs456", "rs789"],
  pmids: ["29942086", "25201988"],
}

const BEHAVIORAL_PATHWAY: PathwaySummary = {
  pathway_id: "behavioral_traits",
  pathway_name: "Behavioral Traits",
  level: "Standard",
  evidence_level: 1,
  prs_primary: false,
  called_snps: 3,
  total_snps: 4,
  missing_snps: ["rs747302"],
  pmids: [],
}

const SUFFICIENT_PRS: TraitsPRS = {
  trait: "educational_attainment",
  name: "Educational Attainment",
  percentile: 65,
  z_score: 0.39,
  bootstrap_ci_lower: 55,
  bootstrap_ci_upper: 75,
  source_ancestry: "EUR",
  source_study: "Okbay 2022",
  snps_used: 95,
  snps_total: 100,
  coverage_fraction: 0.95,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  is_sufficient: true,
  calibrated: true,
  research_use_only: true,
  evidence_level: 2,
}

const INSUFFICIENT_PRS: TraitsPRS = {
  trait: "cognitive_ability",
  name: "Cognitive Ability",
  percentile: null,
  z_score: null,
  bootstrap_ci_lower: null,
  bootstrap_ci_upper: null,
  source_ancestry: "EUR",
  source_study: "Davies 2018",
  snps_used: 30,
  snps_total: 100,
  coverage_fraction: 0.3,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  is_sufficient: false,
  calibrated: true,
  research_use_only: true,
  evidence_level: 2,
}

const UNCALIBRATED_PRS: TraitsPRS = {
  trait: "educational_attainment",
  name: "Educational Attainment",
  percentile: null,
  z_score: null,
  bootstrap_ci_lower: null,
  bootstrap_ci_upper: null,
  source_ancestry: "EUR",
  source_study: "Okbay 2022",
  snps_used: 95,
  snps_total: 100,
  coverage_fraction: 0.95,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  is_sufficient: true,
  calibrated: false,
  research_use_only: true,
  evidence_level: 2,
}

const MISMATCH_PRS: TraitsPRS = {
  trait: "educational_attainment",
  name: "Educational Attainment",
  percentile: 52,
  z_score: 0.05,
  bootstrap_ci_lower: 40,
  bootstrap_ci_upper: 64,
  source_ancestry: "EUR",
  source_study: "Okbay 2022",
  snps_used: 90,
  snps_total: 100,
  coverage_fraction: 0.9,
  ancestry_mismatch: true,
  ancestry_warning_text: "PRS weights derived from EUR populations — results may not generalize to other ancestries.",
  is_sufficient: true,
  calibrated: true,
  research_use_only: true,
  evidence_level: 2,
}

const BIG_FIVE_SNPS: SNPDetail[] = [
  {
    rsid: "rs1234",
    gene: "GENE1",
    variant_name: "test variant 1",
    genotype: "AG",
    category: "Elevated",
    effect_summary: "Associated with higher openness",
    evidence_level: 2,
    trait_domain: "openness",
    recommendation: null,
    pmids: [],
    coverage_note: null,
    cross_module: null,
  },
  {
    rsid: "rs5678",
    gene: "GENE2",
    variant_name: "test variant 2",
    genotype: "CC",
    category: "Standard",
    effect_summary: "Associated with conscientiousness",
    evidence_level: 1,
    trait_domain: "conscientiousness",
    recommendation: null,
    pmids: [],
    coverage_note: null,
    cross_module: null,
  },
  {
    rsid: "rs9012",
    gene: "GENE3",
    variant_name: "test variant 3",
    genotype: "TT",
    category: "Moderate",
    effect_summary: "Associated with neuroticism",
    evidence_level: 2,
    trait_domain: "neuroticism",
    recommendation: null,
    pmids: [],
    coverage_note: null,
    cross_module: null,
  },
]

// ── PathwayCard tests ─────────────────────────────────────────────────

describe("PathwayCard", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders pathway name", () => {
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Big Five Personality")).toBeInTheDocument()
  })

  it("shows Moderate badge for Moderate level", () => {
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Moderate")).toBeInTheDocument()
  })

  it("shows Elevated badge for Elevated level", () => {
    render(<PathwayCard pathway={COGNITIVE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Elevated")).toBeInTheDocument()
  })

  it("shows Standard badge for Standard level", () => {
    render(<PathwayCard pathway={BEHAVIORAL_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    expect(screen.getByLabelText("2 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders SNP coverage count", () => {
    render(<PathwayCard pathway={COGNITIVE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("12/15 SNPs called")).toBeInTheDocument()
  })

  it("shows PRS-primary indicator for PRS-primary pathways", () => {
    render(<PathwayCard pathway={COGNITIVE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("PRS-primary pathway")).toBeInTheDocument()
  })

  it("does not show PRS-primary indicator for non-PRS pathways", () => {
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    expect(screen.queryByText("PRS-primary pathway")).not.toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    await user.click(
      screen.getByRole("button", {
        name: "Big Five Personality — Moderate",
      }),
    )
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", {
      name: "Big Five Personality — Moderate",
    })
    card.focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("has accessible role and label", () => {
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByRole("button", {
        name: "Big Five Personality — Moderate",
      }),
    ).toBeInTheDocument()
  })

  it("renders pathway description for personality_big_five", () => {
    render(<PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Big Five personality.*GWAS/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for cognitive_traits", () => {
    render(<PathwayCard pathway={COGNITIVE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Cognitive ability.*educational attainment/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for behavioral_traits", () => {
    render(<PathwayCard pathway={BEHAVIORAL_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Risk tolerance.*novelty seeking/),
    ).toBeInTheDocument()
  })

  it("shows selected state when selected prop is true", () => {
    render(
      <PathwayCard pathway={PERSONALITY_PATHWAY} onClick={onClick} selected />,
    )
    const card = screen.getByRole("button", {
      name: "Big Five Personality — Moderate",
    })
    expect(card).toHaveAttribute("data-selected", "true")
  })
})

// ── TraitsPRSGaugeCard tests ──────────────────────────────────────────

describe("TraitsPRSGaugeCard", () => {
  it("renders trait name", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(screen.getByText("Educational Attainment")).toBeInTheDocument()
  })

  it("shows Research Use Only banner", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(
      screen.getByText(/Research Use Only.*not for clinical/),
    ).toBeInTheDocument()
  })

  it("shows percentile for sufficient PRS", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(screen.getByText("65")).toBeInTheDocument()
    expect(screen.getByText("th percentile")).toBeInTheDocument()
  })

  it("shows CI range for sufficient PRS", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(screen.getByText("95% CI: 55–75th")).toBeInTheDocument()
  })

  it("shows insufficient coverage message for insufficient PRS", () => {
    render(<TraitsPRSGaugeCard prs={INSUFFICIENT_PRS} />)
    expect(screen.getByText("Insufficient SNP coverage (30%)")).toBeInTheDocument()
  })

  it("shows uncalibrated message when calibrated is false", () => {
    render(<TraitsPRSGaugeCard prs={UNCALIBRATED_PRS} />)
    expect(screen.getByTestId("traits-prs-uncalibrated")).toBeInTheDocument()
    expect(screen.getByText(/reference distribution unavailable/)).toBeInTheDocument()
  })

  it("shows ancestry mismatch warning when applicable", () => {
    render(<TraitsPRSGaugeCard prs={MISMATCH_PRS} />)
    expect(
      screen.getByText(/PRS weights derived from EUR.*may not generalize/),
    ).toBeInTheDocument()
  })

  it("does not show ancestry mismatch warning when no mismatch", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(
      screen.queryByTestId("ancestry-mismatch-warning"),
    ).not.toBeInTheDocument()
  })

  it("shows study source and ancestry", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(screen.getByText("Okbay 2022 (EUR)")).toBeInTheDocument()
  })

  it("shows SNP coverage stats", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(screen.getByText("95/100 SNPs (95%)")).toBeInTheDocument()
  })

  it("has accessible aria-label", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(
      screen.getByLabelText("Educational Attainment polygenic risk score"),
    ).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<TraitsPRSGaugeCard prs={SUFFICIENT_PRS} />)
    expect(screen.getByLabelText("2 of 4 stars evidence")).toBeInTheDocument()
  })
})

// ── BigFiveRadarChart tests ───────────────────────────────────────────

describe("BigFiveRadarChart", () => {
  it("renders SVG with radar chart role", () => {
    render(<BigFiveRadarChart snpDetails={BIG_FIVE_SNPS} />)
    expect(
      screen.getByRole("img", {
        name: /Big Five personality trait associations/,
      }),
    ).toBeInTheDocument()
  })

  it("renders all five dimension labels", () => {
    render(<BigFiveRadarChart snpDetails={BIG_FIVE_SNPS} />)
    expect(screen.getByText("Openness")).toBeInTheDocument()
    expect(screen.getByText("Conscientiousness")).toBeInTheDocument()
    expect(screen.getByText("Extraversion")).toBeInTheDocument()
    expect(screen.getByText("Agreeableness")).toBeInTheDocument()
    expect(screen.getByText("Neuroticism")).toBeInTheDocument()
  })

  it("renders visual-only disclaimer", () => {
    render(<BigFiveRadarChart snpDetails={BIG_FIVE_SNPS} />)
    expect(
      screen.getByText(/Visual representation.*not a personality assessment/),
    ).toBeInTheDocument()
  })

  it("renders with empty SNP data", () => {
    render(<BigFiveRadarChart snpDetails={[]} />)
    expect(screen.getByText("Openness")).toBeInTheDocument()
  })
})
