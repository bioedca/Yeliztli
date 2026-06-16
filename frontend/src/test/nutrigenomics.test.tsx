/// <reference types="node" />
/** Tests for the Nutrigenomics UI (P3-11, T3-11). */

import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/nutrigenomics/PathwayCard"
import {
  PATHWAY_DESCRIPTIONS,
} from "@/components/nutrigenomics/pathwayDescriptions"
import type { PathwaySummary } from "@/types/nutrigenomics"

interface NutrigenomicsPanelJson {
  pathways: Array<{ id: string }>
}

// ── Fixtures ──────────────────────────────────────────────────────────

const ELEVATED_PATHWAY: PathwaySummary = {
  pathway_id: "folate_metabolism",
  pathway_name: "Folate Metabolism",
  level: "Elevated",
  evidence_level: 2,
  called_snps: 2,
  total_snps: 3,
  missing_snps: ["rs1801394"],
  pmids: ["19151529", "22012856"],
}

const MODERATE_PATHWAY: PathwaySummary = {
  pathway_id: "vitamin_d",
  pathway_name: "Vitamin D",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 3,
  total_snps: 4,
  missing_snps: ["rs10741657"],
  pmids: ["20541252"],
}

const STANDARD_PATHWAY: PathwaySummary = {
  pathway_id: "omega_3",
  pathway_name: "Omega-3 Fatty Acids",
  level: "Standard",
  evidence_level: 2,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: [],
}

const LACTOSE_PATHWAY: PathwaySummary = {
  pathway_id: "lactose",
  pathway_name: "Lactose Tolerance",
  level: "Elevated",
  evidence_level: 3,
  called_snps: 1,
  total_snps: 2,
  missing_snps: ["rs182549"],
  pmids: ["12068507"],
}

const VITAMIN_B6_PATHWAY: PathwaySummary = {
  pathway_id: "vitamin_b6",
  pathway_name: "Vitamin B6",
  level: "Moderate",
  evidence_level: 2,
  called_snps: 1,
  total_snps: 1,
  missing_snps: [],
  pmids: ["19744961"],
}

function loadPanelPathwayIds(): string[] {
  const panelPath = resolve(
    process.cwd(),
    "../backend/data/panels/nutrigenomics_panel.json",
  )
  const panel = JSON.parse(
    readFileSync(panelPath, "utf8"),
  ) as NutrigenomicsPanelJson
  return panel.pathways.map(({ id }) => id)
}

// ── PathwayCard tests ─────────────────────────────────────────────────

describe("PathwayCard", () => {
  const onClick = vi.fn()

  it("renders pathway name", () => {
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Folate Metabolism")).toBeInTheDocument()
  })

  it("shows Elevated Consideration badge for Elevated level", () => {
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Elevated Consideration")).toBeInTheDocument()
  })

  it("shows Moderate Consideration badge for Moderate level", () => {
    render(<PathwayCard pathway={MODERATE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Moderate Consideration")).toBeInTheDocument()
  })

  it("shows Standard badge for Standard level", () => {
    render(<PathwayCard pathway={STANDARD_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    expect(screen.getByLabelText("2 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders SNP coverage count", () => {
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("2/3 SNPs called")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    onClick.mockClear()
    const user = userEvent.setup()
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    await user.click(
      screen.getByRole("button", {
        name: "Folate Metabolism — Elevated Consideration",
      }),
    )
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("calls onClick on Enter key", async () => {
    onClick.mockClear()
    const user = userEvent.setup()
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", {
      name: "Folate Metabolism — Elevated Consideration",
    })
    card.focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("has accessible role and label", () => {
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByRole("button", {
        name: "Folate Metabolism — Elevated Consideration",
      }),
    ).toBeInTheDocument()
  })

  it("renders pathway description", () => {
    render(<PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Folate.*essential for DNA synthesis/),
    ).toBeInTheDocument()
  })

  it("renders the Vitamin B6 pathway description", () => {
    render(<PathwayCard pathway={VITAMIN_B6_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/pyridoxal 5'-phosphate.*circulating levels/i),
    ).toBeInTheDocument()
  })

  it("has a description for every backend nutrigenomics pathway", () => {
    const describedPathways = new Set(Object.keys(PATHWAY_DESCRIPTIONS))
    const missingDescriptions = loadPanelPathwayIds().filter(
      (pathwayId) => !describedPathways.has(pathwayId),
    )

    expect(missingDescriptions).toEqual([])
  })

  it("renders lactose pathway correctly", () => {
    render(<PathwayCard pathway={LACTOSE_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Lactose Tolerance")).toBeInTheDocument()
    expect(screen.getByText("Elevated Consideration")).toBeInTheDocument()
    expect(screen.getByLabelText("3 of 4 stars evidence")).toBeInTheDocument()
    expect(screen.getByText("1/2 SNPs called")).toBeInTheDocument()
  })

  it("shows selected state when selected prop is true", () => {
    render(
      <PathwayCard pathway={ELEVATED_PATHWAY} onClick={onClick} selected />,
    )
    const card = screen.getByRole("button", {
      name: "Folate Metabolism — Elevated Consideration",
    })
    expect(card).toHaveAttribute("data-selected", "true")
  })
})
