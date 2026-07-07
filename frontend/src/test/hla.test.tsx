/** Tests for the HLA (imputed) page — drug-hypersensitivity section (SW-D2). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type {
  HlaDrugHypersensitivityResponse,
  HlaRuleOutsResponse,
  HlaSusceptibilityResponse,
  HlaViewerResponse,
} from "@/types/hla"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockUseHlaDrug = vi.fn()
const mockUseHlaRuleOuts = vi.fn()
const mockUseHlaSusceptibility = vi.fn()
const mockUseHlaAlleles = vi.fn()

vi.mock("@/api/hla", () => ({
  useHlaDrugHypersensitivity: () => mockUseHlaDrug(),
  useHlaRuleOuts: () => mockUseHlaRuleOuts(),
  useHlaSusceptibility: () => mockUseHlaSusceptibility(),
  useHlaAlleles: () => mockUseHlaAlleles(),
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

const LOW_CONFIDENCE_RESPONSE: HlaDrugHypersensitivityResponse = {
  available: true,
  any_at_risk: false,
  caveat: "Confirm with clinical high-resolution HLA typing; never use for transplant matching.",
  unavailable_note: null,
  research_use_only: true,
  assessments: [
    {
      allele: "HLA-B*57:01",
      drugs: ["abacavir"],
      reaction: "abacavir hypersensitivity reaction",
      status: "low_confidence",
      carried: true,
      zygosity: "heterozygous",
      copies: 1,
      prob: 0.4,
      low_confidence: true,
      recommendation:
        "HLA-B*57:01 has a low-confidence imputed call. Do not interpret this as positive or negative for abacavir hypersensitivity risk; clinical high-resolution HLA typing is required before using this result.",
      guideline: "CPIC",
      citations: ["PMID:24561393"],
      notes: [],
    },
    {
      allele: "HLA-A*31:01",
      drugs: ["carbamazepine"],
      reaction: "carbamazepine hypersensitivity",
      status: "low_confidence",
      carried: false,
      zygosity: null,
      copies: 0,
      prob: 0.41,
      low_confidence: true,
      recommendation:
        "HLA-A*31:01 has a low-confidence imputed call. Do not interpret this as positive or negative for carbamazepine hypersensitivity risk; clinical high-resolution HLA typing is required before using this result.",
      guideline: "CPIC",
      citations: ["PMID:29392710"],
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

const RULE_OUTS_RESPONSE: HlaRuleOutsResponse = {
  available: true,
  caveat: "caveat",
  unavailable_note: null,
  research_use_only: true,
  citations: ["PMID:31274511", "PMID:30321823"],
  celiac: {
    status: "permissive_present",
    detected: ["DQ2.5 (DQA1*05 + DQB1*02:01)"],
    low_confidence: false,
    interpretation: "A celiac-permissive HLA-DQ haplotype is present. This does NOT diagnose celiac.",
  },
  narcolepsy: {
    status: "absent_lowers",
    carried: false,
    zygosity: null,
    low_confidence: false,
    interpretation: "HLA-DQB1*06:02 was not detected — argues strongly against narcolepsy type 1.",
  },
}

beforeEach(() => {
  routerMock.search = "sample_id=1"
  vi.clearAllMocks()
  // Default queries to "no data" so a section renders only when a test opts in.
  mockUseHlaDrug.mockReturnValue(q())
  mockUseHlaRuleOuts.mockReturnValue(q())
  mockUseHlaSusceptibility.mockReturnValue(q())
  mockUseHlaAlleles.mockReturnValue(q())
})

const SUSCEPTIBILITY_RESPONSE: HlaSusceptibilityResponse = {
  available: true,
  caveat: "caveat",
  unavailable_note: null,
  research_use_only: true,
  findings: [
    {
      condition: "Ankylosing spondylitis / axial spondyloarthritis",
      hla: "HLA-B*27",
      status: "increased_risk",
      carried: true,
      detail: "HLA-B*27:05 (heterozygous)",
      interpretation: "HLA-B*27 is present. It is a susceptibility marker, not a diagnosis.",
      low_confidence: false,
      citations: ["PMID:28259985"],
      notes: ["Also associates with acute anterior uveitis."],
    },
    {
      condition: "Psoriasis (early-onset / guttate)",
      hla: "HLA-C*06:02",
      status: "not_increased",
      carried: false,
      detail: "HLA-C*06:02 not detected",
      interpretation: "HLA-C*06:02 was not detected.",
      low_confidence: false,
      citations: ["PMID:29072309"],
      notes: [],
    },
    {
      condition: "Rheumatoid arthritis (seropositive)",
      hla: "HLA-DRB1 shared epitope",
      status: "limited_screen",
      carried: false,
      detail: "DRB1*04:03 outside the curated shared-epitope screen",
      interpretation:
        "This non-exhaustive screen cannot classify residue-level seropositive-RA susceptibility; do not interpret this as no increased RA susceptibility.",
      low_confidence: false,
      citations: ["PMID:23737967"],
      notes: ["This curated screen is not a residue-aware DRB1 classifier."],
    },
  ],
}

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

  it("renders low-confidence drug calls as neither positive nor negative", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: LOW_CONFIDENCE_RESPONSE }))
    render(<HLAView />)

    const carried = screen.getByTestId("hla-drug-HLA-B*57:01")
    expect(carried).toHaveAttribute("data-status", "low_confidence")
    expect(carried).toHaveTextContent("Low-confidence imputed call")
    expect(carried).toHaveTextContent("clinical HLA typing is required")
    expect(carried).not.toHaveTextContent("Risk allele carried")
    expect(carried).not.toHaveTextContent("Risk allele not detected")
    expect(carried).not.toHaveTextContent("do not prescribe abacavir")

    const absent = screen.getByTestId("hla-drug-HLA-A*31:01")
    expect(absent).toHaveAttribute("data-status", "low_confidence")
    expect(absent).toHaveTextContent("positive or negative")
    expect(absent).not.toHaveTextContent("Risk allele not detected")
  })
})

describe("HLAView disease rule-outs", () => {
  it("renders celiac (permissive) and narcolepsy (absent) rule-out cards", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: CARRIER_RESPONSE }))
    mockUseHlaRuleOuts.mockReturnValue(q({ data: RULE_OUTS_RESPONSE }))
    render(<HLAView />)

    const celiac = screen.getByTestId("hla-rule-out-celiac")
    expect(celiac).toHaveAttribute("data-tone", "non_diagnostic")
    expect(celiac).toHaveTextContent("DQ2.5")
    expect(celiac).toHaveTextContent("does NOT diagnose celiac")

    const narco = screen.getByTestId("hla-rule-out-narcolepsy")
    expect(narco).toHaveAttribute("data-tone", "reassuring")
    expect(narco).toHaveTextContent("argues strongly against narcolepsy")
  })

  it("omits the rule-outs section when unavailable", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: CARRIER_RESPONSE }))
    mockUseHlaRuleOuts.mockReturnValue(
      q({ data: { available: false, celiac: null, narcolepsy: null, caveat: "", unavailable_note: "n", citations: [], research_use_only: true } }),
    )
    render(<HLAView />)

    expect(screen.queryByTestId("hla-rule-outs")).not.toBeInTheDocument()
  })
})

describe("HLAView autoimmune susceptibility", () => {
  it("renders increased-risk and not-increased susceptibility cards", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: CARRIER_RESPONSE }))
    mockUseHlaSusceptibility.mockReturnValue(q({ data: SUSCEPTIBILITY_RESPONSE }))
    render(<HLAView />)

    const b27 = screen.getByTestId("hla-susc-HLA-B*27")
    expect(b27).toHaveAttribute("data-status", "increased_risk")
    expect(b27).toHaveTextContent("Ankylosing spondylitis")
    expect(b27).toHaveTextContent("susceptibility marker, not a diagnosis")

    const c0602 = screen.getByTestId("hla-susc-HLA-C*06:02")
    expect(c0602).toHaveAttribute("data-status", "not_increased")

    const ra = screen.getByTestId("hla-susc-HLA-DRB1 shared epitope")
    expect(ra).toHaveAttribute("data-status", "limited_screen")
    expect(ra).toHaveTextContent("Limited screen")
    expect(ra).toHaveTextContent("do not interpret this as no increased RA susceptibility")
  })

  it("omits the susceptibility section when unavailable", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: CARRIER_RESPONSE }))
    mockUseHlaSusceptibility.mockReturnValue(
      q({ data: { available: false, findings: [], caveat: "", unavailable_note: "n", research_use_only: true } }),
    )
    render(<HLAView />)

    expect(screen.queryByTestId("hla-susceptibility")).not.toBeInTheDocument()
  })
})

const VIEWER_RESPONSE: HlaViewerResponse = {
  available: true,
  caveat: "caveat",
  transplant_guard:
    "These HLA types are statistically imputed and must NEVER be used for transplant or donor matching.",
  unavailable_note: null,
  research_use_only: true,
  alleles: [
    {
      locus: "A",
      allele1: "01:01",
      allele2: "02:01",
      prob: 0.98,
      low_confidence: false,
      source: "hibag",
      ancestry_model: "European",
    },
    {
      locus: "B",
      allele1: "57:01",
      allele2: "07:02",
      prob: 0.4,
      low_confidence: true,
      source: "hibag",
      ancestry_model: "European",
    },
  ],
}

describe("HLAView raw viewer", () => {
  it("renders the allele table with the never-for-transplant guard", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: CARRIER_RESPONSE }))
    mockUseHlaAlleles.mockReturnValue(q({ data: VIEWER_RESPONSE }))
    render(<HLAView />)

    const guard = screen.getByTestId("hla-transplant-guard")
    expect(guard).toHaveTextContent("NEVER be used for transplant")
    expect(screen.getByTestId("hla-allele-A")).toHaveTextContent("A*01:01 / A*02:01")
    // Low-confidence B call is flagged.
    expect(screen.getByTestId("hla-allele-B")).toHaveTextContent("low")
    expect(screen.getByTestId("hla-viewer-download")).toBeInTheDocument()
  })

  it("omits the viewer when no HLA calls exist", () => {
    mockUseHlaDrug.mockReturnValue(q({ data: CARRIER_RESPONSE }))
    mockUseHlaAlleles.mockReturnValue(
      q({
        data: {
          available: false,
          alleles: [],
          caveat: "c",
          transplant_guard: "g",
          unavailable_note: "n",
          research_use_only: true,
        },
      }),
    )
    render(<HLAView />)

    expect(screen.queryByTestId("hla-viewer")).not.toBeInTheDocument()
  })
})
