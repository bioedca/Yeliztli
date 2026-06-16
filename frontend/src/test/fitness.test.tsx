/** Tests for the Gene Fitness UI (P3-47, T3-65). */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/fitness/PathwayCard"
import type { PathwaySummary } from "@/types/fitness"

// ── Fixtures ──────────────────────────────────────────────────────────

const ENDURANCE_PATHWAY: PathwaySummary = {
  pathway_id: "endurance",
  pathway_name: "Endurance",
  level: "Elevated",
  evidence_level: 3,
  called_snps: 3,
  total_snps: 4,
  missing_snps: ["rs4341"],
  pmids: ["12879365", "14614113"],
}

const POWER_PATHWAY: PathwaySummary = {
  pathway_id: "power",
  pathway_name: "Power",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: ["12879365"],
}

const RECOVERY_PATHWAY: PathwaySummary = {
  pathway_id: "recovery_injury",
  pathway_name: "Recovery & Injury",
  level: "Standard",
  evidence_level: 1,
  called_snps: 2,
  total_snps: 3,
  missing_snps: ["rs12722"],
  pmids: [],
}

const TRAINING_PATHWAY: PathwaySummary = {
  pathway_id: "training_response",
  pathway_name: "Training Response",
  level: "Elevated",
  evidence_level: 2,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: ["18285522"],
}

// A Standard-level pathway whose only called SNP is INDETERMINATE. Without the
// caveat this looks identical to a confidently-clear "Standard" pathway
// (#270/#356/#360).
const INDETERMINATE_PATHWAY: PathwaySummary = {
  pathway_id: "training_response",
  pathway_name: "Training Response",
  level: "Standard",
  evidence_level: 1,
  called_snps: 1,
  total_snps: 1,
  missing_snps: [],
  indeterminate_snps: ["rs9939609"],
  pmids: [],
}

// ── PathwayCard tests ─────────────────────────────────────────────────

describe("PathwayCard", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders pathway name", () => {
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Endurance")).toBeInTheDocument()
  })

  it("shows Elevated badge for Elevated level", () => {
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Elevated")).toBeInTheDocument()
  })

  it("shows Moderate badge for Moderate level", () => {
    render(<PathwayCard pathway={POWER_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Moderate")).toBeInTheDocument()
  })

  it("shows Standard badge for Standard level", () => {
    render(<PathwayCard pathway={RECOVERY_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    expect(screen.getByLabelText("3 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders SNP coverage count", () => {
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("3/4 SNPs called")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    await user.click(
      screen.getByRole("button", {
        name: "Endurance — Elevated",
      }),
    )
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", {
      name: "Endurance — Elevated",
    })
    card.focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("has accessible role and label", () => {
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByRole("button", {
        name: "Endurance — Elevated",
      }),
    ).toBeInTheDocument()
  })

  it("renders pathway description for endurance", () => {
    render(<PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Aerobic capacity.*oxygen utilization/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for power", () => {
    render(<PathwayCard pathway={POWER_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Explosive strength.*fast-twitch/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for recovery", () => {
    render(<PathwayCard pathway={RECOVERY_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Injury susceptibility.*connective tissue/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for training response", () => {
    render(<PathwayCard pathway={TRAINING_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Adaptation rate.*exercise/),
    ).toBeInTheDocument()
  })

  it("shows selected state when selected prop is true", () => {
    render(
      <PathwayCard pathway={ENDURANCE_PATHWAY} onClick={onClick} selected />,
    )
    const card = screen.getByRole("button", {
      name: "Endurance — Elevated",
    })
    expect(card).toHaveAttribute("data-selected", "true")
  })

  it("renders all four pathway cards with correct data", () => {
    const pathways = [ENDURANCE_PATHWAY, POWER_PATHWAY, RECOVERY_PATHWAY, TRAINING_PATHWAY]
    for (const pathway of pathways) {
      const { unmount } = render(
        <PathwayCard pathway={pathway} onClick={onClick} />,
      )
      expect(screen.getByText(pathway.pathway_name)).toBeInTheDocument()
      unmount()
    }
  })

  // ── Indeterminate caveat (#360/#608) ────────────────────────────────

  it("renders a neutral indeterminate caveat when indeterminate_snps present", () => {
    render(<PathwayCard pathway={INDETERMINATE_PATHWAY} onClick={onClick} />)
    const caveat = screen.getByTestId("pathway-indeterminate-caveat")
    expect(caveat).toBeInTheDocument()
    expect(caveat).toHaveTextContent(/not interpreted/i)
    expect(caveat).toHaveTextContent(/see details/i)
    expect(caveat).not.toHaveTextContent(/strand-unresolved/i)
    // Singular wording for exactly one indeterminate variant.
    expect(caveat).toHaveTextContent(/1 variant\b/)
  })

  it("does not call unmodeled-allele indeterminate variants strand-unresolved", () => {
    render(
      <PathwayCard
        pathway={{
          ...INDETERMINATE_PATHWAY,
          pathway_id: "power",
          pathway_name: "Power",
          indeterminate_snps: ["rs4341"],
        }}
        onClick={onClick}
      />,
    )
    const caveat = screen.getByTestId("pathway-indeterminate-caveat")
    expect(caveat).toHaveTextContent(/1 variant observed but not interpreted/i)
    expect(caveat).not.toHaveTextContent(/strand-unresolved/i)
  })

  it("does NOT render the caveat for a confidently-clear Standard pathway", () => {
    // RECOVERY_PATHWAY is Standard with no indeterminate_snps.
    render(<PathwayCard pathway={RECOVERY_PATHWAY} onClick={onClick} />)
    expect(screen.queryByTestId("pathway-indeterminate-caveat")).not.toBeInTheDocument()
  })

  it("does NOT render the caveat when indeterminate_snps is empty", () => {
    render(
      <PathwayCard
        pathway={{ ...INDETERMINATE_PATHWAY, indeterminate_snps: [] }}
        onClick={onClick}
      />,
    )
    expect(screen.queryByTestId("pathway-indeterminate-caveat")).not.toBeInTheDocument()
  })

  it("pluralizes the caveat for multiple indeterminate variants", () => {
    render(
      <PathwayCard
        pathway={{
          ...INDETERMINATE_PATHWAY,
          indeterminate_snps: ["rs9939609", "rs1815739"],
        }}
        onClick={onClick}
      />,
    )
    expect(screen.getByTestId("pathway-indeterminate-caveat")).toHaveTextContent(/2 variants\b/)
  })

  it("shows both the Standard badge and the caveat (not just 'no concern')", () => {
    // The #270 regression: an all-indeterminate pathway must NOT read as a
    // confidently-clear Standard. Both the level badge and the caveat appear.
    render(<PathwayCard pathway={INDETERMINATE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
    expect(screen.getByTestId("pathway-indeterminate-caveat")).toBeInTheDocument()
  })
})
