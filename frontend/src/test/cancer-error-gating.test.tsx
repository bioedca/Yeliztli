/** Error-gating resilience for CancerView (#642).
 *
 * A failed/slow SECONDARY query (the "Research Use Only" PRS) must NOT blank the
 * successfully-loaded PRIMARY monogenic variant results — it degrades to a
 * localized inline error/spinner in its own section. The full-page error is
 * reserved for a failed primary (variants) query.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { CancerVariant } from "@/types/cancer"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockVariants = vi.fn()
const mockPRS = vi.fn()
const mockDisclaimer = vi.fn()
const mockAbsoluteRisk = vi.fn()
const mockSetConsent = vi.fn()
vi.mock("@/api/cancer", () => ({
  useCancerVariants: () => mockVariants(),
  useCancerPRS: () => mockPRS(),
  useCancerDisclaimer: () => mockDisclaimer(),
  useAbsoluteRisk: () => mockAbsoluteRisk(),
  useSetAbsoluteRiskConsent: () => mockSetConsent(),
}))

import CancerView from "@/pages/CancerView"

const VARIANT: CancerVariant = {
  rsid: "rs80357906",
  gene_symbol: "BRCA1",
  genotype: "AG",
  zygosity: "heterozygous",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000000001",
  clinvar_review_stars: 3,
  clinvar_conditions: "Hereditary breast and ovarian cancer syndrome",
  syndromes: ["HBOC"],
  cancer_types: ["breast"],
  inheritance: "AD",
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
  mockPRS.mockReturnValue(q({ data: { items: [], total: 0, sufficient_count: 0, insufficient_traits: [] } }))
  mockDisclaimer.mockReturnValue(q({ data: { title: "Cancer disclaimer", text: "Research use only." } }))
  mockAbsoluteRisk.mockReturnValue(q({ data: undefined }))
  mockSetConsent.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false })
})

describe("CancerView error gating (#642)", () => {
  it("renders monogenic variants even when the secondary PRS query fails", () => {
    mockPRS.mockReturnValue(q({ isError: true, error: new Error("Cancer PRS failed: 500") }))
    render(<CancerView />)

    // Primary content is still shown.
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Monogenic Findings" })).toBeInTheDocument()
    // A localized PRS error — not the whole-page error.
    expect(screen.getByText(/Couldn.t load polygenic risk scores/)).toBeInTheDocument()
    expect(screen.queryByText("Failed to load data")).not.toBeInTheDocument()
  })

  it("shows a full-page error only when the PRIMARY variants query fails", () => {
    mockVariants.mockReturnValue(q({ isError: true, error: new Error("variants 500") }))
    render(<CancerView />)

    expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    expect(screen.queryByText("BRCA1")).not.toBeInTheDocument()
  })

  it("keeps monogenic variants visible while the PRS query is still loading", () => {
    mockPRS.mockReturnValue(q({ isLoading: true }))
    render(<CancerView />)

    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    // The whole-page loader is gated on the primary query, so it is absent here.
    expect(screen.queryByText("Loading cancer data...")).not.toBeInTheDocument()
  })
})
