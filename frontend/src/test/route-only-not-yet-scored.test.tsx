/** Route-only pages say "not computed yet" before a sample's first score (#2307).
 *
 * Metabolic, FH and eBMD score a sample only when its page is opened, so a
 * sample nobody has opened them for has no stored results. Their empty sections
 * used to give a reason that only fits a scored sample -- "The PGS score bundle
 * may not be installed", "is not installed" -- right under the "Scoring this
 * sample" status. Each case below pairs the never-scored state with a scored
 * sample whose result really is empty, so both sides of every branch are pinned.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { EbmdResponse } from "@/types/ebmd"
import type { FhAssessment } from "@/types/fh"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockMetabolicRun = vi.fn()
const mockMetabolicPrs = vi.fn()
const mockMetabolicAnchors = vi.fn()
vi.mock("@/api/metabolic", () => ({
  useRunMetabolic: () => mockMetabolicRun(),
  useMetabolicPRS: () => mockMetabolicPrs(),
  useMetabolicAnchors: () => mockMetabolicAnchors(),
}))

const mockFhRun = vi.fn()
const mockFhAssessment = vi.fn()
vi.mock("@/api/fh", () => ({
  useRunFh: () => mockFhRun(),
  useFhAssessment: () => mockFhAssessment(),
}))

const mockEbmdRun = vi.fn()
const mockEbmd = vi.fn()
vi.mock("@/api/ebmd", () => ({
  useRunEbmd: () => mockEbmdRun(),
  useEbmd: () => mockEbmd(),
}))

import MetabolicView from "@/pages/MetabolicView"
import FHView from "@/pages/FHView"
import EBMDView from "@/pages/EBMDView"

/** A TanStack query/mutation stand-in; the views read only these fields. */
function q(over: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isFetching: false,
    isPending: false,
    isError: false,
    error: null,
    mutate: vi.fn(),
    refetch: vi.fn(),
    ...over,
  }
}

const notComputed = (label: string) =>
  `${label} results have not been computed for this sample yet.`

beforeEach(() => {
  routerMock.search = "sample_id=1"
  vi.clearAllMocks()
})

describe("MetabolicView before the first score (#2307)", () => {
  function setMetabolic(scoring_state: "never" | "current", isPending: boolean) {
    mockMetabolicRun.mockReturnValue(q({ isPending }))
    mockMetabolicPrs.mockReturnValue(
      q({
        data: { items: [], total: 0, coverage_context: "", scoring_state, scoring_generation: null },
      }),
    )
    mockMetabolicAnchors.mockReturnValue(
      q({ data: { items: [], total: 0, scoring_generation: null } }),
    )
  }

  it("says both sections are not computed yet while the first run is in flight", () => {
    setMetabolic("never", true)
    render(<MetabolicView />)

    expect(screen.getByText("Scoring this sample. This can take a few minutes.")).toBeInTheDocument()
    expect(screen.getByRole("region", { name: notComputed("Polygenic score") })).toBeInTheDocument()
    // The anchors section used to vanish here, so the FTO anchor a cross-module
    // card had just promised was simply missing, with no reason given.
    expect(screen.getByRole("region", { name: "Established anchor SNPs" })).toBeInTheDocument()
    expect(screen.getByRole("region", { name: notComputed("Established variant") })).toBeInTheDocument()
    expect(screen.queryByText("The PGS score bundle may not be installed.")).not.toBeInTheDocument()
  })

  it("still says not computed when the first run is not in flight", () => {
    // A failed first run leaves the state "never" with no run pending; the
    // page offers Re-score, and the sections must not claim a missing bundle.
    setMetabolic("never", false)
    render(<MetabolicView />)

    expect(screen.getByRole("region", { name: notComputed("Polygenic score") })).toBeInTheDocument()
    expect(screen.queryByText("The PGS score bundle may not be installed.")).not.toBeInTheDocument()
  })

  it("keeps the not-installed reason, and hides the empty anchors, for a scored sample", () => {
    setMetabolic("current", false)
    render(<MetabolicView />)

    expect(screen.getByText("The PGS score bundle may not be installed.")).toBeInTheDocument()
    expect(screen.queryByText(/have not been computed/)).not.toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Established anchor SNPs" })).not.toBeInTheDocument()
  })
})

describe("FHView before the first score (#2307)", () => {
  function assessment(scoring_state: FhAssessment["scoring_state"]): FhAssessment {
    return {
      assessment_status: "available",
      has_monogenic: false,
      monogenic: [],
      apob_fdb: null,
      ldl_prs: null,
      criteria_context: {
        disclaimer: "This is NOT a clinical FH diagnosis.",
        dutch_lipid: "Dutch Lipid Clinic Network criteria.",
        simon_broome: "Simon Broome criteria.",
      },
      research_use_only: true,
      scoring_state,
    }
  }

  it("says the LDL-C polygenic score is not computed yet", () => {
    mockFhRun.mockReturnValue(q({ isPending: true }))
    mockFhAssessment.mockReturnValue(q({ data: assessment("never") }))
    render(<FHView />)

    expect(
      screen.getByRole("region", { name: notComputed("LDL-C polygenic score") }),
    ).toBeInTheDocument()
    expect(screen.queryByText("LDL-C polygenic score unavailable.")).not.toBeInTheDocument()
    expect(screen.queryByText("The PGS score bundle may not be installed.")).not.toBeInTheDocument()
  })

  it("keeps the unavailable reason for a scored sample with no LDL-C score", () => {
    mockFhRun.mockReturnValue(q())
    mockFhAssessment.mockReturnValue(q({ data: assessment("current") }))
    render(<FHView />)

    expect(screen.getByText("LDL-C polygenic score unavailable.")).toBeInTheDocument()
    expect(screen.queryByText(/have not been computed/)).not.toBeInTheDocument()
  })
})

describe("EBMDView before the first score, and when withheld (#2307)", () => {
  function response(over: Partial<EbmdResponse>): EbmdResponse {
    return {
      available: false,
      recommended_pgs_id: "PGS000657",
      prs: null,
      context: {
        not_a_substitute: "Not a DXA or FRAX substitute.",
        direction: "Higher score tracks higher heel eBMD.",
        utility: "Research-grade stratification only.",
        byo: "Bring your own licensed score bundle.",
      },
      research_use_only: true,
      scoring_state: "current",
      withheld: false,
      ...over,
    }
  }
  const notInstalled = "The recommended eBMD score (PGS000657) is not installed."

  it("says the score is not computed yet, not that it is not installed", () => {
    mockEbmdRun.mockReturnValue(q({ isPending: true }))
    mockEbmd.mockReturnValue(q({ data: response({ scoring_state: "never" }) }))
    render(<EBMDView />)

    expect(
      screen.getByRole("region", { name: notComputed("eBMD polygenic score") }),
    ).toBeInTheDocument()
    expect(screen.queryByText(notInstalled)).not.toBeInTheDocument()
  })

  it("says a refused stored score is withheld, not that it is not installed", () => {
    mockEbmdRun.mockReturnValue(q())
    mockEbmd.mockReturnValue(q({ data: response({ withheld: true }) }))
    render(<EBMDView />)

    expect(
      screen.getByRole("region", { name: "eBMD polygenic score results are withheld for this sample." }),
    ).toBeInTheDocument()
    expect(screen.queryByText(notInstalled)).not.toBeInTheDocument()
    expect(screen.queryByText(/have not been computed/)).not.toBeInTheDocument()
  })

  it("keeps the not-installed reason for a scored sample without the score", () => {
    mockEbmdRun.mockReturnValue(q())
    mockEbmd.mockReturnValue(q({ data: response({}) }))
    render(<EBMDView />)

    expect(screen.getByText(notInstalled)).toBeInTheDocument()
    expect(screen.queryByText(/have not been computed/)).not.toBeInTheDocument()
    expect(screen.queryByText(/results are withheld/)).not.toBeInTheDocument()
  })
})
