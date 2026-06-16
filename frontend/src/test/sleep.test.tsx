/** Tests for the Gene Sleep UI (P3-50). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/sleep/PathwayCard"
import type { PathwaySummary } from "@/types/sleep"

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

  it("shows Standard badge for Standard level", () => {
    render(<PathwayCard pathway={QUALITY_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
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
    expect(
      screen.getByText(/Genetic susceptibility.*insomnia/),
    ).toBeInTheDocument()
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
