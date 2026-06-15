/** Error-gating resilience for PharmacogenomicsView (#642).
 *
 * A failed/slow SECONDARY query (the shared, non-sample-specific CPIC drug
 * reference list) must NOT blank the successfully-loaded PRIMARY gene
 * metabolizer results — it degrades to a localized inline error/spinner in its
 * own section. The full-page error is reserved for a failed primary (genes)
 * query.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { GeneSummary } from "@/types/pharmacogenomics"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockGenes = vi.fn()
const mockDrugs = vi.fn()
const mockReport = vi.fn()
vi.mock("@/api/pharmacogenomics", () => ({
  usePharmaGenes: () => mockGenes(),
  usePharmaDrugs: () => mockDrugs(),
  usePharmaReport: () => mockReport(),
}))

import PharmacogenomicsView from "@/pages/PharmacogenomicsView"

const GENE: GeneSummary = {
  gene: "CYP2D6",
  diplotype: "*1/*4",
  phenotype: "Intermediate Metabolizer",
  call_confidence: "Complete",
  confidence_note: null,
  activity_score: 1,
  ehr_notation: null,
  evidence_level: 4,
  involved_rsids: [],
  drugs: [],
  gene_caveat: null,
}

/** A minimal TanStack-query-result stand-in; the view reads only these fields. */
function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGenes.mockReturnValue(q({ data: { items: [GENE], total: 1 } }))
  mockDrugs.mockReturnValue(q({ data: { items: [], total: 0 } }))
  // MedicationSafetyReport stays silent unless it has assessed genes.
  mockReport.mockReturnValue(q({ data: undefined }))
})

describe("PharmacogenomicsView error gating (#642)", () => {
  it("renders gene results even when the secondary drugs query fails", () => {
    mockDrugs.mockReturnValue(q({ isError: true, error: new Error("Pharma drugs failed: 500") }))
    render(<PharmacogenomicsView />)

    // Primary content is still shown.
    expect(screen.getByText("CYP2D6")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Gene Results" })).toBeInTheDocument()
    // A localized drug-reference error — not the whole-page error.
    expect(screen.getByText(/Couldn.t load the drug interaction reference/)).toBeInTheDocument()
    expect(screen.queryByText("Failed to load data")).not.toBeInTheDocument()
  })

  it("shows a full-page error only when the PRIMARY genes query fails", () => {
    mockGenes.mockReturnValue(q({ isError: true, error: new Error("Pharma genes failed: 500") }))
    render(<PharmacogenomicsView />)

    expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    expect(screen.queryByText("CYP2D6")).not.toBeInTheDocument()
  })

  it("keeps gene results visible while the drugs query is still loading", () => {
    mockDrugs.mockReturnValue(q({ isLoading: true }))
    render(<PharmacogenomicsView />)

    expect(screen.getByText("CYP2D6")).toBeInTheDocument()
    // The whole-page loader is gated on the primary query, so it is absent here.
    expect(screen.queryByText("Loading pharmacogenomics data...")).not.toBeInTheDocument()
  })
})
