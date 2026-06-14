/** Tests for the standalone FHView page (/fh, Familial Hypercholesterolemia) — #551.
 *
 * FHView is route-only: on mount with a sample_id it POSTs /run then renders the
 * GET /assessment payload. These tests mock the two FH hooks and the sample_id
 * search param to exercise the page's empty / loading / error states and the
 * monogenic + APOB-FDB + LDL-C-PRS compositions — coverage the standalone /fh
 * page previously lacked entirely (its only FH-adjacent unit test covered the
 * embedded FHStatusCard, not this page).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { FhAssessment } from "@/types/fh"

// ── Mocks: the route param + the route-only API hooks ─────────────────
// vi.hoisted so the value is available when the hoisted vi.mock factory runs.
const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockUseFhAssessment = vi.fn()
const mockUseRunFh = vi.fn()
const mockRunMutate = vi.fn()

vi.mock("@/api/fh", () => ({
  useFhAssessment: () => mockUseFhAssessment(),
  useRunFh: () => mockUseRunFh(),
}))

import FHView from "@/pages/FHView"

// ── Fixtures ──────────────────────────────────────────────────────────

const CRITERIA = {
  disclaimer: "This is NOT a clinical FH diagnosis.",
  dutch_lipid: "Dutch Lipid Clinic Network criteria combine genetics with LDL-C and history.",
  simon_broome: "Simon Broome criteria require clinical confirmation.",
}

function assessment(over: Partial<FhAssessment> = {}): FhAssessment {
  return {
    has_monogenic: false,
    monogenic: [],
    apob_fdb: null,
    ldl_prs: null,
    criteria_context: CRITERIA,
    research_use_only: true,
    ...over,
  }
}

function setAssessment(data: FhAssessment): void {
  mockUseFhAssessment.mockReturnValue({ data, isLoading: false, isError: false, error: null })
}

function setRun(over: Partial<{ isPending: boolean; isError: boolean; error: unknown }> = {}): void {
  mockUseRunFh.mockReturnValue({
    mutate: mockRunMutate,
    isPending: false,
    isError: false,
    error: null,
    ...over,
  })
}

beforeEach(() => {
  routerMock.search = "sample_id=1"
  mockUseFhAssessment.mockReset()
  mockUseRunFh.mockReset()
  mockRunMutate.mockReset()
  setRun()
  setAssessment(assessment())
})

// ── Empty state (no sample) ───────────────────────────────────────────

describe("FHView — empty state (no sample)", () => {
  beforeEach(() => {
    routerMock.search = ""
  })

  it("renders the page heading and a select-a-sample prompt", () => {
    render(<FHView />)
    expect(
      screen.getByRole("heading", { level: 1, name: "Familial Hypercholesterolemia" }),
    ).toBeInTheDocument()
    expect(screen.getByText("Select a sample to view the FH assessment.")).toBeInTheDocument()
  })

  it("does not trigger the FH run without a sample", () => {
    render(<FHView />)
    expect(mockRunMutate).not.toHaveBeenCalled()
  })
})

// ── With a sample ─────────────────────────────────────────────────────

describe("FHView — with a sample", () => {
  it("triggers the FH run on mount", () => {
    render(<FHView />)
    expect(mockRunMutate).toHaveBeenCalled()
  })

  it("shows a loading state while the run is pending", () => {
    setRun({ isPending: true })
    render(<FHView />)
    expect(screen.getByText("Assessing FH genetics...")).toBeInTheDocument()
  })

  it("shows an error state when the run fails", () => {
    setRun({ isError: true, error: new Error("FH run failed: 500") })
    render(<FHView />)
    expect(screen.getByText(/FH run failed: 500/)).toBeInTheDocument()
  })

  it("renders the Dutch-Lipid / Simon-Broome criteria framing disclaimer", () => {
    render(<FHView />)
    const criteria = screen.getByTestId("fh-criteria")
    expect(criteria).toHaveTextContent("NOT a clinical FH diagnosis")
    expect(criteria).toHaveTextContent("Dutch Lipid Clinic Network")
    expect(criteria).toHaveTextContent("Simon Broome")
  })

  it("renders a monogenic finding card when a variant is present", () => {
    setAssessment(
      assessment({
        has_monogenic: true,
        monogenic: [
          {
            gene: "LDLR",
            rsid: "rs28942082",
            clinvar_significance: "Pathogenic",
            zygosity: "het",
            evidence_level: 4,
          },
        ],
      }),
    )
    render(<FHView />)
    const card = screen.getByTestId("fh-monogenic-card")
    expect(card).toHaveTextContent("LDLR")
    expect(card).toHaveTextContent("rs28942082")
    expect(card).toHaveTextContent("Pathogenic")
  })

  it("shows the negative monogenic empty state when none detected", () => {
    setAssessment(assessment({ has_monogenic: false, monogenic: [] }))
    render(<FHView />)
    expect(
      screen.getByText("No reportable monogenic FH variant detected."),
    ).toBeInTheDocument()
  })

  it("highlights an APOB familial-defective-apoB pathogenic carrier", () => {
    setAssessment(
      assessment({
        apob_fdb: {
          rsid: "rs5742904",
          gene: "APOB",
          protein: "R3527Q",
          genotype: "C/T",
          clinvar_significance: "Pathogenic",
          is_pathogenic: true,
        },
      }),
    )
    render(<FHView />)
    const card = screen.getByTestId("fh-apob-fdb-card")
    expect(card).toHaveTextContent("R3527Q")
    expect(card).toHaveTextContent("rs5742904")
    expect(card).toHaveTextContent("pathogenic carrier")
  })

  it("renders the LDL-C polygenic score section when present", () => {
    setAssessment(
      assessment({
        ldl_prs: {
          name: "LDL-C",
          calibrated: true,
          percentile: 82,
          snps_used: 100,
          snps_total: 120,
          coverage_fraction: 0.83,
          is_sufficient: true,
          source_study: "Klarin 2018",
          source_pmid: "30104760",
          pgs_id: "PGS000688",
          pgs_license: "CC-BY-4.0",
          development_method: "C+T",
          ancestry_mismatch: false,
          ancestry_warning_text: null,
          evidence_level: 1,
        },
      }),
    )
    render(<FHView />)
    expect(screen.getByTestId("fh-ldl-prs")).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { level: 2, name: "LDL-C polygenic score" }),
    ).toBeInTheDocument()
  })
})
