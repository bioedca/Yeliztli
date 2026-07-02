/** Tests for the HLA (imputed) page — drug-hypersensitivity section (SW-D2). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { HlaDrugHypersensitivityResponse } from "@/types/hla"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockUseHlaDrug = vi.fn()

vi.mock("@/api/hla", () => ({
  useHlaDrugHypersensitivity: () => mockUseHlaDrug(),
}))

import HLAView from "@/pages/HLAView"

const CARRIER_RESPONSE: HlaDrugHypersensitivityResponse = {
  available: true,
  any_at_risk: true,
  caveat: "Confirm with clinical high-resolution HLA typing; never use for transplant matching.",
  unavailable_note: null,
  research_use_only: true,
  assessments: [
    {
      allele: "HLA-A*31:01",
      drugs: ["carbamazepine"],
      reaction: "carbamazepine hypersensitivity",
      status: "no_risk_allele",
      carried: false,
      zygosity: null,
      copies: 0,
      prob: 0.9,
      low_confidence: false,
      recommendation: "HLA-A*31:01 not detected.",
      guideline: "CPIC",
      citations: ["PMID:29392710"],
      notes: [],
    },
    {
      allele: "HLA-B*57:01",
      drugs: ["abacavir"],
      reaction: "abacavir hypersensitivity reaction",
      status: "at_risk",
      carried: true,
      zygosity: "heterozygous",
      copies: 1,
      prob: 0.95,
      low_confidence: false,
      recommendation: "CPIC: do not prescribe abacavir.",
      guideline: "CPIC",
      citations: ["PMID:24561393"],
      notes: [],
    },
  ],
}

function q(over: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...over,
  }
}

beforeEach(() => {
  routerMock.search = "sample_id=1"
  vi.clearAllMocks()
})

describe("HLAView drug hypersensitivity", () => {
  it("surfaces an at-risk allele card, the caveat, and sorts at-risk first", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: CARRIER_RESPONSE }))
    render(<HLAView />)

    expect(screen.getByTestId("hla-caveat")).toHaveTextContent("clinical high-resolution HLA typing")
    const atRisk = screen.getByTestId("hla-drug-HLA-B*57:01")
    expect(atRisk).toHaveAttribute("data-status", "at_risk")
    expect(atRisk).toHaveTextContent("abacavir")
    expect(atRisk).toHaveTextContent("do not prescribe abacavir")

    // At-risk sorts before the no-risk-allele card.
    const cards = screen.getAllByTestId(/^hla-drug-HLA-/)
    expect(cards[0]).toHaveAttribute("data-status", "at_risk")
  })

  it("shows an empty state when no imputed HLA calls are available", () => {
    mockUseHlaDrug.mockReturnValue(
      q({
        data: {
          available: false,
          any_at_risk: false,
          assessments: [],
          caveat: "caveat text",
          unavailable_note: "Run HIBAG to populate HLA calls.",
          research_use_only: true,
        },
      }),
    )
    render(<HLAView />)

    expect(screen.getByTestId("hla-drug-hypersensitivity")).toHaveTextContent(
      "No imputed HLA calls for this sample.",
    )
    expect(screen.queryByTestId(/^hla-drug-HLA-/)).not.toBeInTheDocument()
  })
})
