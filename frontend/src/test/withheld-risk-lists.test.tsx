/** Guard-withheld risk, variant and PRS lists say so instead of reading as empty (#2521).
 *
 * On these pages an empty list reads as reassurance -- "No pathogenic variants
 * found", "No carrier variants identified" -- so a list the server withheld must
 * say it was withheld, and an ordinary empty list must still read as empty.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams("sample_id=1"), vi.fn()] as const,
  }
})

const m = vi.hoisted(() => ({
  cancerVariants: vi.fn(),
  cancerPRS: vi.fn(),
  cardioVariants: vi.fn(),
  carrierVariants: vi.fn(),
  metabolicPRS: vi.fn(),
  metabolicAnchors: vi.fn(),
  rareFindings: vi.fn(),
}))

vi.mock("@/api/cancer", () => ({
  useCancerVariants: () => m.cancerVariants(),
  useCancerPRS: () => m.cancerPRS(),
  useCancerDisclaimer: () => q(),
  useAbsoluteRisk: () => q(),
  useSetAbsoluteRiskConsent: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))
vi.mock("@/api/cardiovascular", async (orig) => ({
  ...(await orig<typeof import("@/api/cardiovascular")>()),
  useCardiovascularVariants: () => m.cardioVariants(),
  useFHStatus: () => q(),
  useCardiovascularDisclaimer: () => q(),
}))
vi.mock("@/api/carrier", async (orig) => ({
  ...(await orig<typeof import("@/api/carrier")>()),
  useCarrierVariants: () => m.carrierVariants(),
  useCarrierDisclaimer: () => q(),
}))
vi.mock("@/api/metabolic", () => ({
  useRunMetabolic: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useMetabolicPRS: () => m.metabolicPRS(),
  useMetabolicAnchors: () => m.metabolicAnchors(),
}))
vi.mock("@/api/rare-variants", async (orig) => ({
  ...(await orig<typeof import("@/api/rare-variants")>()),
  useRareVariantFindings: () => m.rareFindings(),
}))

import CancerView from "@/pages/CancerView"
import CardiovascularView from "@/pages/CardiovascularView"
import CarrierStatusView from "@/pages/CarrierStatusView"
import MetabolicView from "@/pages/MetabolicView"
import RareVariantsView from "@/pages/RareVariantsView"

function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

const NO_PLP = "Do not read this as having no pathogenic or likely pathogenic variants."

function emptyList(withheld: boolean, extra: Record<string, unknown> = {}) {
  return q({ data: { items: [], total: 0, withheld, ...extra } })
}

const EMPTY_CANCER_PRS = {
  sufficient_count: 0,
  insufficient_traits: [],
  withheld_traits: [],
  unavailable_models: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })))
  m.cancerPRS.mockReturnValue(emptyList(false, EMPTY_CANCER_PRS))
  m.metabolicAnchors.mockReturnValue(emptyList(false, { scoring_generation: "g" }))
})

describe("withheld risk and variant lists (#2521)", () => {
  it("Cancer says monogenic results are withheld, not that no variants were found", () => {
    m.cancerVariants.mockReturnValue(emptyList(true))
    render(<CancerView />)

    expect(screen.getByText("Cancer predisposition results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.getByText(new RegExp(NO_PLP))).toBeInTheDocument()
    expect(screen.queryByText(/No pathogenic or likely pathogenic variants found/)).not.toBeInTheDocument()
  })

  it("Cancer still reads an ordinary empty monogenic list as empty", () => {
    m.cancerVariants.mockReturnValue(emptyList(false))
    render(<CancerView />)

    expect(screen.getByText(/No pathogenic or likely pathogenic variants found/)).toBeInTheDocument()
    expect(screen.queryByText(/results are withheld/)).not.toBeInTheDocument()
  })

  it("Cancer says its polygenic risk scores are withheld", () => {
    m.cancerVariants.mockReturnValue(emptyList(false))
    m.cancerPRS.mockReturnValue(emptyList(true, EMPTY_CANCER_PRS))
    render(<CancerView />)

    expect(screen.getByText("Polygenic risk score results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText("No PRS results yet.")).not.toBeInTheDocument()
  })

  it("Cardiovascular says results are withheld, not that no variants were found", () => {
    m.cardioVariants.mockReturnValue(emptyList(true))
    render(<CardiovascularView />)

    expect(screen.getByText("Cardiovascular results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText(/No pathogenic or likely pathogenic variants found/)).not.toBeInTheDocument()
  })

  it("Cardiovascular still reads an ordinary empty list as empty", () => {
    m.cardioVariants.mockReturnValue(emptyList(false))
    render(<CardiovascularView />)

    expect(screen.getByText(/No pathogenic or likely pathogenic variants found/)).toBeInTheDocument()
  })

  it("Carrier status says results are withheld, not that no carrier variants were identified", () => {
    m.carrierVariants.mockReturnValue(emptyList(true, { genes_with_findings: [] }))
    render(<CarrierStatusView />)

    expect(screen.getByText("Carrier status results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.getByText(/Do not read this as having no carrier variants/)).toBeInTheDocument()
    expect(screen.queryByText(/No carrier variants identified/)).not.toBeInTheDocument()
  })

  it("Carrier status still reads an ordinary empty list as empty", () => {
    m.carrierVariants.mockReturnValue(emptyList(false, { genes_with_findings: [] }))
    render(<CarrierStatusView />)

    expect(screen.getByText(/No carrier variants identified/)).toBeInTheDocument()
  })

  it("Metabolic says its polygenic scores and anchors are withheld", () => {
    m.metabolicPRS.mockReturnValue(emptyList(true, { coverage_context: "", scoring_state: "current", scoring_generation: "g" }))
    m.metabolicAnchors.mockReturnValue(emptyList(true, { scoring_generation: "g" }))
    render(<MetabolicView />)

    expect(screen.getByText("Polygenic score results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.getByText("Established variant results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText("No polygenic scores available.")).not.toBeInTheDocument()
  })

  it("Metabolic still reads an ordinary empty score list as empty", () => {
    m.metabolicPRS.mockReturnValue(emptyList(false, { coverage_context: "", scoring_state: "current", scoring_generation: "g" }))
    render(<MetabolicView />)

    expect(screen.getByText("No polygenic scores available.")).toBeInTheDocument()
    expect(screen.queryByText(/results are withheld/)).not.toBeInTheDocument()
  })

  it("Rare Variants says findings are withheld, not that there are none yet", () => {
    m.rareFindings.mockReturnValue(emptyList(true))
    render(<RareVariantsView />)

    expect(screen.getByText("Rare variant results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText("No rare variant findings yet.")).not.toBeInTheDocument()
  })

  it("Rare Variants still reads an ordinary empty findings list as empty", () => {
    m.rareFindings.mockReturnValue(emptyList(false))
    render(<RareVariantsView />)

    expect(screen.getByText("No rare variant findings yet.")).toBeInTheDocument()
  })
})
