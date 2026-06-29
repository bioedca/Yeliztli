/** Tests for the standalone eBMD page. */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { EbmdResponse } from "@/types/ebmd"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockUseEbmd = vi.fn()
const mockUseRunEbmd = vi.fn()
const mockRunMutate = vi.fn()

vi.mock("@/api/ebmd", () => ({
  useEbmd: () => mockUseEbmd(),
  useRunEbmd: () => mockUseRunEbmd(),
}))

import EBMDView from "@/pages/EBMDView"

const EBMD_RESPONSE: EbmdResponse = {
  available: true,
  recommended_pgs_id: "PGS000657",
  prs: {
    name: "Heel eBMD",
    calibrated: false,
    higher_is: "protective",
    percentile: null,
    snps_used: 90,
    snps_used_imputed: 5,
    snps_total: 100,
    coverage_fraction: 0.9,
    coverage_tier: "imputed",
    is_sufficient: true,
    source_study: "Morris 2019",
    source_pmid: "30598549",
    pgs_id: "PGS000657",
    pgs_license: "Non-commercial",
    development_method: "C+T",
    ancestry_mismatch: false,
    ancestry_warning_text: null,
    evidence_level: 2,
  },
  context: {
    not_a_substitute: "Not a DXA or FRAX substitute.",
    direction: "Higher score tracks higher heel eBMD.",
    utility: "Research-grade stratification only.",
    byo: "Bring your own licensed score bundle.",
  },
  research_use_only: true,
}

function q(over: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isPending: false,
    isError: false,
    error: null,
    mutate: vi.fn(),
    ...over,
  }
}

beforeEach(() => {
  routerMock.search = "sample_id=1"
  vi.clearAllMocks()
  mockUseRunEbmd.mockReturnValue(q({ mutate: mockRunMutate }))
  mockUseEbmd.mockReturnValue(q({ data: EBMD_RESPONSE }))
})

describe("EBMDView", () => {
  it("shows the imputed coverage split on the eBMD PRS card", () => {
    render(<EBMDView />)

    expect(screen.getByTestId("ebmd-prs")).toBeInTheDocument()
    expect(screen.getByText("Heel eBMD")).toBeInTheDocument()
    expect(screen.getByTestId("prs-imputed-coverage")).toHaveTextContent(
      "Coverage split: 85 typed + 5 imputed SNPs",
    )
  })
})
