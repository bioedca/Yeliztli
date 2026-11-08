import { describe, expect, it, vi } from "vitest"
import { render, screen } from "./test-utils"
import NutrigenomicsCard from "@/components/nutrigenomics/PathwayCard"
import MethylationCard from "@/components/methylation/PathwayScoreBar"
import SleepCard from "@/components/sleep/PathwayCard"
import SkinCard from "@/components/skin/PathwayCard"
import AllergyCard from "@/components/allergy/PathwayCard"
import GeneHealthCard from "@/components/gene-health/PathwayCard"
import PathwayFlowDiagram from "@/components/methylation/PathwayFlowDiagram"

const base = {
  pathway_id: "folate_mthfr",
  pathway_name: "Synthetic pathway",
  level: "Standard" as const,
  evidence_level: 2,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [] as string[],
  indeterminate_snps: [] as string[],
  pmids: [],
  additive_promoted: false,
  hla_proxy_lookup: null,
}

describe.each([
  ["nutrigenomics", NutrigenomicsCard],
  ["methylation", MethylationCard],
  ["sleep", SleepCard],
  ["skin", SkinCard],
  ["allergy", AllergyCard],
  ["gene health", GeneHealthCard],
] as const)("%s summary coverage", (_name, Card) => {
  it("shows Not Assessed and exposes the reason to sighted and screen-reader users", () => {
    const onClick = vi.fn()
    render(<Card pathway={{ ...base, indeterminate_snps: ["rsYELIZTLI0", "rsYELIZTLI1"] }} onClick={onClick} />)
    expect(screen.getByText("Not Assessed")).toBeInTheDocument()
    expect(screen.queryByText("Standard")).not.toBeInTheDocument()
    expect(screen.getByTestId("pathway-indeterminate-caveat")).toHaveTextContent(
      "2 variants observed but not interpreted — see details",
    )
    expect(screen.getByRole("button")).toHaveAccessibleName(
      "Synthetic pathway — Not Assessed; 2 variants observed but not interpreted — see details",
    )
  })

  it("keeps a mixed interpreted result Standard while disclosing the withheld call", () => {
    render(<Card pathway={{ ...base, indeterminate_snps: ["rsYELIZTLI0"] }} onClick={() => {}} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
    expect(screen.queryByText("Not Assessed")).not.toBeInTheDocument()
    expect(screen.getByTestId("pathway-indeterminate-caveat")).toHaveTextContent(
      "1 variant observed but not interpreted — see details",
    )
  })

  it("preserves genuine Standard and missing-site controls", () => {
    const { rerender } = render(<Card pathway={base} onClick={() => {}} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
    expect(screen.queryByTestId("pathway-indeterminate-caveat")).not.toBeInTheDocument()
    rerender(<Card pathway={{ ...base, called_snps: 1, missing_snps: ["rsYELIZTLI1"] }} onClick={() => {}} />)
    expect(screen.getByText("Tested Standard")).toBeInTheDocument()
    expect(screen.getByTestId("pathway-coverage-caveat")).toHaveTextContent("1 tracked SNP (1 off-chip) not assessed")
  })
})

it("does not label a methylation diagram node Standard when nothing was interpreted", () => {
  render(<PathwayFlowDiagram pathways={[{ ...base, indeterminate_snps: ["rsYELIZTLI0", "rsYELIZTLI1"] }]} selectedPathwayId={null} onSelectPathway={() => {}} />)
  const node = screen.getByRole("button", { name: "Folate & MTHFR — Not Assessed" })
  expect(node.querySelector("rect")).toHaveClass("fill-muted")
})
