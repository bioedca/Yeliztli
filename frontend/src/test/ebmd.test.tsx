/** Tests for the standalone eBMD page. */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { StrictMode } from "react"
import { render, screen, within } from "./test-utils"
import userEvent from "@testing-library/user-event"
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
    source_ancestry: "",
    source_study: "Morris 2019",
    source_pmid: "30598549",
    sample_size: 0,
    pgs_id: "PGS000657",
    pgs_license: "Non-commercial",
    development_method: "C+T",
    genome_build: null,
    variants_number: null,
    source_url: null,
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
  scoring_state: "current",
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
  it("preserves stored development provenance and its source link (#2423)", () => {
    mockUseEbmd.mockReturnValue(
      q({
        data: {
          ...EBMD_RESPONSE,
          prs: {
            ...EBMD_RESPONSE.prs!,
            source_study: "Synthetic provenance study",
            source_pmid: "",
            source_ancestry: "EAS",
            sample_size: 12345,
            genome_build: "GRCh37",
            variants_number: 1234,
            source_url: "https://example.org/synthetic-ebmd-score",
          },
        },
      }),
    )
    render(<EBMDView />)

    const card = within(screen.getByTestId("ebmd-prs"))
    expect(card.getByText("Source: Synthetic provenance study (EAS, n=12,345)")).toBeInTheDocument()
    expect(card.getByText("1,234 variants")).toBeInTheDocument()
    expect(card.queryByText("90 variants")).not.toBeInTheDocument()
    expect(card.queryByText("100 variants")).not.toBeInTheDocument()
    expect(card.getByRole("link", { name: "PGS000657" })).toHaveAttribute(
      "href",
      "https://example.org/synthetic-ebmd-score",
    )
    expect(card.getByTestId("research-use-badge")).toHaveTextContent("Research Use Only")
    expect(card.getByText("Non-commercial")).toBeInTheDocument()
    expect(screen.getByTestId("ebmd-context")).toHaveTextContent("Not a DXA or FRAX substitute.")
  })

  it("keeps legacy missing provenance empty and falls back to the catalog link", () => {
    mockUseEbmd.mockReturnValue(
      q({
        data: {
          ...EBMD_RESPONSE,
          prs: {
            ...EBMD_RESPONSE.prs!,
            source_study: "Synthetic legacy study",
            source_ancestry: undefined,
            sample_size: undefined,
            genome_build: undefined,
            variants_number: undefined,
            source_url: undefined,
          },
        },
      }),
    )
    render(<EBMDView />)

    const card = within(screen.getByTestId("ebmd-prs"))
    expect(card.getByText("Source: Synthetic legacy study")).toBeInTheDocument()
    expect(card.queryByText(/n=0|\(,|\d+ variants/)).not.toBeInTheDocument()
    expect(card.getByRole("link", { name: "PGS000657" })).toHaveAttribute(
      "href",
      "https://www.pgscatalog.org/score/PGS000657/",
    )
  })

  it("preserves an explicit unknown ancestry without inventing sample or variant counts", () => {
    mockUseEbmd.mockReturnValue(
      q({
        data: {
          ...EBMD_RESPONSE,
          prs: {
            ...EBMD_RESPONSE.prs!,
            source_study: "Synthetic unknown study",
            source_ancestry: "unknown",
            sample_size: 0,
            genome_build: null,
            variants_number: null,
            source_url: null,
          },
        },
      }),
    )
    render(<EBMDView />)

    const card = within(screen.getByTestId("ebmd-prs"))
    expect(card.getByText("Source: Synthetic unknown study (unknown)")).toBeInTheDocument()
    expect(card.queryByText(/n=0|\(,|\d+ variants/)).not.toBeInTheDocument()
    expect(card.getByRole("link", { name: "PGS000657" })).toHaveAttribute(
      "href",
      "https://www.pgscatalog.org/score/PGS000657/",
    )
  })

  it("does not invent a catalog link when the score has no accession", () => {
    mockUseEbmd.mockReturnValue(
      q({
        data: {
          ...EBMD_RESPONSE,
          prs: { ...EBMD_RESPONSE.prs!, pgs_id: null, source_url: null },
        },
      }),
    )
    render(<EBMDView />)
    expect(within(screen.getByTestId("ebmd-prs")).queryByRole("link")).not.toBeInTheDocument()
  })

  it("shows the imputed coverage split on the eBMD PRS card", () => {
    render(<EBMDView />)

    expect(screen.getByTestId("ebmd-prs")).toBeInTheDocument()
    expect(screen.getByText("Heel eBMD")).toBeInTheDocument()
    expect(screen.getByTestId("prs-imputed-coverage")).toHaveTextContent(
      "Coverage split: 85 typed + 5 imputed SNPs",
    )
  })

  it("renders the stored eBMD score while the run re-runs (#1992)", () => {
    // The stored score is present; the ~190 s background re-score must not gate
    // it behind the full-page spinner.
    mockUseRunEbmd.mockReturnValue(q({ mutate: mockRunMutate, isPending: true }))
    render(<EBMDView />)

    expect(screen.getByText("Heel eBMD")).toBeInTheDocument()
    expect(screen.queryByText("Scoring eBMD polygenic risk...")).not.toBeInTheDocument()
  })

  it("keeps the stored eBMD score when the background run fails (#1992)", () => {
    mockUseRunEbmd.mockReturnValue(
      q({ mutate: mockRunMutate, isError: true, error: new Error("eBMD run failed: 500") }),
    )
    render(<EBMDView />)

    expect(screen.getByText("Heel eBMD")).toBeInTheDocument()
    expect(screen.queryByText("Failed to load eBMD score.")).not.toBeInTheDocument()
    // Reported beside the stored score, not in place of it (#2118): with scoring
    // gated on its state, a silent failure would read as "no score".
    expect(within(screen.getByTestId("scoring-status")).getByRole("alert")).toHaveTextContent(
      "eBMD run failed: 500",
    )
  })
})

describe("EBMDView scoring (#2118)", () => {
  function withState(scoring_state: EbmdResponse["scoring_state"] | undefined) {
    const data = { ...EBMD_RESPONSE, scoring_state } as EbmdResponse
    mockUseEbmd.mockReturnValue(q({ data }))
  }

  it.each(["never", "stale"] as const)("scores once when the stored score is %s", (state) => {
    withState(state)
    const { rerender } = render(<EBMDView />)
    rerender(<EBMDView />)
    expect(mockRunMutate).toHaveBeenCalledTimes(1)
  })

  it("does not re-score a current score", () => {
    withState("current")
    render(<EBMDView />)
    expect(mockRunMutate).not.toHaveBeenCalled()
  })

  it("waits for the stored score before deciding", () => {
    mockUseEbmd.mockReturnValue(q({ isLoading: true }))
    render(<EBMDView />)
    expect(mockRunMutate).not.toHaveBeenCalled()
  })

  it("scores when the response does not report a state", () => {
    withState(undefined)
    render(<EBMDView />)
    expect(mockRunMutate).toHaveBeenCalledTimes(1)
  })

  it("starts one run under StrictMode's double effect", () => {
    withState("never")
    render(
      <StrictMode>
        <EBMDView />
      </StrictMode>,
    )
    expect(mockRunMutate).toHaveBeenCalledTimes(1)
  })

  it("re-scores a current score on request", async () => {
    withState("current")
    render(<EBMDView />)
    await userEvent.click(screen.getByRole("button", { name: "Re-score" }))
    expect(mockRunMutate).toHaveBeenCalledTimes(1)
  })

  it("keeps saying a stale score may be out of date when it is not re-scoring", () => {
    withState("stale")
    render(<EBMDView />)
    expect(within(screen.getByTestId("scoring-status")).getByRole("status")).toHaveTextContent(
      "may be out of date",
    )
  })

  it("shows the stored score with a warning when its re-fetch failed", () => {
    mockUseEbmd.mockReturnValue(q({ data: EBMD_RESPONSE, isError: true, error: new Error("503") }))
    render(<EBMDView />)
    expect(screen.getByText("Heel eBMD")).toBeInTheDocument()
    expect(screen.queryByText("Failed to load eBMD score.")).toBeNull()
    expect(screen.getByText(/Couldn't check these results for changes/)).toBeInTheDocument()
  })

  it("says why it is re-scoring and disables the button meanwhile", () => {
    withState("stale")
    mockUseRunEbmd.mockReturnValue(q({ mutate: mockRunMutate, isPending: true }))
    render(<EBMDView />)
    expect(screen.getByRole("status")).toHaveTextContent("inputs changed since it was last scored")
    expect(screen.getByRole("button", { name: "Re-score" })).toBeDisabled()
    // The previous score stays on screen while it re-scores.
    expect(screen.getByText("Heel eBMD")).toBeInTheDocument()
  })
})
