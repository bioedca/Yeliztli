/** Shared orientation explanation in the production pathway panels (#2441).
 * All rows below are synthetic display fixtures, not biological assertions.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, within } from "./test-utils"

vi.mock("@/api/allergy", () => ({ useAllergyPathwayDetail: vi.fn() }))
vi.mock("@/api/fitness", () => ({ useFitnessPathwayDetail: vi.fn() }))
vi.mock("@/api/gene-health", () => ({ useGeneHealthPathwayDetail: vi.fn() }))
vi.mock("@/api/methylation", () => ({ useMethylationPathwayDetail: vi.fn() }))
vi.mock("@/api/nutrigenomics", () => ({ useNutrigenomicsPathwayDetail: vi.fn() }))
vi.mock("@/api/skin", () => ({ useSkinPathwayDetail: vi.fn() }))
vi.mock("@/api/sleep", () => ({ useSleepPathwayDetail: vi.fn() }))
vi.mock("@/api/traits", () => ({ useTraitsPathwayDetail: vi.fn() }))

import AllergyPanel from "@/components/allergy/PathwayDetailPanel"
import FitnessPanel from "@/components/fitness/PathwayDetailPanel"
import GeneHealthPanel from "@/components/gene-health/PathwayDetailPanel"
import MethylationPanel from "@/components/methylation/PathwayDetailPanel"
import NutrigenomicsPanel from "@/components/nutrigenomics/PathwayDetailPanel"
import SkinPanel from "@/components/skin/PathwayDetailPanel"
import SleepPanel from "@/components/sleep/PathwayDetailPanel"
import TraitsPanel from "@/components/traits/PathwayDetailPanel"
import { useAllergyPathwayDetail } from "@/api/allergy"
import { useFitnessPathwayDetail } from "@/api/fitness"
import { useGeneHealthPathwayDetail } from "@/api/gene-health"
import { useMethylationPathwayDetail } from "@/api/methylation"
import { useNutrigenomicsPathwayDetail } from "@/api/nutrigenomics"
import { useSkinPathwayDetail } from "@/api/skin"
import { useSleepPathwayDetail } from "@/api/sleep"
import { useTraitsPathwayDetail } from "@/api/traits"

const panels = [
  { name: "allergy", Panel: AllergyPanel, hook: useAllergyPathwayDetail },
  { name: "fitness", Panel: FitnessPanel, hook: useFitnessPathwayDetail },
  { name: "gene-health", Panel: GeneHealthPanel, hook: useGeneHealthPathwayDetail },
  { name: "methylation", Panel: MethylationPanel, hook: useMethylationPathwayDetail },
  { name: "nutrigenomics", Panel: NutrigenomicsPanel, hook: useNutrigenomicsPathwayDetail },
  { name: "skin", Panel: SkinPanel, hook: useSkinPathwayDetail },
  { name: "sleep", Panel: SleepPanel, hook: useSleepPathwayDetail },
  { name: "traits", Panel: TraitsPanel, hook: useTraitsPathwayDetail },
] as const

// Exact reviewed wording from #2440; the evidence packet and claim mapping are
// data/science-evidence/2026-10-14-methylation-genotype-orientation-2103/.
const NOTE = "Genotypes are shown as your array reported them. A variant name "
  + "written as a coding-DNA change, such as C677T, follows the strand its "
  + "gene is read from (a genomic description, written with g., follows the "
  + "genome's reference strand instead), and arrays report some variants "
  + "on the other strand. When they do, the genotype shows the "
  + "complementary letters (A pairs with T, C with G). Names written as "
  + "protein changes, such as Val158Met, use amino-acid codes and cannot be "
  + "compared letter by letter with a genotype."
const NOTE_START = /Genotypes are shown as your array reported them/

function renderPanel(panel: typeof panels[number], genotypes: (string | null)[], expand = true) {
  const snps = genotypes.map((genotype, index) => ({
    rsid: `rs244100${index}`,
    gene: "SYNTHETIC",
    variant_name: "Synthetic variant name",
    genotype,
    category: "Standard",
    effect_summary: "Synthetic display fixture.",
    evidence_level: 1,
    recommendation: null,
    pmids: [],
    coverage_note: null,
  }))
  const hook = panel.hook as unknown as ReturnType<typeof vi.fn>
  hook.mockReturnValue({
    data: {
      pathway_id: "synthetic",
      pathway_name: "Synthetic pathway",
      level: "Standard",
      evidence_level: 1,
      called_snps: genotypes.filter((value) => value != null).length,
      total_snps: snps.length,
      missing_snps: [],
      no_call_snps: [],
      pmids: [],
      snp_details: snps,
    },
    isLoading: false,
    isError: false,
    error: null,
  })
  const Panel = panel.Panel
  render(<Panel pathwayId="synthetic" pathwayName="Synthetic pathway" sampleId={1} onClose={() => {}} />)
  expect(hook).toHaveBeenCalledWith("synthetic", 1)
  if (panel.name === "methylation" && expand) {
    fireEvent.click(screen.getByRole("button", { name: /Advanced View/ }))
  }
  return screen.getByRole("region", { name: "Individual SNP results" })
}

describe.each(panels)("$name pathway genotype orientation", (panel) => {
  beforeEach(() => vi.resetAllMocks())

  it("shows the exact explanation once and preserves the raw genotype without a row strand label", () => {
    const results = renderPanel(panel, ["GG"])
    expect(within(results).getAllByText(NOTE_START)).toHaveLength(1)
    expect(within(results).getByText(NOTE_START).textContent).toBe(NOTE)
    const card = within(results).getByText("rs2441000").closest(".rounded-lg") as HTMLElement
    expect(within(card).getByText("GG")).toBeInTheDocument()
    expect(within(card).getByText("SYNTHETIC — Synthetic variant name")).toBeInTheDocument()
    expect(within(card).queryByText(/strand/i)).not.toBeInTheDocument()
  })

  it("shows the explanation once when a later row is a nucleotide call", () => {
    const results = renderPanel(panel, [null, "ID", "A", "ACGT"])
    expect(within(results).getAllByText(NOTE_START)).toHaveLength(1)
    expect(within(results).getByText("ID")).toBeInTheDocument()
    expect(within(results).getByText("A")).toBeInTheDocument()
    expect(within(results).getByText("ACGT")).toBeInTheDocument()
  })

  it.each([null, "", "ID", "--", "ag"])("omits the explanation for a non-nucleotide call %j", (genotype) => {
    const results = renderPanel(panel, [genotype])
    expect(within(results).getByText("rs2441000")).toBeInTheDocument()
    expect(within(results).queryByText(NOTE_START)).not.toBeInTheDocument()
    if (genotype) expect(within(results).getByText(genotype)).toBeInTheDocument()
  })

  it("omits the explanation for an empty variant list", () => {
    const results = renderPanel(panel, [])
    expect(within(results).getByText("No variant data available for this pathway.")).toBeInTheDocument()
    expect(within(results).queryByText(NOTE_START)).not.toBeInTheDocument()
  })
})

it("keeps the methylation explanation inside the Advanced View disclosure", () => {
  const panel = panels.find((entry) => entry.name === "methylation")!
  const results = renderPanel(panel, ["GG"], false)
  const toggle = within(results).getByRole("button", { name: /Advanced View/ })
  expect(toggle).toHaveAttribute("aria-expanded", "false")
  expect(within(results).queryByText(NOTE_START)).not.toBeInTheDocument()
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute("aria-expanded", "true")
  expect(within(results).getByText(NOTE_START)).toBeInTheDocument()
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute("aria-expanded", "false")
  expect(within(results).queryByText(NOTE_START)).not.toBeInTheDocument()
})
