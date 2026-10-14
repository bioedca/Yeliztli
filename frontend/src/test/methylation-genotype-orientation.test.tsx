/** The Methylation Advanced View says which strand a genotype is read on (#2103).
 *
 * Genotypes are shown on the genome's plus strand, while variant names such as
 * "C677T" follow the gene's transcript. For a gene transcribed from the minus
 * strand the two never share a letter ("C677T — Genotype: GG"), which reads as
 * a data error unless the orientation is stated.
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

const C677T = snp({ rsid: "rs1801133", variant_name: "C677T", genotype: "GG", strand_unresolved: false })
const A1298C = snp({
  rsid: "rs1801131",
  variant_name: "A1298C",
  genotype: "GT",
  category: "Moderate",
  strand_unresolved: false,
})
// Vendor insertion/deletion tokens name no strand at all.
const DHFR_INDEL = snp({
  rsid: "rs70991108",
  gene: "DHFR",
  variant_name: "19bp deletion",
  genotype: "ID",
  strand_unresolved: false,
})
// A palindromic homozygote whose strand the backend could not resolve.
const PALINDROME = snp({
  rsid: "rs1801198",
  gene: "TCN2",
  variant_name: "C776G (Pro259Arg)",
  genotype: "CC",
  category: "Indeterminate",
  coverage_note: "Strand-ambiguous palindromic SNP — confirm the genotyping strand.",
  strand_unresolved: true,
})
// An allele the panel does not model is Indeterminate too, but its strand is known.
const UNMODELLED = snp({
  rsid: "rs1677693",
  gene: "DHFR",
  variant_name: "DHFR intronic tagSNP",
  genotype: "GA",
  category: "Indeterminate",
  strand_unresolved: false,
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

const NOTE = /Genotypes are shown as read on the genome's plus strand/

describe("Methylation Advanced View genotype orientation (#2103)", () => {
  beforeEach(() => {
    hook.mockReset()
  })

  it("explains that genotypes and variant names can be in opposite orientations", () => {
    renderPanel([C677T, A1298C])
    const note = screen.getByText(NOTE)
    expect(note).toHaveTextContent("C677T")
    expect(note).toHaveTextContent(/DNA changes.*complements/)
    // The complement rule is for nucleotide names only; protein names use
    // amino-acid codes, which are not bases.
    expect(note).toHaveTextContent(/protein changes, such as Val158Met.*cannot be compared/)
  })

  it("tags each nucleotide genotype with the strand it is read on", () => {
    renderPanel([C677T, A1298C])
    expect(row("rs1801133").getByText("GG")).toBeInTheDocument()
    expect(row("rs1801133").getByText("(plus strand)")).toBeInTheDocument()
    expect(row("rs1801131").getByText("GT")).toBeInTheDocument()
    expect(row("rs1801131").getByText("(plus strand)")).toBeInTheDocument()
  })

  it("does not tag an insertion/deletion call, which names no strand", () => {
    renderPanel([C677T, DHFR_INDEL])
    expect(row("rs70991108").getByText("ID")).toBeInTheDocument()
    expect(row("rs70991108").queryByText("(plus strand)")).not.toBeInTheDocument()
  })

  it("does not claim a strand for a genotype whose strand could not be determined", () => {
    renderPanel([C677T, PALINDROME])
    expect(row("rs1801198").getByText("CC")).toBeInTheDocument()
    expect(row("rs1801198").queryByText("(plus strand)")).not.toBeInTheDocument()
  })

  it("tags an unmodelled allele, which is Indeterminate but strand-resolved", () => {
    renderPanel([UNMODELLED])
    expect(row("rs1677693").getByText("GA")).toBeInTheDocument()
    expect(row("rs1677693").getByText("(plus strand)")).toBeInTheDocument()
    expect(screen.getByText(NOTE)).toBeInTheDocument()
  })

  it("does not vouch for a genotype when the strand field is absent", () => {
    // An older response without the field: the call is not vouched for.
    renderPanel([snp({ rsid: "rs1801133", variant_name: "C677T", genotype: "GG" })])
    expect(row("rs1801133").queryByText("(plus strand)")).not.toBeInTheDocument()
  })

  it("omits the note when no genotype on the list is tagged", () => {
    renderPanel([DHFR_INDEL, PALINDROME])
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })
})
