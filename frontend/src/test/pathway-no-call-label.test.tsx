/**
 * Issue #900 — the gene-health & nutrigenomics PathwayDetailPanel rendered the
 * backend `missing_snps` union with the literal label "not on array". But that
 * union also contains on-chip no-calls (a probe that was on the array but failed
 * to produce a genotype), which is the OPPOSITE remediation (a no-call may be
 * re-testable; an off-chip SNP is an inherent coverage gap). The backend now
 * exposes `no_call_snps`; the panels must render the off-chip remainder as "not
 * on array" and the no-calls distinctly — never mislabel a no-call "not on array".
 *
 * Mock each panel's detail hook with a pathway whose missing set has one off-chip
 * SNP and one on-chip no-call, and assert the split renders correctly.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import type { ComponentType } from "react"
import { render, screen } from "./test-utils"

vi.mock("@/api/gene-health", () => ({ useGeneHealthPathwayDetail: vi.fn() }))
vi.mock("@/api/nutrigenomics", () => ({ useNutrigenomicsPathwayDetail: vi.fn() }))

import GeneHealthPanel from "@/components/gene-health/PathwayDetailPanel"
import NutrigenomicsPanel from "@/components/nutrigenomics/PathwayDetailPanel"
import { useGeneHealthPathwayDetail } from "@/api/gene-health"
import { useNutrigenomicsPathwayDetail } from "@/api/nutrigenomics"

const OFF_CHIP = "rs10000001"
const NO_CALL = "rs80338939"

const DETAIL = {
  pathway_id: "test_pathway",
  pathway_name: "Test Pathway",
  level: "Standard",
  evidence_level: 1,
  called_snps: 8,
  total_snps: 10,
  // Union of all not-present SNPs: one genuinely off-chip + one on-chip no-call.
  missing_snps: [OFF_CHIP, NO_CALL],
  no_call_snps: [NO_CALL],
  pmids: [],
  snp_details: [],
}

interface PanelProps {
  pathwayId: string
  pathwayName: string
  sampleId: number
  onClose: () => void
}

type MockedDetailHook = ReturnType<typeof vi.fn>
const asMock = (h: unknown) => h as unknown as MockedDetailHook

const MODULES: { name: string; Panel: ComponentType<PanelProps>; hook: MockedDetailHook }[] = [
  { name: "gene-health", Panel: GeneHealthPanel, hook: asMock(useGeneHealthPathwayDetail) },
  { name: "nutrigenomics", Panel: NutrigenomicsPanel, hook: asMock(useNutrigenomicsPathwayDetail) },
]

describe.each(MODULES)(
  "$name PathwayDetailPanel: on-chip no-call is not labeled 'not on array' (#900)",
  ({ Panel, hook }) => {
    beforeEach(() => {
      hook.mockReset()
      hook.mockReturnValue({ data: DETAIL, isLoading: false, isError: false, error: null })
    })

    const renderPanel = () =>
      render(
        <Panel pathwayId="test_pathway" pathwayName="Test Pathway" sampleId={1} onClose={() => {}} />,
      )

    it("lists the off-chip SNP under 'Not on array', without the no-call", () => {
      renderPanel()
      const offChipLine = screen.getByText(/^Not on array:/)
      expect(offChipLine.textContent).toContain(OFF_CHIP)
      // The bug: the on-chip no-call must NOT appear in the "not on array" line.
      expect(offChipLine.textContent).not.toContain(NO_CALL)
    })

    it("lists the on-chip no-call under a distinct 'No call' line", () => {
      renderPanel()
      const noCallLine = screen.getByText(/^No call \(on the array/)
      expect(noCallLine.textContent).toContain(NO_CALL)
      expect(noCallLine.textContent).not.toContain(OFF_CHIP)
    })

    it("counts off-chip and no-call separately in the header", () => {
      renderPanel()
      expect(screen.getByText(/1 not on array/)).toBeInTheDocument()
      expect(screen.getByText(/1 no-call/)).toBeInTheDocument()
    })
  },
)
