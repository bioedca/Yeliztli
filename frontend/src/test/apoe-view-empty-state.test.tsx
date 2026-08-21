/** Regression coverage for APOE findings empty-state status copy (#2034). */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import { render } from "./test-utils"
import APOEView from "@/pages/APOEView"
import {
  useAPOEDisclaimer,
  useAPOEGateStatus,
  useAcknowledgeAPOEGate,
  useAPOEGenotype,
  useAPOEFindings,
} from "@/api/apoe"
import type { APOEGenotypeResponse } from "@/types/apoe"

vi.mock("@/api/apoe", () => ({
  useAPOEDisclaimer: vi.fn(),
  useAPOEGateStatus: vi.fn(),
  useAcknowledgeAPOEGate: vi.fn(),
  useAPOEGenotype: vi.fn(),
  useAPOEFindings: vi.fn(),
}))

const mockUseAPOEDisclaimer = vi.mocked(useAPOEDisclaimer)
const mockUseAPOEGateStatus = vi.mocked(useAPOEGateStatus)
const mockUseAcknowledgeAPOEGate = vi.mocked(useAcknowledgeAPOEGate)
const mockUseAPOEGenotype = vi.mocked(useAPOEGenotype)
const mockUseAPOEFindings = vi.mocked(useAPOEFindings)

function genotype(status: APOEGenotypeResponse["status"]): APOEGenotypeResponse {
  return {
    status,
    diplotype: status === "determined" ? "e3/e3" : null,
    has_e4: status === "determined" ? false : null,
    e4_count: status === "determined" ? 0 : null,
    has_e2: status === "determined" ? false : null,
    e2_count: status === "determined" ? 0 : null,
    rs429358_genotype: status === "determined" ? "TT" : null,
    rs7412_genotype: status === "determined" ? "CC" : null,
  }
}

function settledQuery<T>(data: T) {
  return {
    data,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }
}

function renderEmptyFindings(status: APOEGenotypeResponse["status"]) {
  mockUseAPOEGenotype.mockReturnValue(
    settledQuery(genotype(status)) as unknown as ReturnType<typeof useAPOEGenotype>,
  )

  render(<APOEView />, { route: "/apoe?sample_id=1" })
  return within(screen.getByRole("region", { name: "APOE findings" }))
}

describe("APOEView findings empty state", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseAPOEDisclaimer.mockReturnValue(
      settledQuery(undefined) as unknown as ReturnType<typeof useAPOEDisclaimer>,
    )
    mockUseAPOEGateStatus.mockReturnValue(
      settledQuery({ acknowledged: true, acknowledged_at: null }) as unknown as ReturnType<
        typeof useAPOEGateStatus
      >,
    )
    mockUseAcknowledgeAPOEGate.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useAcknowledgeAPOEGate>)
    mockUseAPOEFindings.mockReturnValue(
      settledQuery({ items: [], total: 0 }) as unknown as ReturnType<typeof useAPOEFindings>,
    )
  })

  it.each([
    [
      "missing_snps",
      "One or both APOE SNPs (rs429358, rs7412) are missing from this sample.",
    ],
    ["no_call", "APOE SNPs are present but have no-call genotypes."],
    ["ambiguous", "APOE genotype could not be unambiguously determined."],
  ] as const)("explains empty findings for %s", (status, expected) => {
    const findings = renderEmptyFindings(status)

    expect(findings.getByText("No APOE findings available.")).toBeInTheDocument()
    expect(findings.getByText(expected)).toBeInTheDocument()
    expect(findings.queryByText("Run the APOE analysis first.")).not.toBeInTheDocument()
  })

  it("reserves the run instruction for a genuinely unrun analysis", () => {
    const findings = renderEmptyFindings("not_run")

    expect(findings.getByText("Run the APOE analysis first.")).toBeInTheDocument()
  })

  it("does not tell a determined sample to rerun when findings are empty", () => {
    const findings = renderEmptyFindings("determined")

    expect(findings.getByText("No APOE findings available.")).toBeInTheDocument()
    expect(findings.queryByText("Run the APOE analysis first.")).not.toBeInTheDocument()
  })
})
