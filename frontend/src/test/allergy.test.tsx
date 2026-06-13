/** Tests for the Gene Allergy UI (P3-61). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import PathwayCard from "@/components/allergy/PathwayCard"
import PathwayDetailPanel from "@/components/allergy/PathwayDetailPanel"
import { useAllergyPathwayDetail } from "@/api/allergy"
import type { PathwaySummary, SNPDetail, PathwayDetailResponse } from "@/types/allergy"

vi.mock("@/api/allergy", () => ({ useAllergyPathwayDetail: vi.fn() }))
const mockUseDetail = vi.mocked(useAllergyPathwayDetail)

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
