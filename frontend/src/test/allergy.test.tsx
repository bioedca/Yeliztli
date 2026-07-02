/** Tests for the Gene Allergy UI (P3-61). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/allergy/PathwayCard"
import PathwayDetailPanel from "@/components/allergy/PathwayDetailPanel"
import AllergyView from "@/pages/AllergyView"
import { useAllergyPathwayDetail, useAllergyPathways } from "@/api/allergy"
import type {
  CeliacCombinedItem,
  PathwaySummary,
  SNPDetail,
  PathwayDetailResponse,
} from "@/types/allergy"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

vi.mock("@/api/allergy", () => ({
  useAllergyPathwayDetail: vi.fn(),
  useAllergyPathways: vi.fn(),
}))
const mockUseDetail = vi.mocked(useAllergyPathwayDetail)
const mockUsePathways = vi.mocked(useAllergyPathways)

// ── Fixtures ──────────────────────────────────────────────────────────

const ATOPIC_PATHWAY: PathwaySummary = {
  pathway_id: "atopic_conditions",
  pathway_name: "Atopic Conditions",
  level: "Elevated",
  evidence_level: 3,
  called_snps: 3,
  total_snps: 3,
  missing_snps: [],
  pmids: ["18007931", "17611496"],
  hla_proxy_lookup: null,
}

const DRUG_PATHWAY: PathwaySummary = {
  pathway_id: "drug_hypersensitivity",
  pathway_name: "Drug Hypersensitivity",
  level: "Moderate",
  evidence_level: 4,
  called_snps: 3,
  total_snps: 4,
  missing_snps: ["rs1061235"],
  pmids: ["18192595"],
  hla_proxy_lookup: null,
}

const FOOD_PATHWAY: PathwaySummary = {
  pathway_id: "food_sensitivity",
  pathway_name: "Food Sensitivity",
  level: "Standard",
  evidence_level: 3,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: ["18311140"],
  hla_proxy_lookup: null,
}

const HISTAMINE_PATHWAY: PathwaySummary = {
  pathway_id: "histamine_metabolism",
  pathway_name: "Histamine Metabolism",
  level: "Standard",
  evidence_level: 1,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: [],
  hla_proxy_lookup: null,
}

const CELIAC_INDETERMINATE: CeliacCombinedItem = {
  state: "indeterminate",
  label: "Celiac Risk Undetermined (Insufficient HLA Coverage)",
  dq2_genotype: null,
  dq8_genotype: null,
  description:
    "The HLA-DQ2 and/or DQ8 proxy markers were not genotyped in this sample, so celiac disease cannot be excluded.",
  evidence_level: 3,
  pmids: [],
}

function pathwaysWith(celiac: CeliacCombinedItem) {
  mockUsePathways.mockReturnValue({
    data: {
      items: [FOOD_PATHWAY],
      total: 1,
      celiac_combined: celiac,
      histamine_combined: null,
      cross_module: [],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useAllergyPathways>)
}

// ── PathwayCard tests ─────────────────────────────────────────────────

describe("PathwayCard", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders pathway name", () => {
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Atopic Conditions")).toBeInTheDocument()
  })

  it("shows Elevated badge for Elevated level", () => {
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Elevated")).toBeInTheDocument()
  })

  it("shows Moderate badge for Moderate level", () => {
    render(<PathwayCard pathway={DRUG_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Moderate")).toBeInTheDocument()
  })

  it("shows Standard badge for Standard level", () => {
    render(<PathwayCard pathway={FOOD_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("Standard")).toBeInTheDocument()
  })

  it("qualifies Standard histamine cards when AOC1 SNPs are off-chip", () => {
    render(
      <PathwayCard
        pathway={{
          ...HISTAMINE_PATHWAY,
          called_snps: 1,
          total_snps: 5,
          missing_snps: ["rs10156191", "rs1049742", "rs1049793", "rs2052129"],
          no_call_snps: [],
        }}
        onClick={onClick}
      />,
    )

    expect(screen.getByText("Tested Standard")).toBeInTheDocument()
    expect(screen.getByTestId("pathway-coverage-caveat")).toHaveTextContent(
      "No variants of concern among tested SNPs; 4 tracked SNPs (4 off-chip) not assessed.",
    )
    expect(screen.queryByText("Standard")).not.toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    expect(screen.getByLabelText("3 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders SNP coverage count", () => {
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    expect(screen.getByText("3/3 SNPs called")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    await user.click(
      screen.getByRole("button", {
        name: "Atopic Conditions — Elevated",
      }),
    )
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    const card = screen.getByRole("button", {
      name: "Atopic Conditions — Elevated",
    })
    card.focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("has accessible role and label", () => {
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByRole("button", {
        name: "Atopic Conditions — Elevated",
      }),
    ).toBeInTheDocument()
  })

  it("renders pathway description for atopic_conditions", () => {
    render(<PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/atopic conditions.*asthma.*eczema.*allergic rhinitis/i),
    ).toBeInTheDocument()
  })

  it("renders pathway description for drug_hypersensitivity", () => {
    render(<PathwayCard pathway={DRUG_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/HLA-mediated drug hypersensitivity/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for food_sensitivity", () => {
    render(<PathwayCard pathway={FOOD_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Celiac disease.*HLA-DQ2\/DQ8/),
    ).toBeInTheDocument()
  })

  it("renders pathway description for histamine_metabolism", () => {
    render(<PathwayCard pathway={HISTAMINE_PATHWAY} onClick={onClick} />)
    expect(
      screen.getByText(/Histamine.*salicylate metabolism/),
    ).toBeInTheDocument()
  })

  it("shows selected state when selected prop is true", () => {
    render(
      <PathwayCard pathway={ATOPIC_PATHWAY} onClick={onClick} selected />,
    )
    const card = screen.getByRole("button", {
      name: "Atopic Conditions — Elevated",
    })
    expect(card).toHaveAttribute("data-selected", "true")
  })

  it("renders all four pathway cards with correct data", () => {
    const pathways = [ATOPIC_PATHWAY, DRUG_PATHWAY, FOOD_PATHWAY, HISTAMINE_PATHWAY]
    for (const pathway of pathways) {
      const { unmount } = render(
        <PathwayCard pathway={pathway} onClick={onClick} />,
      )
      expect(screen.getByText(pathway.pathway_name)).toBeInTheDocument()
      unmount()
    }
  })
})

// ── Celiac combined-card state colors (#847) ──────────────────────────

describe("CeliacCombinedCard indeterminate state (#847)", () => {
  beforeEach(() => {
    routerMock.search = "sample_id=1"
    mockUsePathways.mockReset()
  })

  it("renders insufficient HLA coverage as neutral slate, not blue positive susceptibility", () => {
    pathwaysWith(CELIAC_INDETERMINATE)
    render(<AllergyView />)

    expect(screen.getByText(CELIAC_INDETERMINATE.label)).toBeInTheDocument()
    expect(screen.getByTestId("celiac-combined-card")).toHaveClass("bg-slate-50")
    expect(screen.getByTestId("celiac-combined-label")).toHaveClass("bg-slate-100")
    expect(screen.getByTestId("celiac-combined-description")).toHaveClass("bg-slate-100/50")
    expect(screen.getByTestId("celiac-combined-card")).not.toHaveClass("bg-blue-50")
    expect(screen.getByTestId("celiac-combined-label")).not.toHaveClass("bg-blue-100")
    expect(screen.getByTestId("celiac-combined-description")).not.toHaveClass("bg-blue-100/50")
  })
})

describe("CeliacCombinedCard DQ8 marker identity (#1372)", () => {
  beforeEach(() => {
    routerMock.search = "sample_id=1"
    mockUsePathways.mockReset()
  })

  it("labels DQ8 genotypes with the scored rs7454108 proxy, not rs7775228", () => {
    pathwaysWith({
      ...CELIAC_INDETERMINATE,
      state: "dq8_only",
      label: "HLA-DQ8 Detected",
      dq8_genotype: "CT",
      description: "HLA-DQ8 proxy allele detected.",
    })

    render(<AllergyView />)

    expect(screen.getByText(/DQ8 proxy \(rs7454108\):/)).toBeInTheDocument()
    expect(screen.queryByText(/DQ8 proxy \(rs7775228\):/)).not.toBeInTheDocument()
    expect(screen.getByText("CT")).toBeInTheDocument()
  })
})

// ── HLAProxyBadge (via PathwayDetailPanel) tests ──────────────────────
// Regression for #402: the badge previously read snp.hla_proxy.r_squared
// (singular), which is undefined on the backend's hla_proxy block, so
// undefined.toFixed(2) crashed for every HLA-proxy SNP. None of these paths
// were exercised by any test.

const HLA_SNP_BASE: SNPDetail = {
  rsid: "rs2395029",
  gene: "HLA-B",
  variant_name: "HLA-B*57:01 proxy",
  genotype: "TG",
  category: "Moderate",
  effect_summary: "HLA-B*57:01 tag-SNP positive",
  evidence_level: 4,
  recommendation: null,
  pmids: [],
  hla_proxy: { hla_allele: "HLA-B*57:01", clinical_grade: true, confirmatory_test_required: true },
  hla_proxy_lookup: null,
  coverage_note: null,
}

function detailWith(snp: SNPDetail): PathwayDetailResponse {
  return {
    pathway_id: "drug_hypersensitivity",
    pathway_name: "Drug Hypersensitivity",
    level: "Moderate",
    evidence_level: 4,
    called_snps: 1,
    total_snps: 1,
    missing_snps: [],
    pmids: [],
    snp_details: [snp],
    hla_proxy_lookup: null,
  }
}

describe("HLAProxyBadge", () => {
  beforeEach(() => {
    mockUseDetail.mockReset()
  })

  function renderBadge(snp: SNPDetail): HTMLElement {
    mockUseDetail.mockReturnValue({
      data: detailWith(snp),
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useAllergyPathwayDetail>)
    render(
      <PathwayDetailPanel
        pathwayId="drug_hypersensitivity"
        pathwayName="Drug Hypersensitivity"
        sampleId={1}
        onClose={() => {}}
      />,
    )
    return screen.getByText(/HLA Proxy:/).closest("div") as HTMLElement
  }

  it("renders the min per-population r² from hla_proxy_lookup without crashing", () => {
    const badge = renderBadge({
      ...HLA_SNP_BASE,
      hla_proxy_lookup: {
        hla_allele: "HLA-B*57:01",
        r_squared_by_pop: { EUR: 0.97, AFR: 0.85 },
      },
    })
    expect(badge.textContent).toContain("HLA Proxy: HLA-B*57:01")
    expect(badge.textContent).toContain("min r²=0.85") // conservative: lowest across pops
    expect(badge.textContent).toContain("AFR, EUR")
    expect(badge.textContent).not.toContain("NaN")
  })

  it("falls back to the panel block's legacy r_squared_<pop> when the lookup is null", () => {
    const badge = renderBadge({
      ...HLA_SNP_BASE,
      hla_proxy: { hla_allele: "HLA-B*57:01", r_squared_eur: 0.97 },
      hla_proxy_lookup: null,
    })
    expect(badge.textContent).toContain("HLA Proxy: HLA-B*57:01")
    expect(badge.textContent).toContain("r²=0.97")
    expect(badge.textContent).toContain("EUR")
    expect(badge.textContent).not.toContain("NaN")
  })

  it("renders the allele only (no r², no NaN) when no per-population r² exists", () => {
    const badge = renderBadge({
      ...HLA_SNP_BASE,
      hla_proxy: { hla_allele: "HLA-B*57:01" },
      hla_proxy_lookup: null,
    })
    expect(badge.textContent).toContain("HLA Proxy: HLA-B*57:01")
    expect(badge.textContent).not.toContain("NaN")
    expect(badge.textContent).not.toContain("r²=")
  })

  it("drops non-finite r² (NaN) instead of rendering 'NaN'", () => {
    // typeof NaN === "number", so an unvalidated r_squared_* must be filtered.
    const badge = renderBadge({
      ...HLA_SNP_BASE,
      hla_proxy: { hla_allele: "HLA-B*57:01", r_squared_eur: NaN },
      hla_proxy_lookup: { hla_allele: "HLA-B*57:01", r_squared_by_pop: { EAS: NaN } },
    })
    expect(badge.textContent).toContain("HLA Proxy: HLA-B*57:01")
    expect(badge.textContent).not.toContain("NaN")
    expect(badge.textContent).not.toContain("r²=")
  })
})

// ── Indeterminate SNP category rendering (#465) ───────────────────────
// Since #436 extended the #269 palindromic strand guard to the allergy
// scorer, an A/T or C/G homozygote whose strand cannot be resolved from the
// array (e.g. AOC1 rs1049793 CC/GG) is withheld as the runtime-only
// `Indeterminate` category. It must render neutral slate, never the green
// "Standard" colour, mirroring the six categorical modules standardized in
// #369. (e2e: tests/e2e/categorical-indeterminate.spec.ts.)

const INDETERMINATE_SNP: SNPDetail = {
  rsid: "rs1049793",
  gene: "AOC1",
  variant_name: "His664Asp",
  genotype: "CC",
  category: "Indeterminate",
  effect_summary:
    "CC is a palindromic (A/T or C/G) homozygote: its strand — and therefore its " +
    "effect category — cannot be determined from the array genotype alone, so it is " +
    "reported as indeterminate rather than a possibly strand-flipped call.",
  evidence_level: 1,
  recommendation: null,
  pmids: [],
  hla_proxy: null,
  hla_proxy_lookup: null,
  coverage_note: null,
}

describe("SNPRow Indeterminate category (#465)", () => {
  beforeEach(() => {
    mockUseDetail.mockReset()
  })

  it("renders a strand-withheld homozygote as neutral slate, not green Standard", () => {
    mockUseDetail.mockReturnValue({
      data: {
        ...detailWith(INDETERMINATE_SNP),
        pathway_id: "histamine_metabolism",
        pathway_name: "Histamine Metabolism",
        level: "Standard",
      },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useAllergyPathwayDetail>)
    render(
      <PathwayDetailPanel
        pathwayId="histamine_metabolism"
        pathwayName="Histamine Metabolism"
        sampleId={1}
        onClose={() => {}}
      />,
    )
    const badge = screen.getByText("Indeterminate")
    expect(badge).toBeInTheDocument()
    // Shared neutral slate from SNP_CATEGORY_COLORS.Indeterminate (#427), not the
    // emerald Standard fallback.
    expect(badge).toHaveClass("text-slate-600")
    expect(badge).not.toHaveClass("text-emerald-700")
    // Strand caveat is surfaced so the user understands why the call is withheld.
    expect(screen.getByText(/palindromic/i)).toBeInTheDocument()
  })
})
