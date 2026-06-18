/** DrugDetailPanel gene-effect rendering, incl. the uncalled-gene state (#905).
 *
 * A guideline gene with no sample finding (Insufficient/uncallable on the array, or
 * not-yet-annotated) comes back flagged `not_assessed` with all sample fields null.
 * It must render an explicit "Not assessed" state, NOT a bare "CPIC Level {x}" card
 * that reads as evaluated-and-normal — the backend excludes such genes from
 * prescribing alerts, and the UI must not silently undo that safety guard.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { GeneEffect } from "@/types/pharmacogenomics"

const mockLookup = vi.fn()
vi.mock("@/api/pharmacogenomics", () => ({
  usePharmaDrugLookup: () => mockLookup(),
}))

const mockPgx = vi.fn()
vi.mock("@/api/pgxGuidelines", () => ({
  usePgxGuidelines: () => mockPgx(),
}))

import DrugDetailPanel from "@/components/pharmacogenomics/DrugDetailPanel"
import type { PgxAlertSources } from "@/types/pgxGuidelines"

function effect(over: Partial<GeneEffect> = {}): GeneEffect {
  return {
    gene: "TPMT",
    diplotype: null,
    metabolizer_status: null,
    recommendation: null,
    classification: null,
    guideline_url: null,
    call_confidence: null,
    confidence_note: null,
    evidence_level: null,
    activity_score: null,
    ehr_notation: null,
    involved_rsids: [],
    gene_caveat: null,
    not_assessed: false,
    ...over,
  }
}

/** Minimal TanStack-query-result stand-in; the panel reads only these fields. */
function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, error: null, ...over }
}

const EVALUATED = effect({
  gene: "TPMT",
  diplotype: "*1/*1",
  metabolizer_status: "Normal Metabolizer",
  recommendation: "Use label-recommended dosing",
  classification: "A",
  call_confidence: "Complete",
})

const UNCALLED = effect({
  gene: "NUDT15",
  classification: "A",
  guideline_url: "https://cpicpgx.org/guidelines/thiopurines/",
  not_assessed: true,
})

beforeEach(() => {
  vi.clearAllMocks()
  // Default: no cross-source evidence (existing tests don't exercise it).
  mockPgx.mockReturnValue(q())
})

function sources(over: Partial<PgxAlertSources> = {}): PgxAlertSources {
  return {
    finding_id: 1,
    gene_symbol: "CYP2C19",
    drug: "clopidogrel",
    metabolizer_status: "Poor Metabolizer",
    has_sources: true,
    pharmgkb_loe: "1A",
    dpwg_guideline: true,
    fda_pgx_level: "Actionable PGx",
    ...over,
  }
}

function pgxResult(alerts: PgxAlertSources[]) {
  return q({ data: { alerts, context_only: true, note: "", pmid_citations: [] } })
}

describe("DrugDetailPanel — uncalled guideline gene (#905)", () => {
  it("shows an explicit 'Not assessed' state for a not_assessed gene", () => {
    mockLookup.mockReturnValue(
      q({ data: { drug: "mercaptopurine", gene_effects: [UNCALLED] } }),
    )
    render(<DrugDetailPanel drugName="mercaptopurine" sampleId={1} onClose={vi.fn()} />)

    // The uncalled gene must carry an unmistakable not-assessed signal…
    expect(screen.getByText("Not assessed")).toBeInTheDocument()
    expect(
      screen.getByText(/could not be called from this sample's array/i),
    ).toBeInTheDocument()
    // …rather than presenting as an evaluated result.
    expect(screen.queryByText("Normal Metabolizer")).not.toBeInTheDocument()
  })

  it("does not flag an evaluated gene as not assessed", () => {
    mockLookup.mockReturnValue(
      q({ data: { drug: "clopidogrel", gene_effects: [EVALUATED] } }),
    )
    render(<DrugDetailPanel drugName="clopidogrel" sampleId={1} onClose={vi.fn()} />)

    expect(screen.getByText("Normal Metabolizer")).toBeInTheDocument()
    expect(screen.queryByText("Not assessed")).not.toBeInTheDocument()
  })

  it("distinguishes an uncalled gene from an evaluated one in the same drug card", () => {
    // The real NUDT15/thiopurines case: TPMT evaluated, NUDT15 uncallable on v3.
    mockLookup.mockReturnValue(
      q({ data: { drug: "mercaptopurine", gene_effects: [EVALUATED, UNCALLED] } }),
    )
    render(<DrugDetailPanel drugName="mercaptopurine" sampleId={1} onClose={vi.fn()} />)

    // Evaluated TPMT shows its metabolizer result; uncalled NUDT15 shows the banner.
    expect(screen.getByText("Normal Metabolizer")).toBeInTheDocument()
    expect(screen.getByText(/NUDT15 could not be called/i)).toBeInTheDocument()
    // Exactly one gene is flagged not-assessed (NUDT15, not TPMT).
    expect(screen.getAllByText("Not assessed")).toHaveLength(1)
  })
})

describe("DrugDetailPanel — cross-source PGx evidence strip (SW-E2)", () => {
  const CYP2C19 = effect({ gene: "CYP2C19", classification: "A", call_confidence: "Complete" })

  it("renders the evidence strip (LoE/DPWG/FDA + attribution) for a matching pair", () => {
    mockLookup.mockReturnValue(q({ data: { drug: "clopidogrel", gene_effects: [CYP2C19] } }))
    mockPgx.mockReturnValue(pgxResult([sources()]))
    render(<DrugDetailPanel drugName="clopidogrel" sampleId={1} onClose={vi.fn()} />)

    expect(screen.getByText("Cross-source evidence")).toBeInTheDocument()
    expect(screen.getByText(/PharmGKB LoE 1A/)).toBeInTheDocument()
    expect(screen.getByText(/DPWG ✓/)).toBeInTheDocument()
    expect(screen.getByText(/FDA: Actionable PGx/)).toBeInTheDocument()
    // CC-BY-SA-4.0 attribution + share-alike note travels with the data.
    expect(screen.getByText(/CC BY-SA 4\.0/)).toBeInTheDocument()
  })

  it("shows no strip when the (gene, drug) pair lacks curated sources", () => {
    mockLookup.mockReturnValue(q({ data: { drug: "clopidogrel", gene_effects: [CYP2C19] } }))
    mockPgx.mockReturnValue(pgxResult([sources({ has_sources: false })]))
    render(<DrugDetailPanel drugName="clopidogrel" sampleId={1} onClose={vi.fn()} />)

    expect(screen.getByText("CYP2C19")).toBeInTheDocument()
    expect(screen.queryByText("Cross-source evidence")).not.toBeInTheDocument()
  })

  it("does not leak another drug's evidence onto this drug", () => {
    mockLookup.mockReturnValue(q({ data: { drug: "warfarin", gene_effects: [CYP2C19] } }))
    mockPgx.mockReturnValue(pgxResult([sources({ drug: "clopidogrel" })]))
    render(<DrugDetailPanel drugName="warfarin" sampleId={1} onClose={vi.fn()} />)

    expect(screen.queryByText("Cross-source evidence")).not.toBeInTheDocument()
  })

  it("degrades gracefully (still shows the drug) when the pgx query has no data", () => {
    mockLookup.mockReturnValue(q({ data: { drug: "clopidogrel", gene_effects: [CYP2C19] } }))
    mockPgx.mockReturnValue(q({ data: undefined }))
    render(<DrugDetailPanel drugName="clopidogrel" sampleId={1} onClose={vi.fn()} />)

    expect(screen.getByText("CYP2C19")).toBeInTheDocument()
    expect(screen.queryByText("Cross-source evidence")).not.toBeInTheDocument()
  })
})
