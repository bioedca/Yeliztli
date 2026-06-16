/** Tests for the Pharmacogenomics UI (P3-06, T3-10). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import MetabolizerCard from "@/components/pharmacogenomics/MetabolizerCard"
import DrugTable from "@/components/pharmacogenomics/DrugTable"
import type { GeneSummary, DrugListItem } from "@/types/pharmacogenomics"

// ── Fixtures ──────────────────────────────────────────────────────────

const COMPLETE_GENE: GeneSummary = {
  gene: "CYP2C19",
  diplotype: "*1/*2",
  phenotype: "Intermediate Metabolizer",
  call_confidence: "Complete",
  confidence_note: null,
  activity_score: 1.0,
  ehr_notation: "CYP2C19 *1/*2",
  evidence_level: 4,
  involved_rsids: ["rs4244285"],
  drugs: ["clopidogrel", "omeprazole", "voriconazole"],
  gene_caveat: null,
}

const NORMAL_GENE: GeneSummary = {
  ...COMPLETE_GENE,
  diplotype: "*1/*1",
  phenotype: "Normal Metabolizer",
  activity_score: 2.0,
  ehr_notation: "CYP2C19 *1/*1",
}

const POOR_GENE: GeneSummary = {
  ...COMPLETE_GENE,
  diplotype: "*2/*2",
  phenotype: "Poor Metabolizer",
  activity_score: 0.0,
  ehr_notation: "CYP2C19 *2/*2",
}

const PARTIAL_GENE: GeneSummary = {
  gene: "CYP2D6",
  diplotype: "*1/*4",
  phenotype: "Intermediate Metabolizer",
  call_confidence: "Partial",
  confidence_note:
    "SNP-based alleles called, but structural variants cannot be excluded from array data.",
  activity_score: 1.0,
  ehr_notation: "CYP2D6 *1/*4",
  evidence_level: 3,
  involved_rsids: ["rs3892097"],
  drugs: ["codeine", "tramadol"],
  gene_caveat: null,
}

const INSUFFICIENT_GENE: GeneSummary = {
  gene: "CYP2B6",
  diplotype: null,
  phenotype: null,
  call_confidence: "Insufficient",
  confidence_note: "Key defining rsids not on the 23andMe array.",
  activity_score: null,
  ehr_notation: null,
  evidence_level: null,
  involved_rsids: [],
  drugs: ["efavirenz"],
  gene_caveat: null,
}

const DPYD_GENE: GeneSummary = {
  gene: "DPYD",
  diplotype: "*1/*2A",
  phenotype: "Intermediate Metabolizer",
  call_confidence: "Complete",
  confidence_note: null,
  activity_score: 1.0,
  ehr_notation: "DPYD Intermediate Metabolizer",
  evidence_level: 4,
  involved_rsids: ["rs3918290"],
  drugs: ["fluorouracil", "capecitabine"],
  gene_caveat:
    "DPYD result interpretation (context only). This panel types only 4 DPYD variants. " +
    "A normal-metabolizer / negative result does NOT rule out DPD deficiency, which can " +
    "cause severe or fatal fluoropyrimidine toxicity.",
}

const DRUG_LIST: DrugListItem[] = [
  { drug: "clopidogrel", genes: ["CYP2C19"], classification: "A" },
  { drug: "codeine", genes: ["CYP2D6"], classification: "A" },
  { drug: "omeprazole", genes: ["CYP2C19"], classification: "B" },
  { drug: "warfarin", genes: ["CYP2C9", "VKORC1"], classification: "A" },
]

// ── MetabolizerCard tests ─────────────────────────────────────────────

describe("MetabolizerCard", () => {
  it("renders gene name and diplotype", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(screen.getByText("CYP2C19")).toBeInTheDocument()
    expect(screen.getByText("*1/*2")).toBeInTheDocument()
  })

  it("renders metabolizer phenotype", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(screen.getByText("Intermediate Metabolizer")).toBeInTheDocument()
  })

  it("shows Complete confidence indicator", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(screen.getByText("Complete")).toBeInTheDocument()
  })

  it("colors the card by phenotype rather than Complete confidence", () => {
    const { rerender } = render(<MetabolizerCard gene={POOR_GENE} />)
    const poorCard = screen.getByRole("article", {
      name: "CYP2C19 metabolizer status",
    })
    expect(poorCard).toHaveClass("bg-amber-50/70")
    expect(poorCard).not.toHaveClass("bg-emerald-50/60")
    expect(screen.getByText("Complete")).toHaveClass("text-emerald-700")

    rerender(<MetabolizerCard gene={NORMAL_GENE} />)
    const normalCard = screen.getByRole("article", {
      name: "CYP2C19 metabolizer status",
    })
    expect(normalCard).toHaveClass("bg-emerald-50/60")
    expect(normalCard).not.toHaveClass("bg-amber-50/70")
  })

  it("shows Partial confidence indicator with note", () => {
    render(<MetabolizerCard gene={PARTIAL_GENE} />)
    expect(screen.getByText("Partial")).toBeInTheDocument()
    expect(
      screen.getByText(/structural variants cannot be excluded/),
    ).toBeInTheDocument()
  })

  it("shows Insufficient confidence indicator", () => {
    render(<MetabolizerCard gene={INSUFFICIENT_GENE} />)
    expect(screen.getByText("Insufficient")).toBeInTheDocument()
  })

  it("shows 'No result available' for null phenotype", () => {
    render(<MetabolizerCard gene={INSUFFICIENT_GENE} />)
    expect(screen.getByText("No result available")).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(screen.getByLabelText("4 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders activity score", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(screen.getByText("Activity: 1")).toBeInTheDocument()
  })

  it("shows associated drugs", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(screen.getByText(/clopidogrel/)).toBeInTheDocument()
  })

  it("has accessible role and label", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(
      screen.getByRole("article", { name: "CYP2C19 metabolizer status" }),
    ).toBeInTheDocument()
  })

  it("renders the DPYD interpretive caveat as a note", () => {
    render(<MetabolizerCard gene={DPYD_GENE} />)
    const note = screen.getByRole("note", { name: "DPYD interpretation caveat" })
    expect(note).toBeInTheDocument()
    expect(note).toHaveTextContent(/does NOT rule out DPD deficiency/i)
    expect(note).toHaveTextContent(/fatal fluoropyrimidine toxicity/i)
  })

  it("does not render a caveat note when gene_caveat is null", () => {
    render(<MetabolizerCard gene={COMPLETE_GENE} />)
    expect(screen.queryByRole("note")).not.toBeInTheDocument()
  })
})

// ── DrugTable tests ───────────────────────────────────────────────────

describe("DrugTable", () => {
  const onSelectDrug = vi.fn()

  beforeEach(() => {
    onSelectDrug.mockClear()
  })

  it("renders all drugs", () => {
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    expect(screen.getByText("clopidogrel")).toBeInTheDocument()
    expect(screen.getByText("codeine")).toBeInTheDocument()
    expect(screen.getByText("omeprazole")).toBeInTheDocument()
    expect(screen.getByText("warfarin")).toBeInTheDocument()
  })

  it("shows total count", () => {
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    expect(screen.getByText("4 of 4 drugs")).toBeInTheDocument()
  })

  it("filters drugs by search", async () => {
    const user = userEvent.setup()
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    await user.type(screen.getByLabelText("Search drugs or genes"), "clop")
    expect(screen.getByText("clopidogrel")).toBeInTheDocument()
    expect(screen.queryByText("codeine")).not.toBeInTheDocument()
    expect(screen.getByText("1 of 4 drugs")).toBeInTheDocument()
  })

  it("filters drugs by gene name", async () => {
    const user = userEvent.setup()
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    await user.type(screen.getByLabelText("Search drugs or genes"), "CYP2D6")
    expect(screen.getByText("codeine")).toBeInTheDocument()
    expect(screen.queryByText("clopidogrel")).not.toBeInTheDocument()
  })

  it("calls onSelectDrug when row is clicked", async () => {
    const user = userEvent.setup()
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    await user.click(screen.getByText("clopidogrel"))
    expect(onSelectDrug).toHaveBeenCalledWith("clopidogrel")
  })

  it("shows CPIC classification badges", () => {
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    const badges = screen.getAllByText("A")
    expect(badges.length).toBe(3) // clopidogrel, codeine, warfarin
  })

  it("shows empty state when no results match", async () => {
    const user = userEvent.setup()
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    await user.type(screen.getByLabelText("Search drugs or genes"), "nonexistent")
    expect(screen.getByText("No drugs match your search.")).toBeInTheDocument()
  })

  it("has accessible search input", () => {
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    expect(screen.getByLabelText("Search drugs or genes")).toBeInTheDocument()
  })

  it("has accessible table role", () => {
    render(
      <DrugTable drugs={DRUG_LIST} onSelectDrug={onSelectDrug} selectedDrug={null} />,
    )
    expect(
      screen.getByRole("grid", { name: "Drug interactions table" }),
    ).toBeInTheDocument()
  })
})
