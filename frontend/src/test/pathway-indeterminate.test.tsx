/**
 * Issue #559 — the "Indeterminate → neutral slate, never green Standard" per-SNP
 * safety property (snpCategory.ts, #170/#269/#427) was tested for the allergy
 * PathwayDetailPanel only. The five sibling categorical panels (methylation,
 * gene-health, traits, nutrigenomics, skin) render the same category through the
 * identical `SNP_CATEGORY_COLORS[snp.category] || SNP_CATEGORY_COLORS.Standard`
 * green fallback, but no test rendered them — so a regression making any of them
 * show an Indeterminate SNP as a confidently-clear green Standard would ship
 * unnoticed.
 *
 * This locks the invariant uniformly across ALL SIX categorical panels with a
 * single parametrized suite (allergy included as the control), so the next
 * module to adopt the pattern is covered by adding one line to MODULES.
 *
 * Each module's pathway-detail hook is mocked to return a pathway whose only SNP
 * is `Indeterminate`; we assert the rendered category badge gets the shared
 * neutral-slate colour and NOT the emerald Standard colour. The mock return is
 * cast through `unknown` because only the fields the panel reads at render are
 * supplied (the per-module SNPDetail extras — cross_module, hla_proxy,
 * mc1r_allele_class, … — are all falsy-guarded in the panels).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import type { ComponentType } from "react"
import { render, screen, fireEvent } from "./test-utils"

// Factory mocks (hoisted) — each panel imports ONLY its detail hook from
// `@/api/<module>`, so returning just the hook is sufficient and safe.
vi.mock("@/api/allergy", () => ({ useAllergyPathwayDetail: vi.fn() }))
vi.mock("@/api/methylation", () => ({ useMethylationPathwayDetail: vi.fn() }))
vi.mock("@/api/gene-health", () => ({ useGeneHealthPathwayDetail: vi.fn() }))
vi.mock("@/api/traits", () => ({ useTraitsPathwayDetail: vi.fn() }))
vi.mock("@/api/nutrigenomics", () => ({ useNutrigenomicsPathwayDetail: vi.fn() }))
vi.mock("@/api/skin", () => ({ useSkinPathwayDetail: vi.fn() }))

import AllergyPanel from "@/components/allergy/PathwayDetailPanel"
import MethylationPanel from "@/components/methylation/PathwayDetailPanel"
import GeneHealthPanel from "@/components/gene-health/PathwayDetailPanel"
import TraitsPanel from "@/components/traits/PathwayDetailPanel"
import NutrigenomicsPanel from "@/components/nutrigenomics/PathwayDetailPanel"
import SkinPanel from "@/components/skin/PathwayDetailPanel"

import { useAllergyPathwayDetail } from "@/api/allergy"
import { useMethylationPathwayDetail } from "@/api/methylation"
import { useGeneHealthPathwayDetail } from "@/api/gene-health"
import { useTraitsPathwayDetail } from "@/api/traits"
import { useNutrigenomicsPathwayDetail } from "@/api/nutrigenomics"
import { useSkinPathwayDetail } from "@/api/skin"

// A strand-ambiguous palindromic homozygote the backend withholds as the
// runtime-only `Indeterminate` category (the common fields every panel reads).
const INDETERMINATE_SNP = {
  rsid: "rs1801198",
  gene: "TCN2",
  variant_name: "Pro259Arg",
  genotype: "CC",
  category: "Indeterminate",
  effect_summary:
    "CC is a palindromic (C/G) homozygote: its strand — and therefore its effect " +
    "category — cannot be determined from the array genotype alone, so it is reported " +
    "as indeterminate rather than a possibly strand-flipped call.",
  evidence_level: 1,
  recommendation: null,
  pmids: [],
}

const DETAIL = {
  pathway_id: "test_pathway",
  pathway_name: "Test Pathway",
  level: "Standard",
  evidence_level: 1,
  called_snps: 1,
  total_snps: 1,
  missing_snps: [],
  pmids: [],
  snp_details: [INDETERMINATE_SNP],
}

interface PanelProps {
  pathwayId: string
  pathwayName: string
  sampleId: number
  onClose: () => void
}

// At runtime each imported hook IS the vi.fn() from its factory mock above; we
// type it as a generic mock (not the real hook's union signature) so
// `mockReturnValue` accepts our partial fixture. (Typing it as the union of the
// six real hooks makes mockReturnValue demand the *intersection* of their
// return types — an unsatisfiable type that fails `tsc -b`.)
type MockedDetailHook = ReturnType<typeof vi.fn>
const asMock = (h: unknown) => h as unknown as MockedDetailHook

const MODULES: {
  name: string
  Panel: ComponentType<PanelProps>
  hook: MockedDetailHook
}[] = [
  { name: "allergy", Panel: AllergyPanel, hook: asMock(useAllergyPathwayDetail) },
  { name: "methylation", Panel: MethylationPanel, hook: asMock(useMethylationPathwayDetail) },
  { name: "gene-health", Panel: GeneHealthPanel, hook: asMock(useGeneHealthPathwayDetail) },
  { name: "traits", Panel: TraitsPanel, hook: asMock(useTraitsPathwayDetail) },
  { name: "nutrigenomics", Panel: NutrigenomicsPanel, hook: asMock(useNutrigenomicsPathwayDetail) },
  { name: "skin", Panel: SkinPanel, hook: asMock(useSkinPathwayDetail) },
]

describe.each(MODULES)(
  "$name PathwayDetailPanel: Indeterminate → slate, never green Standard (#559)",
  ({ Panel, hook }) => {
    beforeEach(() => {
      hook.mockReset()
    })

    it("renders a strand-withheld Indeterminate SNP as neutral slate, not emerald Standard", () => {
      hook.mockReturnValue({
        data: DETAIL,
        isLoading: false,
        isError: false,
        error: null,
      })

      render(
        <Panel
          pathwayId="test_pathway"
          pathwayName="Test Pathway"
          sampleId={1}
          onClose={() => {}}
        />,
      )

      // Some panels (methylation) gate the per-SNP rows behind a collapsed
      // "Advanced View" toggle; expand it if present so the SNP badge renders.
      const advancedToggle = screen.queryByRole("button", { name: /Advanced View/i })
      if (advancedToggle) {
        fireEvent.click(advancedToggle)
      }

      const badge = screen.getByText("Indeterminate")
      expect(badge).toBeInTheDocument()
      // Shared neutral slate from SNP_CATEGORY_COLORS.Indeterminate …
      expect(badge).toHaveClass("text-slate-600")
      // … NOT the green Standard fallback (the `|| Standard` failure mode).
      expect(badge).not.toHaveClass("text-emerald-700")
    })
  },
)
