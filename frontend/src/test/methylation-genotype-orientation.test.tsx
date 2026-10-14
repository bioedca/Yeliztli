/** The Methylation Advanced View says which strand a genotype was read on (#2103).
 *
 * Variant names such as "C677T" follow the gene's transcript, while an array
 * reports each probe on its own design strand, so a name and a genotype can
 * share no letter ("C677T — Genotype: GG"). The backend tells the strand from
 * the sample's annotated genome alleles; the view labels it and explains it.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, within } from "./test-utils"

vi.mock("@/api/methylation", () => ({ useMethylationPathwayDetail: vi.fn() }))

import MethylationPanel from "@/components/methylation/PathwayDetailPanel"
import { useMethylationPathwayDetail } from "@/api/methylation"

const hook = useMethylationPathwayDetail as unknown as ReturnType<typeof vi.fn>

function snp(overrides: Record<string, unknown>) {
  return {
    gene: "MTHFR",
    category: "Standard",
    effect_summary: "Effect.",
    evidence_level: 3,
    recommendation: null,
    pmids: [],
    coverage_note: null,
    ...overrides,
  }
}

// 23andMe-style: read on the genome's plus strand.
const C677T_PLUS = snp({ rsid: "rs1801133", variant_name: "C677T", genotype: "GG", genotype_strand: "plus" })
// AncestryDNA-style: read on the minus strand.
const A1298C_MINUS = snp({
  rsid: "rs1801131",
  variant_name: "A1298C",
  genotype: "AC",
  category: "Moderate",
  genotype_strand: "minus",
})
// A C/G palindrome: both strands read the same letters.
const PALINDROME = snp({
  rsid: "rs1801198",
  gene: "TCN2",
  variant_name: "C776G (Pro259Arg)",
  genotype: "CC",
  category: "Indeterminate",
  genotype_strand: null,
})
// Vendor insertion/deletion tokens name no strand.
const DHFR_INDEL = snp({
  rsid: "rs70991108",
  gene: "DHFR",
  variant_name: "19bp deletion",
  genotype: "ID",
  genotype_strand: null,
})

function renderPanel(snpDetails: unknown[]) {
  hook.mockReturnValue({
    data: {
      pathway_id: "folate_mthfr",
      pathway_name: "Folate & MTHFR",
      level: "Moderate",
      evidence_level: 3,
      called_snps: snpDetails.length,
      total_snps: snpDetails.length,
      missing_snps: [],
      no_call_snps: [],
      pmids: [],
      snp_details: snpDetails,
    },
    isLoading: false,
    isError: false,
    error: null,
  })
  render(
    <MethylationPanel
      pathwayId="folate_mthfr"
      pathwayName="Folate & MTHFR"
      sampleId={1}
      onClose={() => {}}
    />,
  )
  fireEvent.click(screen.getByRole("button", { name: /Advanced View/i }))
}

/** The card that renders one rsid. */
function row(rsid: string) {
  const card = screen.getByText(rsid).closest(".rounded-lg")
  expect(card).not.toBeNull()
  return within(card as HTMLElement)
}

const NOTE = /Where a genotype is labelled plus or minus strand/
const ANY_LABEL = /\((plus|minus) strand\)/

describe("Methylation Advanced View genotype strand (#2103)", () => {
  beforeEach(() => {
    hook.mockReset()
  })

  it("labels each genotype with the strand the backend says it was read on", () => {
    renderPanel([C677T_PLUS, A1298C_MINUS])
    expect(row("rs1801133").getByText("GG")).toBeInTheDocument()
    expect(row("rs1801133").getByText("(plus strand)")).toBeInTheDocument()
    expect(row("rs1801131").getByText("AC")).toBeInTheDocument()
    expect(row("rs1801131").getByText("(minus strand)")).toBeInTheDocument()
    // Read as one phrase by screen readers and copy-paste, not "GG(plus strand)".
    expect(row("rs1801133").getByText(/^Genotype:/).closest("p")).toHaveTextContent(
      "Genotype: GG (plus strand)",
    )
  })

  it("explains the labels, scoped to the labelled genotypes", () => {
    renderPanel([C677T_PLUS, A1298C_MINUS])
    const note = screen.getByText(NOTE)
    expect(note).toHaveTextContent(/DNA changes, such as\s+C677T.*complementary/)
    // The complement rule is for nucleotide names only.
    expect(note).toHaveTextContent(/protein changes,\s+such as Val158Met.*cannot be compared/)
  })

  it("claims no strand when the backend could not tell it", () => {
    renderPanel([C677T_PLUS, PALINDROME, DHFR_INDEL])
    expect(row("rs1801198").getByText("CC")).toBeInTheDocument()
    expect(row("rs1801198").queryByText(ANY_LABEL)).not.toBeInTheDocument()
    expect(row("rs70991108").getByText("ID")).toBeInTheDocument()
    expect(row("rs70991108").queryByText(ANY_LABEL)).not.toBeInTheDocument()
  })

  it("claims no strand when the response has no strand field", () => {
    renderPanel([snp({ rsid: "rs1801133", variant_name: "C677T", genotype: "GG" })])
    expect(row("rs1801133").queryByText(ANY_LABEL)).not.toBeInTheDocument()
  })

  it("omits the note when no genotype is labelled", () => {
    renderPanel([PALINDROME, DHFR_INDEL])
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })
})
