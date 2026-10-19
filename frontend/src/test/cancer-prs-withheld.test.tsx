/** Sex-gated cancer PRS notice on CancerView (#2498).
 *
 * A model the biological-sex gate withholds used to vanish without a reason.
 * The page must name it and say why, and must tell a confident other-sex result
 * ("does not apply") apart from an ambiguous or undetermined sex ("withheld").
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "./test-utils"
import PRSWithheldNotice from "@/components/cancer/PRSWithheldNotice"
import type { CancerPRS, CancerPRSUnavailable, CancerPRSWithheld } from "@/types/cancer"
import { PRS_PROV_DEFAULTS } from "./fixtures/prs"

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

const PROSTATE_NAME = "Prostate cancer (PRACTICAL)"

function withheld(over: Partial<CancerPRSWithheld> = {}): CancerPRSWithheld {
  return {
    trait: "prostate_cancer",
    name: PROSTATE_NAME,
    required_sex: "XY",
    resolved_sex: "XX",
    reason: "sex_mismatch",
    scored_sex: null,
    ...over,
  }
}

const COLORECTAL_PRS: CancerPRS = {
  trait: "colorectal_cancer",
  name: "Colorectal cancer (CRC)",
  calibrated: false,
  percentile: null,
  z_score: null,
  bootstrap_ci_lower: null,
  bootstrap_ci_upper: null,
  bootstrap_iterations: 0,
  snps_used: 18,
  snps_used_imputed: 0,
  snps_total: 20,
  coverage_fraction: 0.9,
  coverage_tier: "typed_only",
  is_sufficient: true,
  source_ancestry: "EUR",
  source_study: "Example",
  source_pmid: "",
  sample_size: 0,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 1,
  research_use_only: true,
  ...PRS_PROV_DEFAULTS,
}

function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

function prsData(
  items: CancerPRS[],
  withheldTraits: CancerPRSWithheld[],
  unavailableModels: CancerPRSUnavailable[] = [],
) {
  return q({
    data: {
      items,
      total: items.length,
      sufficient_count: items.length,
      insufficient_traits: [],
      withheld_traits: withheldTraits,
      unavailable_models: unavailableModels,
    },
  })
}

const BREAST_UNAVAILABLE: CancerPRSUnavailable = {
  trait: "breast_cancer",
  name: "Breast cancer (Mavaddat PRS77; runtime blocked)",
  reason: "runtime_blocked",
}

beforeEach(() => {
  vi.clearAllMocks()
  mockVariants.mockReturnValue(q({ data: { items: [], total: 0 } }))
  mockDisclaimer.mockReturnValue(q({ data: undefined }))
  mockAbsoluteRisk.mockReturnValue(q({ data: undefined }))
  mockSetConsent.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false })
})

describe("CancerView sex-gated PRS notice (#2498)", () => {
  it("says a confident other-sex sample does not get the model", () => {
    mockPRS.mockReturnValue(prsData([COLORECTAL_PRS], [withheld()]))
    render(<CancerView />)

    const note = screen.getByRole("note", { name: "Polygenic risk scores not shown for this sample" })
    expect(note).toHaveTextContent(
      `${PROSTATE_NAME} is not shown: it is computed only for XY samples, and this sample's biological sex is XX.`,
    )
    expect(note).not.toHaveTextContent("withheld")
    // The notice sits in the PRS tier beside the scores that were computed.
    expect(within(screen.getByTestId("prs-tier")).getByTestId("prs-withheld-notice")).toBe(note)
    expect(screen.getByText("Colorectal cancer (CRC)")).toBeInTheDocument()
  })

  it.each([
    ["manual_review", "ambiguous"],
    ["unknown", "undetermined"],
    [null, "undetermined"],
  ])("says the score is withheld when the sex is %s", (resolvedSex, state) => {
    mockPRS.mockReturnValue(
      prsData([COLORECTAL_PRS], [withheld({ resolved_sex: resolvedSex, reason: "sex_unresolved" })]),
    )
    render(<CancerView />)

    expect(screen.getByTestId("prs-withheld-notice")).toHaveTextContent(
      `${PROSTATE_NAME} is not shown: it is computed only for XY samples, and this sample's ` +
        `biological sex is ${state}, so the score is withheld. Recording the individual's ` +
        `biological sex and re-running the analysis resolves this.`,
    )
  })

  it.each([
    ["XX", "XX"],
    ["manual_review", "ambiguous"],
    ["unknown", "undetermined"],
  ])("asks for a re-run when the analysis ran while the sex was %s (#2509)", (scoredSex, state) => {
    mockPRS.mockReturnValue(
      prsData(
        [COLORECTAL_PRS],
        [
          withheld({
            resolved_sex: "XY",
            reason: "sex_changed_since_scoring",
            scored_sex: scoredSex,
          }),
        ],
      ),
    )
    render(<CancerView />)

    const note = screen.getByTestId("prs-withheld-notice")
    expect(note).toHaveTextContent(
      `${PROSTATE_NAME} is not shown: it has not been computed yet. The analysis ran while this ` +
        `sample's biological sex was ${state}, and it is now XY. Re-running the analysis computes it.`,
    )
    expect(note).not.toHaveTextContent("withheld")
  })

  it("shows the notice in the empty state when no score was computed", () => {
    mockPRS.mockReturnValue(prsData([], [withheld({ resolved_sex: "unknown", reason: "sex_unresolved" })]))
    render(<CancerView />)

    expect(screen.queryByTestId("prs-tier")).not.toBeInTheDocument()
    expect(screen.getByText("No PRS results yet.")).toBeInTheDocument()
    expect(screen.getByTestId("prs-withheld-notice")).toHaveTextContent(PROSTATE_NAME)
  })

  it("shows no notice when nothing was withheld", () => {
    mockPRS.mockReturnValue(prsData([COLORECTAL_PRS], []))
    render(<CancerView />)

    expect(screen.getByText("Colorectal cancer (CRC)")).toBeInTheDocument()
    expect(screen.queryByTestId("prs-withheld-notice")).not.toBeInTheDocument()
  })
})

describe("CancerView unavailable PRS models (#2504)", () => {
  it("names a runtime-blocked model beside the computed scores", () => {
    mockPRS.mockReturnValue(prsData([COLORECTAL_PRS], [], [BREAST_UNAVAILABLE]))
    render(<CancerView />)

    const note = within(screen.getByTestId("prs-tier")).getByTestId("prs-withheld-notice")
    expect(note).toHaveTextContent(
      "Breast cancer (Mavaddat PRS77; runtime blocked) is not shown: this model is not currently " +
        "scored or reported. Its source is verified, but it cannot yet be scored safely.",
    )
    expect(screen.getByText("Colorectal cancer (CRC)")).toBeInTheDocument()
  })

  it("names a curator-disabled model without claiming a runtime block", () => {
    mockPRS.mockReturnValue(
      prsData([COLORECTAL_PRS], [], [{ trait: "melanoma", name: "Melanoma (GenoMEL)", reason: "scoring_disabled" }]),
    )
    render(<CancerView />)

    const note = screen.getByTestId("prs-withheld-notice")
    expect(note).toHaveTextContent(
      "Melanoma (GenoMEL) is not shown: this model is not currently scored or reported.",
    )
    expect(note).not.toHaveTextContent("verified")
  })

  it("names it in the empty state too, alongside a sex-gate notice", () => {
    mockPRS.mockReturnValue(
      prsData([], [withheld({ resolved_sex: "unknown", reason: "sex_unresolved" })], [BREAST_UNAVAILABLE]),
    )
    render(<CancerView />)

    expect(screen.getByText("No PRS results yet.")).toBeInTheDocument()
    const items = within(screen.getByTestId("prs-withheld-notice")).getAllByRole("listitem")
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining(PROSTATE_NAME),
      expect.stringContaining("Breast cancer (Mavaddat PRS77; runtime blocked)"),
    ])
  })
})

describe("CancerView insufficient-coverage models (#2503)", () => {
  it("names a low-coverage model by its name, never its trait id", () => {
    const sparse: CancerPRS = {
      ...COLORECTAL_PRS,
      trait: "prostate_cancer",
      name: PROSTATE_NAME,
      is_sufficient: false,
      coverage_fraction: 0.05,
      snps_used: 1,
    }
    mockPRS.mockReturnValue(
      q({
        data: {
          items: [sparse],
          total: 1,
          sufficient_count: 0,
          insufficient_traits: ["prostate_cancer"],
          withheld_traits: [],
          unavailable_models: [],
        },
      }),
    )
    render(<CancerView />)

    const line = screen.getByTestId("prs-insufficient-line")
    expect(line).toHaveTextContent(`Insufficient SNP coverage for: ${PROSTATE_NAME}`)
    expect(line).not.toHaveTextContent("prostate_cancer")
    // The model gets its own card instead of the "Run annotation" empty state.
    expect(screen.getByText("Insufficient SNP coverage (5%)")).toBeInTheDocument()
    expect(screen.queryByText("No PRS results yet.")).not.toBeInTheDocument()
  })
})

describe("PRSWithheldNotice", () => {
  it("renders nothing for a response without the field", () => {
    const { container } = render(<PRSWithheldNotice />)
    expect(container).toBeEmptyDOMElement()
  })

  it("lists each withheld model", () => {
    render(
      <PRSWithheldNotice
        withheld={[
          withheld(),
          withheld({
            trait: "breast_cancer",
            name: "Breast cancer",
            required_sex: "XX",
            resolved_sex: "XY",
          }),
        ]}
      />,
    )
    const items = within(screen.getByRole("note")).getAllByRole("listitem")
    expect(items).toHaveLength(2)
    expect(items[1]).toHaveTextContent(
      "Breast cancer is not shown: it is computed only for XX samples, and this sample's biological sex is XY.",
    )
  })
})
