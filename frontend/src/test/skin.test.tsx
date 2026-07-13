/** Tests for the Gene Skin UI (P3-56, T3-67). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/skin/PathwayCard"
import PathwayDetailPanel from "@/components/skin/PathwayDetailPanel"
import SkinView from "@/pages/SkinView"
import { useSkinPathwayDetail, useSkinPathways } from "@/api/skin"
import type {
  MC1RAggregateItem,
  PathwayDetailResponse,
  PathwaySummary,
} from "@/types/skin"

vi.mock("@/api/skin", () => ({
  useSkinPathwayDetail: vi.fn(),
  useSkinPathways: vi.fn(),
}))

// ── Fixtures ──────────────────────────────────────────────────────────

const PIGMENTATION_PATHWAY: PathwaySummary = {
  pathway_id: "pigmentation_uv",
  pathway_name: "Pigmentation & UV Response",
  level: "Elevated",
  evidence_level: 3,
  called_snps: 4,
  total_snps: 4,
  missing_snps: [],
  pmids: ["18488027", "17952075"],
}

const BARRIER_PATHWAY: PathwaySummary = {
  pathway_id: "skin_barrier_inflammation",
  pathway_name: "Skin Barrier & Inflammation",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 2,
  total_snps: 3,
  missing_snps: ["rs61816761"],
  pmids: ["16804399"],
}

const OXIDATIVE_PATHWAY: PathwaySummary = {
  pathway_id: "oxidative_stress_aging",
  pathway_name: "Oxidative Stress & Aging",
  level: "Standard",
  evidence_level: 1,
  called_snps: 3,
  total_snps: 3,
  missing_snps: [],
  pmids: [],
}

const MICRONUTRIENTS_PATHWAY: PathwaySummary = {
  pathway_id: "skin_micronutrients",
  pathway_name: "Skin Micronutrients",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 1,
  total_snps: 2,
  missing_snps: ["rs7975232"],
  pmids: ["20541252"],
}

const RS885479_DETAIL: PathwayDetailResponse = {
  pathway_id: "pigmentation_uv",
  pathway_name: "Pigmentation & UV Response",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 1,
  total_snps: 1,
  missing_snps: [],
  pmids: ["18366057"],
  snp_details: [
    {
      rsid: "rs885479",
      gene: "MC1R",
      variant_name: "R163Q",
      genotype: "AG",
      category: "Moderate",
      effect_summary:
        "One copy of the R163Q 'r' (mild) allele. Modestly reduced MC1R signaling.",
      evidence_level: 2,
      recommendation: null,
      pmids: ["18366057"],
      mc1r_allele_class: "r",
      coverage_note: null,
      insufficient_data_flag: false,
    },
  ],
}

const useSkinPathwayDetailMock = useSkinPathwayDetail as unknown as ReturnType<
  typeof vi.fn
>
const useSkinPathwaysMock = useSkinPathways as unknown as ReturnType<
  typeof vi.fn
>

function renderMC1RAggregate(aggregate: MC1RAggregateItem) {
  useSkinPathwaysMock.mockReturnValue({
    data: {
      items: [PIGMENTATION_PATHWAY],
      total: 1,
      mc1r_aggregate: aggregate,
      cross_module: [],
      insufficient_data: [],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })

  return render(<SkinView />, { route: "/skin?sample_id=1" })
}

// ── MC1R summary tests ────────────────────────────────────────────────

describe("MC1R allele summary tier styling (#1759)", () => {
  beforeEach(() => {
    useSkinPathwaysMock.mockReset()
  })

  it.each([
    ["Low UV Sensitivity", 0, "emerald"],
    ["Mild MC1R Variant", 0, "sky"],
    ["Moderate UV Sensitivity", 1, "blue"],
    ["High UV Sensitivity", 2, "amber"],
  ])(
    "renders %s with the %s-count %s palette",
    (riskLabel, rAlleleCount, tone) => {
      renderMC1RAggregate({
        r_allele_count: rAlleleCount,
        r_allele_rsids: [],
        total_mc1r_called: 4,
        risk_label: riskLabel,
        risk_description: `${riskLabel} description`,
        evidence_level: 2,
        pmids: [],
      })

      const summary = screen.getByRole("region", {
        name: "MC1R allele summary",
      })
      const card = summary.querySelector(":scope > div")
      const countBadge = within(summary).getByText(String(rAlleleCount), {
        selector: "span",
      })
      const labelBox = within(summary).getByText(riskLabel, {
        exact: true,
      }).parentElement

      expect(card).toHaveClass(
        `bg-${tone}-50`,
        `border-${tone}-200`,
        `dark:bg-${tone}-950/30`,
        `dark:border-${tone}-800`,
      )
      expect(countBadge).toHaveClass(
        `bg-${tone}-100`,
        `text-${tone}-800`,
        `dark:bg-${tone}-900/50`,
        `dark:text-${tone}-300`,
      )
      expect(labelBox).toHaveClass(
        `bg-${tone}-100/50`,
        `dark:bg-${tone}-900/20`,
      )
    },
  )

  it("warns once and uses neutral styling for an unknown label", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const aggregate: MC1RAggregateItem = {
      r_allele_count: 0,
      r_allele_rsids: [],
      total_mc1r_called: 4,
      risk_label: "constructor",
      risk_description: "Unrecognized tier description",
      evidence_level: 2,
      pmids: [],
    }

    renderMC1RAggregate(aggregate)

    const summary = screen.getByRole("region", { name: "MC1R allele summary" })
    const card = summary.querySelector(":scope > div")
    const countBadge = within(summary).getByText("0", { selector: "span" })
    const labelBox = within(summary).getByText("constructor", {
      exact: true,
    }).parentElement

    expect(card).toHaveClass(
      "bg-slate-50",
      "border-slate-200",
      "dark:bg-slate-950/30",
      "dark:border-slate-800",
    )
    expect(countBadge).toHaveClass(
      "bg-slate-100",
      "text-slate-800",
      "dark:bg-slate-900/50",
      "dark:text-slate-300",
    )
    expect(labelBox).toHaveClass(
      "bg-slate-100/50",
      "dark:bg-slate-900/20",
    )

    renderMC1RAggregate(aggregate)
    expect(warn).toHaveBeenCalledOnce()
    expect(warn).toHaveBeenCalledWith(
      '[SkinView] Unknown MC1R risk label "constructor"; using neutral styling.',
    )

    warn.mockRestore()
  })
})

// ── PathwayCard tests ─────────────────────────────────────────────────

describe("PathwayCard", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders pathway name", () => {
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Pigmentation & UV Response")).toBeInTheDocument()
  })

  it("shows Elevated badge for Elevated level", () => {
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Elevated")).toBeInTheDocument()
  })

  it("shows Moderate badge for Moderate level", () => {
    render(<PathwayCard pathway={BARRIER_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Moderate")).toBeInTheDocument()
  })

  it("shows Standard badge for Standard level", () => {
    render(<PathwayCard pathway={OXIDATIVE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    expect(screen.getByLabelText("3 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders SNP coverage count", () => {
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("4/4 SNPs called")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    await user.click(
      screen.getByRole("button", {
        name: "Pigmentation & UV Response — Elevated",
      }),
    )
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", {
      name: "Pigmentation & UV Response — Elevated",
    })
    card.focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("has accessible role and label", () => {
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByRole("button", {
        name: "Pigmentation & UV Response — Elevated",
      }),
    ).toBeInTheDocument()
  })

  it("renders pathway description for pigmentation_uv", () => {
    render(<PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/MC1R-driven pigmentation.*UV sensitivity/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for skin_barrier_inflammation", () => {
    render(<PathwayCard pathway={BARRIER_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Skin barrier integrity.*filaggrin/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for oxidative_stress_aging", () => {
    render(<PathwayCard pathway={OXIDATIVE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Antioxidant capacity.*collagen/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for skin_micronutrients", () => {
    render(<PathwayCard pathway={MICRONUTRIENTS_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Vitamin D receptor.*micronutrient/),
    ).toBeInTheDocument()
  })

  it("shows selected state when selected prop is true", () => {
    render(
      <PathwayCard pathway={PIGMENTATION_PATHWAY} onClick={onClick} selected />,
    )
    const card = screen.getByRole("button", {
      name: "Pigmentation & UV Response — Elevated",
    })
    expect(card).toHaveAttribute("data-selected", "true")
  })

  it("renders all four pathway cards with correct data", () => {
    const pathways = [PIGMENTATION_PATHWAY, BARRIER_PATHWAY, OXIDATIVE_PATHWAY, MICRONUTRIENTS_PATHWAY]
    for (const pathway of pathways) {
      const { unmount } = render(
        <PathwayCard pathway={pathway} onClick={onClick} />,
      )
      expect(screen.getByText(pathway.pathway_name)).toBeInTheDocument()
      unmount()
    }
  })
})

// ── PathwayDetailPanel tests ───────────────────────────────────────────

describe("PathwayDetailPanel", () => {
  beforeEach(() => {
    useSkinPathwayDetailMock.mockReset()
  })

  it("preserves the lowercase MC1R r allele badge for rs885479", () => {
    useSkinPathwayDetailMock.mockReturnValue({
      data: RS885479_DETAIL,
      isLoading: false,
      isError: false,
      error: null,
    })

    render(
      <PathwayDetailPanel
        pathwayId="pigmentation_uv"
        pathwayName="Pigmentation & UV Response"
        sampleId={1}
        onClose={() => {}}
      />,
    )

    expect(screen.getByText("MC1R r allele")).toBeInTheDocument()
    expect(screen.queryByText("MC1R R allele")).not.toBeInTheDocument()
  })
})
