/** Tests for the MTHFR & Methylation UI (P3-53, T3-68). */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayScoreBar from "@/components/methylation/PathwayScoreBar"
import PathwayFlowDiagram from "@/components/methylation/PathwayFlowDiagram"
import CompoundHetBanner from "@/components/methylation/CompoundHetBanner"
import type { PathwaySummary, CompoundHetInfo } from "@/types/methylation"

// ── Fixtures ──────────────────────────────────────────────────────────

const FOLATE_PATHWAY: PathwaySummary = {
  pathway_id: "folate_mthfr",
  pathway_name: "Folate & MTHFR",
  level: "Elevated",
  evidence_level: 2,
  called_snps: 5,
  total_snps: 8,
  missing_snps: ["rs70991108", "rs202676", "rs3758149"],
  pmids: ["19151529", "22012856"],
  additive_promoted: false,
}

const METHIONINE_PATHWAY: PathwaySummary = {
  pathway_id: "methionine_cycle",
  pathway_name: "Methionine Cycle",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 4,
  total_snps: 7,
  missing_snps: ["rs2275565", "rs2228611", "rs1569942"],
  pmids: ["16825135"],
  additive_promoted: false,
}

const TRANSSULFURATION_PATHWAY: PathwaySummary = {
  pathway_id: "transsulfuration",
  pathway_name: "Transsulfuration",
  level: "Standard",
  evidence_level: 1,
  called_snps: 5,
  total_snps: 7,
  missing_snps: ["rs1001761", "rs3170633"],
  pmids: [],
  additive_promoted: false,
}

const BH4_PATHWAY: PathwaySummary = {
  pathway_id: "bh4_neurotransmitter",
  pathway_name: "BH4 & Neurotransmitter Synthesis",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 6,
  total_snps: 7,
  missing_snps: ["rs10483639"],
  pmids: ["17913697"],
  additive_promoted: false,
}

const CHOLINE_PATHWAY: PathwaySummary = {
  pathway_id: "choline_betaine",
  pathway_name: "Choline & Betaine",
  level: "Moderate",
  evidence_level: 1,
  called_snps: 4,
  total_snps: 6,
  missing_snps: ["rs2236225", "rs12325817"],
  pmids: [],
  additive_promoted: true,
}

const ALL_PATHWAYS = [
  FOLATE_PATHWAY,
  METHIONINE_PATHWAY,
  TRANSSULFURATION_PATHWAY,
  BH4_PATHWAY,
  CHOLINE_PATHWAY,
]

const COMPOUND_HET: CompoundHetInfo = {
  is_compound_het: true,
  is_double_homozygous: false,
  label: "MTHFR compound heterozygote",
  c677t_genotype: "GA",
  a1298c_genotype: "AC",
  finding_text:
    "Compound heterozygote for MTHFR C677T and A1298C — may have moderately reduced enzyme activity.",
}

const DOUBLE_HOMOZYGOUS: CompoundHetInfo = {
  is_compound_het: false,
  is_double_homozygous: true,
  label: "MTHFR double variant",
  c677t_genotype: "AA",
  a1298c_genotype: "CC",
  finding_text:
    "Double variant for MTHFR C677T and A1298C — significantly reduced enzyme activity.",
}

const NO_COMPOUND_HET: CompoundHetInfo = {
  is_compound_het: false,
  is_double_homozygous: false,
  label: null,
  c677t_genotype: null,
  a1298c_genotype: null,
  finding_text: null,
}

// ── PathwayScoreBar tests ────────────────────────────────────────────

describe("PathwayScoreBar", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders pathway name", () => {
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Folate & MTHFR")).toBeInTheDocument()
  })

  it("shows Elevated badge for Elevated level", () => {
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Elevated")).toBeInTheDocument()
  })

  it("shows Moderate badge for Moderate level", () => {
    render(<PathwayScoreBar pathway={METHIONINE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Moderate")).toBeInTheDocument()
  })

  it("shows Standard badge for Standard level", () => {
    render(<PathwayScoreBar pathway={TRANSSULFURATION_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    expect(screen.getByLabelText("2 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders SNP coverage count", () => {
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("5/8 SNPs called")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    await user.click(
      screen.getByRole("button", { name: "Folate & MTHFR — Elevated" }),
    )
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", { name: "Folate & MTHFR — Elevated" })
    card.focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("has accessible role and label", () => {
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByRole("button", { name: "Folate & MTHFR — Elevated" }),
    ).toBeInTheDocument()
  })

  it("shows selected state when selected prop is true", () => {
    render(
      <PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} selected />,
    )
    const card = screen.getByRole("button", { name: "Folate & MTHFR — Elevated" })
    expect(card).toHaveAttribute("data-selected", "true")
  })

  it("shows Promoted indicator for additive promoted pathway", () => {
    render(<PathwayScoreBar pathway={CHOLINE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Promoted")).toBeInTheDocument()
  })

  it("does not show Promoted indicator for non-promoted pathway", () => {
    render(<PathwayScoreBar pathway={FOLATE_PATHWAY} onClick={onClick} />)
    expect(screen.queryByText("Promoted")).not.toBeInTheDocument()
  })

  it("renders all five pathway cards", () => {
    for (const pathway of ALL_PATHWAYS) {
      const { unmount } = render(
        <PathwayScoreBar pathway={pathway} onClick={onClick} />,
      )
      expect(screen.getByText(pathway.pathway_name)).toBeInTheDocument()
      unmount()
    }
  })
})

// ── PathwayFlowDiagram tests ────────────────────────────────────────

describe("PathwayFlowDiagram", () => {
  const onSelect = vi.fn()

  beforeEach(() => {
    onSelect.mockClear()
  })

  it("renders the SVG diagram with accessible label", () => {
    render(
      <PathwayFlowDiagram
        pathways={ALL_PATHWAYS}
        selectedPathwayId={null}
        onSelectPathway={onSelect}
      />,
    )
    expect(
      screen.getByRole("img", {
        name: /Methylation pathway flow diagram/,
      }),
    ).toBeInTheDocument()
  })

  it("renders buttons for all 5 pathways", () => {
    render(
      <PathwayFlowDiagram
        pathways={ALL_PATHWAYS}
        selectedPathwayId={null}
        onSelectPathway={onSelect}
      />,
    )
    expect(screen.getByRole("button", { name: /Folate.*Elevated/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Methionine.*Moderate/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /sulfuration.*Standard/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /BH4.*Moderate/ })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Choline.*Moderate/ })).toBeInTheDocument()
  })

  it("calls onSelectPathway when a node is clicked", async () => {
    const user = userEvent.setup()
    render(
      <PathwayFlowDiagram
        pathways={ALL_PATHWAYS}
        selectedPathwayId={null}
        onSelectPathway={onSelect}
      />,
    )
    await user.click(screen.getByRole("button", { name: /Folate.*Elevated/ }))
    expect(onSelect).toHaveBeenCalledWith("folate_mthfr")
  })

  it("keeps the selected node ring thicker than unselected node borders", () => {
    render(
      <PathwayFlowDiagram
        pathways={ALL_PATHWAYS}
        selectedPathwayId="folate_mthfr"
        onSelectPathway={onSelect}
      />,
    )
    const selectedNode = screen.getByRole("button", { name: /Folate.*Elevated/ })
    const unselectedNode = screen.getByRole("button", { name: /Methionine.*Moderate/ })
    const selectedRect = selectedNode.querySelector("rect")
    const unselectedRect = unselectedNode.querySelector("rect")

    expect(selectedRect).toHaveAttribute(
      "class",
      expect.stringContaining("stroke-[3]"),
    )
    expect(selectedRect).not.toHaveAttribute(
      "class",
      expect.stringContaining("stroke-[2]"),
    )
    expect(unselectedRect).toHaveAttribute(
      "class",
      expect.stringContaining("stroke-[2]"),
    )
  })

  it("places diagonal connector endpoints on node edges", () => {
    const { container } = render(
      <PathwayFlowDiagram
        pathways={ALL_PATHWAYS}
        selectedPathwayId={null}
        onSelectPathway={onSelect}
      />,
    )
    const lines = container.querySelectorAll("line")
    const folateToBh4 = lines[2]
    const methionineToCholine = lines[3]
    const attrNumber = (element: Element, attr: string) =>
      Number(element.getAttribute(attr))

    expect(attrNumber(folateToBh4, "y1")).toBeCloseTo(88)
    expect(attrNumber(folateToBh4, "y2")).toBeCloseTo(152)
    expect(attrNumber(methionineToCholine, "y1")).toBeCloseTo(88)
    expect(attrNumber(methionineToCholine, "y2")).toBeCloseTo(152)
  })
})

// ── CompoundHetBanner tests ──────────────────────────────────────────

describe("CompoundHetBanner", () => {
  it("renders compound heterozygote banner", () => {
    render(<CompoundHetBanner compoundHet={COMPOUND_HET} />)
    expect(
      screen.getByText("MTHFR Compound Heterozygote Detected"),
    ).toBeInTheDocument()
  })

  it("shows finding text", () => {
    render(<CompoundHetBanner compoundHet={COMPOUND_HET} />)
    expect(
      screen.getByText(/moderately reduced enzyme activity/),
    ).toBeInTheDocument()
  })

  it("displays C677T genotype", () => {
    render(<CompoundHetBanner compoundHet={COMPOUND_HET} />)
    expect(screen.getByText("GA")).toBeInTheDocument()
  })

  it("displays A1298C genotype", () => {
    render(<CompoundHetBanner compoundHet={COMPOUND_HET} />)
    expect(screen.getByText("AC")).toBeInTheDocument()
  })

  it("renders double homozygous banner", () => {
    render(<CompoundHetBanner compoundHet={DOUBLE_HOMOZYGOUS} />)
    expect(
      screen.getByText("MTHFR Double Variant Detected"),
    ).toBeInTheDocument()
  })

  it("renders nothing when neither compound het nor double homozygous", () => {
    const { container } = render(<CompoundHetBanner compoundHet={NO_COMPOUND_HET} />)
    expect(container.innerHTML).toBe("")
  })

  it("has alert role for accessibility", () => {
    render(<CompoundHetBanner compoundHet={COMPOUND_HET} />)
    expect(screen.getByRole("alert")).toBeInTheDocument()
  })
})
