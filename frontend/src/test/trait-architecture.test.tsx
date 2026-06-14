import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import TraitArchitectureCard from "@/components/ui/TraitArchitectureCard"

describe("TraitArchitectureCard", () => {
  it("renders the collapsible explainer", () => {
    render(<TraitArchitectureCard />)
    expect(screen.getByTestId("trait-architecture-card")).toBeInTheDocument()
    expect(screen.getByText("How to read a polygenic score")).toBeInTheDocument()
  })

  it("explains the three architecture points", () => {
    render(<TraitArchitectureCard />)
    expect(screen.getByText(/Most heritability is missing/)).toBeInTheDocument()
    expect(screen.getByText(/h²_twin/)).toBeInTheDocument()
    expect(screen.getByText(/Accuracy drops across ancestries/)).toBeInTheDocument()
    // The cross-ancestry stat names its source paper + the specific statistic.
    expect(screen.getByText(/Pearson r ≈ −0.95 across 84\s+traits/)).toBeInTheDocument()
    expect(screen.getByText(/Calibration is not accuracy/)).toBeInTheDocument()
  })

  it("renders the full canonical Ding 2023 citation (volume, pages, DOI)", () => {
    // Pins the rendered citation so a page/DOI/volume drift fails here too; the
    // backend↔frontend parity guard lives in test_trait_architecture_parity.py.
    render(<TraitArchitectureCard />)
    expect(
      screen.getByText(/Nature 618:774-781 \(2023\); doi:10\.1038\/s41586-023-06079-4/),
    ).toBeInTheDocument()
  })
})
