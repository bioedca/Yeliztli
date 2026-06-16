/** Error-gating resilience for CardiovascularView (#642).
 *
 * A failed/slow SECONDARY query (the FH-status card) must NOT blank the
 * successfully-loaded PRIMARY monogenic-variant results — it degrades to a
 * localized inline error/spinner in its own section. The full-page error is
 * reserved for a failed primary (variants) query.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { CardiovascularVariant } from "@/types/cardiovascular"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockVariants = vi.fn()
const mockFHStatus = vi.fn()
const mockDisclaimer = vi.fn()
vi.mock("@/api/cardiovascular", () => ({
  useCardiovascularVariants: () => mockVariants(),
  useFHStatus: () => mockFHStatus(),
  useCardiovascularDisclaimer: () => mockDisclaimer(),
}))

import CardiovascularView from "@/pages/CardiovascularView"

const VARIANT: CardiovascularVariant = {
  rsid: "rs121908025",
  gene_symbol: "LDLR",
  genotype: "A/G",
  zygosity: "Heterozygous",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000000001",
  clinvar_review_stars: 2,
  clinvar_conditions: "Familial hypercholesterolemia",
  conditions: ["Familial hypercholesterolemia"],
  cardiovascular_category: "FH",
  inheritance: "autosomal_dominant",
  evidence_level: 4,
  cross_links: [],
  pmids: [],
}

/** A minimal TanStack-query-result stand-in; the view reads only these fields. */
function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockVariants.mockReturnValue(q({ data: { items: [VARIANT], total: 1 } }))
  mockFHStatus.mockReturnValue(
    q({
      data: {
        status: "Negative",
        summary_text: "No FH variants detected.",
        affected_genes: [],
        variant_count: 0,
        has_homozygous: false,
        highest_evidence_level: 0,
        variants: [],
      },
    }),
  )
  mockDisclaimer.mockReturnValue(q({ data: { title: "Disclaimer", text: "Research use only." } }))
})

describe("CardiovascularView error gating (#642)", () => {
  it("renders monogenic variants even when the secondary FH-status query fails", () => {
    mockFHStatus.mockReturnValue(q({ isError: true, error: new Error("FH status failed: 500") }))
    render(<CardiovascularView />)

    // Primary content is still shown.
    expect(screen.getByRole("heading", { name: "LDLR" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Monogenic Findings" })).toBeInTheDocument()
    // A localized FH-status error — not the whole-page error.
    expect(screen.getByText(/Couldn.t load familial hypercholesterolemia status/)).toBeInTheDocument()
    expect(screen.queryByText("Failed to load data")).not.toBeInTheDocument()
  })

  it("shows a full-page error only when the PRIMARY variants query fails", () => {
    mockVariants.mockReturnValue(q({ isError: true, error: new Error("variants 500") }))
    render(<CardiovascularView />)

    expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    expect(screen.queryByRole("heading", { name: "LDLR" })).not.toBeInTheDocument()
  })

  it("keeps monogenic variants visible while the FH-status query is still loading", () => {
    mockFHStatus.mockReturnValue(q({ isLoading: true }))
    render(<CardiovascularView />)

    expect(screen.getByRole("heading", { name: "LDLR" })).toBeInTheDocument()
    // The whole-page loader is gated on the primary query, so it is absent here.
    expect(screen.queryByText("Loading cardiovascular data...")).not.toBeInTheDocument()
  })
})
