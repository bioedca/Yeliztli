/** Error-gating resilience for MetabolicView (#642).
 *
 * A failed/slow SECONDARY query (the optional established anchor SNPs) must NOT
 * blank the successfully-computed PRIMARY results (the core T2D/BMI polygenic
 * scores) — it degrades to a localized inline error/spinner in its own section.
 * The full-page error is reserved for a failed primary (run / PRS) query.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { MetabolicPRS } from "@/types/metabolic"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockRun = vi.fn()
const mockPRS = vi.fn()
const mockAnchors = vi.fn()
vi.mock("@/api/metabolic", () => ({
  useRunMetabolic: () => mockRun(),
  useMetabolicPRS: () => mockPRS(),
  useMetabolicAnchors: () => mockAnchors(),
}))

import MetabolicView from "@/pages/MetabolicView"

const PRS: MetabolicPRS = {
  trait: "t2d",
  name: "Type 2 Diabetes",
  calibrated: false,
  percentile: null,
  snps_used: 100,
  snps_total: 200,
  coverage_fraction: 0.5,
  is_sufficient: false,
  source_ancestry: "European",
  source_study: "PGS000713",
  source_pmid: "30297969",
  sample_size: 0,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 2,
  research_use_only: true,
  pgs_id: "PGS000713",
  pgs_license: "CC BY 4.0",
  development_method: "C+T",
  genome_build: "GRCh37",
  variants_number: 200,
  source_url: null,
}

/** A minimal TanStack-query/mutation-result stand-in; the view reads only these
 * fields (mutations expose isPending; queries expose isLoading). */
function q(over: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isPending: false,
    isError: false,
    error: null,
    mutate: vi.fn(),
    refetch: vi.fn(),
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRun.mockReturnValue(q())
  mockPRS.mockReturnValue(q({ data: { items: [PRS], total: 1, coverage_context: "" } }))
  mockAnchors.mockReturnValue(q({ data: { items: [], total: 0 } }))
})

describe("MetabolicView error gating (#642)", () => {
  it("renders PRS results even when the secondary anchors query fails", () => {
    mockAnchors.mockReturnValue(q({ isError: true, error: new Error("Metabolic anchors failed: 500") }))
    render(<MetabolicView />)

    // Primary content is still shown.
    expect(screen.getByText("Type 2 Diabetes")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Polygenic Risk Scores" })).toBeInTheDocument()
    // A localized anchors error — not the whole-page error.
    expect(screen.getByText(/Couldn.t load established anchor SNPs/)).toBeInTheDocument()
    expect(screen.queryByText("Failed to load data")).not.toBeInTheDocument()
  })

  it("shows a full-page error only when the PRIMARY PRS query fails", () => {
    mockPRS.mockReturnValue(q({ isError: true, error: new Error("Metabolic PRS failed: 500") }))
    render(<MetabolicView />)

    expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    expect(screen.queryByText("Type 2 Diabetes")).not.toBeInTheDocument()
  })

  it("keeps PRS results visible while the anchors query is still loading", () => {
    mockAnchors.mockReturnValue(q({ isLoading: true }))
    render(<MetabolicView />)

    expect(screen.getByText("Type 2 Diabetes")).toBeInTheDocument()
    // The whole-page loader is gated on the primary work, so it is absent here.
    expect(screen.queryByText("Scoring metabolic polygenic risk...")).not.toBeInTheDocument()
  })
})
