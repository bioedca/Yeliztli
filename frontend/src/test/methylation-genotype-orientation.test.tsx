/** The Methylation Advanced View explains why a genotype can look unrelated
 * to its variant name (#2103).
 *
 * Variant names such as "C677T" follow the strand the gene is read from, while
 * an array reports some probes on the other strand, so "C677T — Genotype: GG"
 * can share no letter and read as a data error. Which strand a given call was
 * read on cannot be proven from local data, so the view explains the
 * convention instead of labelling rows.
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

const C677T = snp({ rsid: "rs1801133", variant_name: "C677T", genotype: "GG" })
// Vendor insertion/deletion tokens are not bases.
const DHFR_INDEL = snp({
  rsid: "rs70991108",
  gene: "DHFR",
  variant_name: "19bp deletion",
  genotype: "ID",
})
const NOT_GENOTYPED = snp({ rsid: "rs1979277", variant_name: "C1420T", genotype: null })

function renderPanel(snpDetails: unknown[]) {
  hook.mockReturnValue({
    data: {
      pathway_id: "folate_mthfr",
      pathway_name: "Folate & MTHFR",
      level: "Standard",
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

const NOTE = /Genotypes are shown as your array reported them/

describe("Methylation Advanced View genotype orientation note (#2103)", () => {
  beforeEach(() => {
    hook.mockReset()
  })

  it("explains that a genotype's letters can be the complements of its name's", () => {
    renderPanel([C677T])
    const note = screen.getByText(NOTE)
    expect(note).toHaveTextContent(/coding-DNA change, such as\s+C677T, follows the strand its\s+gene is read from/)
    // The rule is transcript-specific; a genomic (g.) description is not.
    expect(note).toHaveTextContent(/genomic description, written with g\., follows the\s+genome's reference strand instead/)
    expect(note).toHaveTextContent(/arrays report some variants on the other strand/)
    expect(note).toHaveTextContent(/complementary letters \(A pairs with T,\s+C with G\)/)
  })

  it("limits the comparison to DNA-change names", () => {
    renderPanel([C677T])
    expect(screen.getByText(NOTE)).toHaveTextContent(
      /protein changes, such as Val158Met, use\s+amino-acid codes and cannot be compared letter by letter/,
    )
  })

  it("makes no strand claim for any individual genotype", () => {
    renderPanel([C677T])
    const card = screen.getByText("rs1801133").closest(".rounded-lg") as HTMLElement
    expect(within(card).getByText("GG")).toBeInTheDocument()
    expect(within(card).queryByText(/strand/i)).not.toBeInTheDocument()
  })

  it("omits the note when no genotype on the list is a nucleotide call", () => {
    renderPanel([DHFR_INDEL, NOT_GENOTYPED])
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument()
  })
})
