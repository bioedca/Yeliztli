/** Tests for the Gene Sleep UI (P3-50). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/sleep/PathwayCard"
import SleepView, { MetabolizerCard } from "@/pages/SleepView"
import { useSleepPathways } from "@/api/sleep"
import type { CrossModuleItem, MetabolizerState, PathwaySummary } from "@/types/sleep"

vi.mock("@/api/sleep", () => ({
  useSleepPathways: vi.fn(),
  useSleepPathwayDetail: vi.fn(),
}))

const mockUseSleepPathways = vi.mocked(useSleepPathways)

// ── Fixtures ──────────────────────────────────────────────────────────

const CAFFEINE_PATHWAY: PathwaySummary = {
  pathway_id: "caffeine_sleep",
  pathway_name: "Caffeine & Sleep",
  level: "Elevated",
  evidence_level: 3,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: ["16522833", "26378246"],
}

const QUALITY_PATHWAY: PathwaySummary = {
  pathway_id: "sleep_quality",
  pathway_name: "Sleep Quality",
  level: "Standard",
  evidence_level: 1,
  called_snps: 1,
  total_snps: 2,
  missing_snps: ["rs2300478"],
  pmids: [],
}

const DISORDERS_PATHWAY: PathwaySummary = {
  pathway_id: "sleep_disorders",
  pathway_name: "Sleep Disorders",
  level: "Elevated",
  evidence_level: 2,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: ["19923809"],
}

function crossModule(targetModule: string): CrossModuleItem {
  return {
    rsid: `rs-${targetModule}`,
    gene: "GENE1",
    source_module: "sleep",
    target_module: targetModule,
    finding_text: "Shared sleep variant also assessed elsewhere.",
    evidence_level: 2,
    pmids: [],
  }
}

function mockSleepPathways(crossModuleItems: CrossModuleItem[]) {
  mockUseSleepPathways.mockReturnValue({
    data: {
      items: [CAFFEINE_PATHWAY],
      total: 1,
      cross_module: crossModuleItems,
      metabolizer: null,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useSleepPathways>)
}

// ── PathwayCard tests ─────────────────────────────────────────────────

describe("PathwayCard", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders pathway name", () => {
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Caffeine & Sleep")).toBeInTheDocument()
  })

  it("shows Elevated badge for Elevated level", () => {
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Elevated")).toBeInTheDocument()
  })

  it("shows Moderate badge for Moderate level", () => {
    render(
      <PathwayCard pathway={{ ...CAFFEINE_PATHWAY, level: "Moderate" }} onClick={onClick} />,
    )
    expect(screen.getByText("Moderate")).toBeInTheDocument()
  })

  it("qualifies Standard badge when SNP coverage is incomplete", () => {
    render(<PathwayCard pathway={QUALITY_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Tested Standard")).toBeInTheDocument()
    expect(screen.getByTestId("pathway-coverage-caveat")).toHaveTextContent(
      "No variants of concern among tested SNPs; 1 tracked SNP (1 off-chip) not assessed.",
    )
  })

  it("renders evidence stars", () => {
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    expect(screen.getByLabelText("3 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders SNP coverage count", () => {
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("2/2 SNPs called")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    await user.click(
      screen.getByRole("button", {
        name: "Caffeine & Sleep — Elevated",
      }),
    )
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", {
      name: "Caffeine & Sleep — Elevated",
    })
    card.focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("has accessible role and label", () => {
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByRole("button", {
        name: "Caffeine & Sleep — Elevated",
      }),
    ).toBeInTheDocument()
  })

  it("renders pathway description for caffeine_sleep", () => {
    render(<PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Caffeine metabolism rate.*sensitivity/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for sleep_quality", () => {
    render(<PathwayCard pathway={QUALITY_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Sleep depth.*duration needs/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for sleep_disorders", () => {
    render(<PathwayCard pathway={DISORDERS_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", {
      name: "Sleep Disorders — Elevated",
    })
    expect(card).toHaveTextContent(/no narcolepsy risk is inferred from rs2858884/i)
    expect(card).not.toHaveTextContent(/narcolepsy(?:[-\s]risk)? prox(?:y|ies)/i)
  })

  it("shows selected state when selected prop is true", () => {
    render(
      <PathwayCard pathway={CAFFEINE_PATHWAY} onClick={onClick} selected />,
    )
    const card = screen.getByRole("button", {
      name: "Caffeine & Sleep — Elevated",
    })
    expect(card).toHaveAttribute("data-selected", "true")
  })

  it("renders all three pathway cards with correct data", () => {
    const pathways = [CAFFEINE_PATHWAY, QUALITY_PATHWAY, DISORDERS_PATHWAY]
    for (const pathway of pathways) {
      const { unmount } = render(
        <PathwayCard pathway={pathway} onClick={onClick} />,
      )
      expect(screen.getByText(pathway.pathway_name)).toBeInTheDocument()
      unmount()
    }
  })
})

describe("MetabolizerCard (#758)", () => {
  const meta = (state: string | null): MetabolizerState => ({
    state,
    gene: "CYP1A2",
    rsid: "rs762551",
  })

  // The backend (`_resolve_metabolizer_state`) returns the panel's FULL label
  // verbatim — "Rapid metabolizer" / "Intermediate metabolizer" / "Slow
  // metabolizer" — NOT a short code. Feed those real values (not hand-invented
  // short codes) so the card's label→key resolution is locked against the
  // backend contract. Before the fix the whole lowercased label was looked up in
  // a rapid/intermediate/slow map and always missed → "Unknown" for every user.
  it.each([
    ["Rapid metabolizer", "Rapid Metabolizer"],
    ["Intermediate metabolizer", "Intermediate Metabolizer"],
    ["Slow metabolizer", "Slow Metabolizer"],
  ])("resolves the real backend label %s to %s (not Unknown)", (backendLabel, shown) => {
    render(<MetabolizerCard metabolizer={meta(backendLabel)} />)
    expect(screen.getByText(shown)).toBeInTheDocument()
    expect(screen.queryByText("Unknown")).toBeNull()
    expect(
      screen.queryByText(/could not be determined/i),
    ).toBeNull()
  })

  it("falls back to Unknown only when the state is genuinely null", () => {
    render(<MetabolizerCard metabolizer={meta(null)} />)
    expect(screen.getByText("Unknown")).toBeInTheDocument()
    expect(screen.getByText(/could not be determined/i)).toBeInTheDocument()
  })
})

describe("SleepView cross-module references (#1547)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("uses the shared registry route and label for non-Pharmacogenomics targets", () => {
    mockSleepPathways([crossModule("nutrigenomics")])

    const { container } = render(<SleepView />, { route: "/sleep?sample_id=1" })

    const link = screen.getByRole("link", { name: /View in Nutrigenomics/i })
    expect(link).toHaveAttribute("href", "/nutrigenomics?sample_id=1")
    expect(container.textContent).toContain("Nutrigenomics")
    expect(screen.queryByRole("link", { name: /View in Pharmacogenomics/i })).toBeNull()
  })

  it("shows panel-only module labels without rendering a stale Pharmacogenomics link", () => {
    mockSleepPathways([crossModule("lhon")])

    const { container } = render(<SleepView />, { route: "/sleep?sample_id=1" })

    expect(container.textContent).toContain("LHON")
    expect(screen.queryByRole("link", { name: /View in LHON/i })).toBeNull()
    expect(screen.queryByRole("link", { name: /View in Pharmacogenomics/i })).toBeNull()
  })
})
