/** Error-gating resilience for TraitsPersonalityView (#642).
 *
 * A failed/slow SECONDARY query (the "Research Use Only" PRS) must NOT blank the
 * successfully-loaded PRIMARY pathway results — it degrades to a localized inline
 * error/spinner in its own section. The full-page error is reserved for a failed
 * primary (pathways) query.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { PathwaySummary } from "@/types/traits"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockPathways = vi.fn()
const mockPRS = vi.fn()
const mockDisclaimer = vi.fn()
const mockPathwayDetail = vi.fn()
vi.mock("@/api/traits", () => ({
  useTraitsPathways: () => mockPathways(),
  useTraitsPRS: () => mockPRS(),
  useTraitsDisclaimer: () => mockDisclaimer(),
  useTraitsPathwayDetail: () => mockPathwayDetail(),
}))

import TraitsPersonalityView from "@/pages/TraitsPersonalityView"

const PATHWAY: PathwaySummary = {
  pathway_id: "caffeine_metabolism",
  pathway_name: "Caffeine Metabolism",
  level: "Moderate",
  evidence_level: 2,
  prs_primary: false,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: [],
}

/** A minimal TanStack-query-result stand-in; the view reads only these fields. */
function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPathways.mockReturnValue(q({ data: { items: [PATHWAY], cross_module: [] } }))
  mockPRS.mockReturnValue(q({ data: { items: [] } }))
  mockDisclaimer.mockReturnValue(q({ data: { disclaimer: "Research use only.", evidence_cap: 2 } }))
  mockPathwayDetail.mockReturnValue(q({ data: undefined }))
})

describe("TraitsPersonalityView error gating (#642)", () => {
  it("renders pathway results even when the secondary PRS query fails", () => {
    mockPRS.mockReturnValue(q({ isError: true, error: new Error("Traits PRS failed: 500") }))
    render(<TraitsPersonalityView />)

    // Primary content is still shown.
    expect(screen.getByText("Caffeine Metabolism")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Pathway Results" })).toBeInTheDocument()
    // A localized PRS error — not the whole-page error.
    expect(screen.getByText(/Couldn.t load polygenic risk scores/)).toBeInTheDocument()
    expect(screen.queryByText("Failed to load data")).not.toBeInTheDocument()
  })

  it("shows a full-page error only when the PRIMARY pathways query fails", () => {
    mockPathways.mockReturnValue(q({ isError: true, error: new Error("pathways 500") }))
    render(<TraitsPersonalityView />)

    expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    expect(screen.queryByText("Caffeine Metabolism")).not.toBeInTheDocument()
  })

  it("keeps pathways visible while the PRS query is still loading", () => {
    mockPRS.mockReturnValue(q({ isLoading: true }))
    render(<TraitsPersonalityView />)

    expect(screen.getByText("Caffeine Metabolism")).toBeInTheDocument()
    // The whole-page loader is gated on the primary query, so it is absent here.
    expect(screen.queryByText("Loading traits data...")).not.toBeInTheDocument()
  })
})
