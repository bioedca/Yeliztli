/** Guard-withheld pharmacogenomics and findings-summary responses say so (#2522).
 *
 * An empty drug lookup, gene list or medication safety report reads as "no
 * interactions", and an empty findings summary as "no findings / run annotation".
 * When the server withheld the response instead, every consumer must say so,
 * and an ordinary empty response must still read as empty.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams("sample_id=1"), vi.fn()] as const,
  }
})

const pgx = vi.hoisted(() => ({ genes: vi.fn(), drugs: vi.fn(), report: vi.fn(), lookup: vi.fn() }))
vi.mock("@/api/pharmacogenomics", () => ({
  usePharmaGenes: () => pgx.genes(),
  usePharmaDrugs: () => pgx.drugs(),
  usePharmaReport: () => pgx.report(),
  usePharmaDrugLookup: () => pgx.lookup(),
}))
vi.mock("@/api/pgxGuidelines", () => ({ usePgxGuidelines: () => q() }))

const summary = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock("@/api/findings", () => ({
  useFindingsSummary: () => summary.get(),
  useFindings: () => q({ data: [] }),
}))
vi.mock("@/api/reports", () => ({
  useGenerateReport: () => ({ mutate: vi.fn(), isPending: false, isError: false, data: undefined }),
  useExportFhir: () => ({ mutate: vi.fn(), isPending: false, isError: false, data: undefined }),
  useFhirExportEligibility: () => q(),
}))

import PharmacogenomicsView from "@/pages/PharmacogenomicsView"
import DrugDetailPanel from "@/components/pharmacogenomics/DrugDetailPanel"
import MedicationSafetyReport from "@/components/pharmacogenomics/MedicationSafetyReport"
import FindingsPreview from "@/components/dashboard/FindingsPreview"
import ReportBuilder from "@/pages/ReportBuilder"
import FindingsExplorer from "@/pages/FindingsExplorer"

function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

const NO_INTERACTIONS = "Do not read this as having no drug–gene interactions."

function emptySummary(withheld: boolean) {
  return q({
    data: {
      total_findings: 0,
      modules: [],
      evidence_level_counts: [],
      high_confidence_findings: [],
      withheld,
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  pgx.drugs.mockReturnValue(q({ data: { items: [], total: 0 } }))
  pgx.report.mockReturnValue(q({ data: undefined }))
})

describe("withheld pharmacogenomics responses (#2522)", () => {
  it("the gene list says pharmacogenomics results are withheld, not that there are none", () => {
    pgx.genes.mockReturnValue(q({ data: { items: [], total: 0, withheld: true } }))
    render(<PharmacogenomicsView />)

    expect(screen.getByText("Pharmacogenomics results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.getByText(new RegExp(NO_INTERACTIONS))).toBeInTheDocument()
    expect(screen.queryByText("No pharmacogenomics results yet.")).not.toBeInTheDocument()
  })

  it("an ordinary empty gene list still reads as empty", () => {
    pgx.genes.mockReturnValue(q({ data: { items: [], total: 0, withheld: false } }))
    render(<PharmacogenomicsView />)

    expect(screen.getByText("No pharmacogenomics results yet.")).toBeInTheDocument()
    expect(screen.queryByText(/results are withheld/)).not.toBeInTheDocument()
  })

  it("a withheld drug lookup never says there are no gene interactions", () => {
    pgx.lookup.mockReturnValue(q({ data: { drug: "clopidogrel", gene_effects: [], withheld: true } }))
    render(<DrugDetailPanel drugName="clopidogrel" sampleId={1} onClose={vi.fn()} />)

    expect(screen.getByTestId("drug-guidance-withheld")).toHaveTextContent(
      `Guidance for this drug is withheld for this sample. Part of it could not be shown safely, so none is shown here. ${NO_INTERACTIONS}`,
    )
    expect(screen.queryByText("No gene interactions found for this drug.")).not.toBeInTheDocument()
  })

  it("an ordinary empty drug lookup still says there are no gene interactions", () => {
    pgx.lookup.mockReturnValue(q({ data: { drug: "aspirin", gene_effects: [], withheld: false } }))
    render(<DrugDetailPanel drugName="aspirin" sampleId={1} onClose={vi.fn()} />)

    expect(screen.getByText("No gene interactions found for this drug.")).toBeInTheDocument()
    expect(screen.queryByTestId("drug-guidance-withheld")).not.toBeInTheDocument()
  })

  it("a withheld medication safety report says so instead of vanishing", () => {
    pgx.report.mockReturnValue(
      q({
        data: {
          reference_bias_disclosure: "",
          genes_assessed: 0,
          drugs_assessed: 0,
          actionable_drug_count: 0,
          gene_coverage: [],
          drugs: [],
          withheld: true,
        },
      }),
    )
    render(<MedicationSafetyReport sampleId={1} />)

    expect(screen.getByRole("region", { name: "Medication safety report" })).toBeInTheDocument()
    expect(
      screen.getByText("Medication safety report results are withheld for this sample."),
    ).toBeInTheDocument()
  })

  it("an ordinary empty medication safety report still renders nothing", () => {
    pgx.report.mockReturnValue(
      q({
        data: {
          reference_bias_disclosure: "",
          genes_assessed: 0,
          drugs_assessed: 0,
          actionable_drug_count: 0,
          gene_coverage: [],
          drugs: [],
          withheld: false,
        },
      }),
    )
    const { container } = render(<MedicationSafetyReport sampleId={1} />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe("withheld findings summary (#2522)", () => {
  it("the Dashboard preview says findings are withheld, not to run annotation", () => {
    summary.get.mockReturnValue(emptySummary(true))
    render(<FindingsPreview sampleId={1} />)

    expect(screen.getByTestId("findings-summary-withheld")).toHaveTextContent(
      "Findings are withheld for this sample.",
    )
    expect(screen.queryByText("No findings yet")).not.toBeInTheDocument()
    expect(screen.queryByText(/Run annotation/)).not.toBeInTheDocument()
  })

  it("an ordinary empty summary still tells the Dashboard to run annotation", () => {
    summary.get.mockReturnValue(emptySummary(false))
    render(<FindingsPreview sampleId={1} />)

    expect(screen.getByText("No findings yet")).toBeInTheDocument()
    expect(screen.queryByTestId("findings-summary-withheld")).not.toBeInTheDocument()
  })

  it("the Report Builder says analysis results are withheld", () => {
    summary.get.mockReturnValue(emptySummary(true))
    render(<ReportBuilder />)

    expect(screen.getByText("Analysis results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText("No analysis findings available.")).not.toBeInTheDocument()
  })

  it("an ordinary empty summary still reads as no findings in the Report Builder", () => {
    summary.get.mockReturnValue(emptySummary(false))
    render(<ReportBuilder />)

    expect(screen.getByText("No analysis findings available.")).toBeInTheDocument()
  })

  it("the Findings Explorer header says the summary is withheld", () => {
    summary.get.mockReturnValue(emptySummary(true))
    render(<FindingsExplorer />)

    expect(screen.getByText("The findings summary is withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText(/0 findings across 0 modules/)).not.toBeInTheDocument()
    // The empty list beneath must not contradict the header with run-annotation advice.
    expect(screen.getByText(/Findings are withheld for this sample/)).toBeInTheDocument()
    expect(screen.queryByText(/Run annotation/)).not.toBeInTheDocument()
  })

  it("an ordinary empty summary still counts zero in the Findings Explorer header", () => {
    summary.get.mockReturnValue(emptySummary(false))
    render(<FindingsExplorer />)

    expect(screen.getByText("0 findings across 0 modules")).toBeInTheDocument()
    expect(screen.getByText(/No findings yet. Run annotation/)).toBeInTheDocument()
  })
})
