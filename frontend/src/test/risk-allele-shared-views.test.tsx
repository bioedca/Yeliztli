/** Shared-view rendering guard for ClinVar lower-penetrance/risk-allele findings (#2052). */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "./test-utils"
import type { CancerVariant } from "@/types/cancer"
import type { CardiovascularVariant } from "@/types/cardiovascular"
import type { CarrierVariant } from "@/types/carrier"

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams("sample_id=1"), vi.fn()] as const,
  }
})

const mockCancerVariants = vi.fn()
const mockCancerPRS = vi.fn()
const mockCancerDisclaimer = vi.fn()
const mockAbsoluteRisk = vi.fn()
const mockSetAbsoluteRiskConsent = vi.fn()
vi.mock("@/api/cancer", () => ({
  useCancerVariants: () => mockCancerVariants(),
  useCancerPRS: () => mockCancerPRS(),
  useCancerDisclaimer: () => mockCancerDisclaimer(),
  useAbsoluteRisk: () => mockAbsoluteRisk(),
  useSetAbsoluteRiskConsent: () => mockSetAbsoluteRiskConsent(),
}))

const mockCardiovascularVariants = vi.fn()
const mockFHStatus = vi.fn()
const mockCardiovascularDisclaimer = vi.fn()
vi.mock("@/api/cardiovascular", () => ({
  useCardiovascularVariants: () => mockCardiovascularVariants(),
  useFHStatus: () => mockFHStatus(),
  useCardiovascularDisclaimer: () => mockCardiovascularDisclaimer(),
}))

const mockCarrierVariants = vi.fn()
const mockCarrierDisclaimer = vi.fn()
vi.mock("@/api/carrier", () => ({
  useCarrierVariants: () => mockCarrierVariants(),
  useCarrierDisclaimer: () => mockCarrierDisclaimer(),
}))

import CancerView from "@/pages/CancerView"
import CardiovascularView from "@/pages/CardiovascularView"
import CarrierStatusView from "@/pages/CarrierStatusView"

const CANCER_PLP: CancerVariant = {
  rsid: "rs_cancer_plp",
  gene_symbol: "BRCA1",
  genotype: "A/G",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: null,
  clinvar_review_stars: 2,
  clinvar_conditions: "Synthetic cancer condition",
  syndromes: ["Synthetic syndrome"],
  cancer_types: ["Synthetic cancer"],
  inheritance: "AD",
  clinvar_low_penetrance_or_risk_allele: false,
  evidence_level: 4,
  cross_links: [],
  pmids: [],
}

const CANCER_RISK: CancerVariant = {
  ...CANCER_PLP,
  rsid: "rs_cancer_risk",
  gene_symbol: "CHEK2",
  clinvar_significance: "Pathogenic/Established risk allele",
  clinvar_low_penetrance_or_risk_allele: true,
  evidence_level: 2,
}

const CARDIOVASCULAR_PLP: CardiovascularVariant = {
  rsid: "rs_cardio_plp",
  gene_symbol: "LDLR",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: null,
  clinvar_review_stars: 2,
  clinvar_conditions: "Synthetic cardiovascular condition",
  conditions: ["Synthetic cardiovascular condition"],
  cardiovascular_category: "familial_hypercholesterolemia",
  clinvar_low_penetrance_or_risk_allele: false,
  inheritance: "AD",
  evidence_level: 4,
  cross_links: [],
  pmids: [],
}

const CARDIOVASCULAR_RISK: CardiovascularVariant = {
  ...CARDIOVASCULAR_PLP,
  rsid: "rs_cardio_risk",
  gene_symbol: "PCSK9",
  clinvar_significance: "Pathogenic/Established risk allele",
  clinvar_low_penetrance_or_risk_allele: true,
  evidence_level: 2,
}

const CARRIER_PLP: CarrierVariant = {
  rsid: "rs_carrier_plp",
  gene_symbol: "CFTR",
  genotype: "A/G",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: null,
  clinvar_review_stars: 2,
  clinvar_conditions: "Synthetic carrier condition",
  conditions: ["Synthetic carrier condition"],
  inheritance: "AR",
  clinvar_low_penetrance_or_risk_allele: false,
  evidence_level: 4,
  cross_links: [],
  pmids: [],
  notes: "Synthetic behavior-only fixture.",
}

const CARRIER_RISK: CarrierVariant = {
  ...CARRIER_PLP,
  rsid: "rs_carrier_risk",
  gene_symbol: "HBB",
  clinvar_significance: "Pathogenic/Established risk allele",
  clinvar_low_penetrance_or_risk_allele: true,
  evidence_level: 2,
}

function queryResult(overrides: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockCancerVariants.mockReturnValue(
    queryResult({ data: { items: [CANCER_PLP, CANCER_RISK], total: 2 } }),
  )
  mockCancerPRS.mockReturnValue(
    queryResult({
      data: { items: [], total: 0, sufficient_count: 0, insufficient_traits: [] },
    }),
  )
  mockCancerDisclaimer.mockReturnValue(queryResult())
  mockAbsoluteRisk.mockReturnValue(queryResult())
  mockSetAbsoluteRiskConsent.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
  })

  mockCardiovascularVariants.mockReturnValue(
    queryResult({
      data: { items: [CARDIOVASCULAR_PLP, CARDIOVASCULAR_RISK], total: 2 },
    }),
  )
  mockFHStatus.mockReturnValue(queryResult())
  mockCardiovascularDisclaimer.mockReturnValue(queryResult())

  mockCarrierVariants.mockReturnValue(
    queryResult({
      data: {
        items: [CARRIER_PLP, CARRIER_RISK],
        total: 2,
        genes_with_findings: ["CFTR", "HBB"],
      },
    }),
  )
  mockCarrierDisclaimer.mockReturnValue(queryResult())
})

describe("lower-penetrance findings share current module views (#2052)", () => {
  it("renders ordinary and lower-penetrance cancer findings together", () => {
    render(<CancerView />)

    expect(screen.getAllByTestId("cancer-variant-card")).toHaveLength(2)
    expect(screen.getByText("rs_cancer_plp")).toBeInTheDocument()
    expect(screen.getByText("rs_cancer_risk")).toBeInTheDocument()
  })

  it("renders ordinary and lower-penetrance cardiovascular findings together", () => {
    render(<CardiovascularView />)

    expect(screen.getAllByTestId("cardiovascular-variant-card")).toHaveLength(2)
    expect(screen.getByText("rs_cardio_plp")).toBeInTheDocument()
    expect(screen.getByText("rs_cardio_risk")).toBeInTheDocument()
  })

  it("renders ordinary and lower-penetrance carrier findings together", () => {
    render(<CarrierStatusView />)

    expect(screen.getAllByTestId("carrier-variant-card")).toHaveLength(2)
    expect(screen.getByText("rs_carrier_plp")).toBeInTheDocument()
    expect(screen.getByText("rs_carrier_risk")).toBeInTheDocument()
  })
})
