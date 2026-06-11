/** Tests for the Cancer predisposition UI (P3-18, T3-21). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import VariantCard from "@/components/cancer/VariantCard"
import PRSGaugeCard from "@/components/cancer/PRSGaugeCard"
import VariantDetailPanel from "@/components/cancer/VariantDetailPanel"
import type { CancerVariant, CancerPRS } from "@/types/cancer"

// ── Fixtures ──────────────────────────────────────────────────────────

const BRCA1_VARIANT: CancerVariant = {
  rsid: "rs80357906",
  gene_symbol: "BRCA1",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000017661",
  clinvar_review_stars: 3,
  clinvar_conditions: "Hereditary breast and ovarian cancer syndrome",
  syndromes: ["Hereditary Breast and Ovarian Cancer"],
  cancer_types: ["Breast", "Ovarian"],
  inheritance: "AD",
  evidence_level: 4,
  cross_links: ["carrier"],
  pmids: ["20301425", "22006311"],
}

const TP53_VARIANT: CancerVariant = {
  rsid: "rs28934578",
  gene_symbol: "TP53",
  genotype: "G/A",
  zygosity: "het",
  clinvar_significance: "Likely pathogenic",
  clinvar_accession: "VCV000012347",
  clinvar_review_stars: 2,
  clinvar_conditions: "Li-Fraumeni syndrome",
  syndromes: ["Li-Fraumeni Syndrome"],
  cancer_types: ["Breast", "Sarcoma", "Brain"],
  inheritance: "AD",
  evidence_level: 3,
  cross_links: [],
  pmids: ["19454582"],
}

const BREAST_PRS: CancerPRS = {
  trait: "breast_cancer",
  name: "Breast Cancer",
  calibrated: true,
  percentile: 72.3,
  z_score: 0.59,
  bootstrap_ci_lower: 65.1,
  bootstrap_ci_upper: 79.5,
  bootstrap_iterations: 1000,
  snps_used: 280,
  snps_total: 313,
  coverage_fraction: 0.89,
  is_sufficient: true,
  source_ancestry: "EUR",
  source_study: "BCAC",
  source_pmid: "29059683",
  sample_size: 228951,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 2,
  research_use_only: true,
}

const MISMATCH_PRS: CancerPRS = {
  trait: "prostate_cancer",
  name: "Prostate Cancer",
  calibrated: true,
  percentile: 55.0,
  z_score: 0.13,
  bootstrap_ci_lower: 48.2,
  bootstrap_ci_upper: 61.8,
  bootstrap_iterations: 1000,
  snps_used: 150,
  snps_total: 200,
  coverage_fraction: 0.75,
  is_sufficient: true,
  source_ancestry: "EUR",
  source_study: "PRACTICAL",
  source_pmid: "29892016",
  sample_size: 140306,
  ancestry_mismatch: true,
  ancestry_warning_text:
    "PRS weights derived from EUR population. Your inferred ancestry (AFR) differs. Interpret with caution.",
  evidence_level: 1,
  research_use_only: true,
}

const INSUFFICIENT_PRS: CancerPRS = {
  trait: "melanoma",
  name: "Melanoma",
  calibrated: true,
  percentile: null,
  z_score: null,
  bootstrap_ci_lower: null,
  bootstrap_ci_upper: null,
  bootstrap_iterations: 0,
  snps_used: 10,
  snps_total: 50,
  coverage_fraction: 0.2,
  is_sufficient: false,
  source_ancestry: "EUR",
  source_study: "Meta-analysis",
  source_pmid: "00000000",
  sample_size: 50000,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 1,
  research_use_only: true,
}

const UNCALIBRATED_PRS: CancerPRS = {
  trait: "colorectal_cancer",
  name: "Colorectal Cancer",
  calibrated: false,
  percentile: null,
  z_score: null,
  bootstrap_ci_lower: null,
  bootstrap_ci_upper: null,
  bootstrap_iterations: 0,
  snps_used: 18,
  snps_total: 20,
  coverage_fraction: 0.9,
  is_sufficient: true,
  source_ancestry: "EUR",
  source_study: "CRC",
  source_pmid: "30510241",
  sample_size: 125478,
  ancestry_mismatch: false,
  ancestry_warning_text: null,
  evidence_level: 1,
  research_use_only: true,
}

// ── VariantCard tests ─────────────────────────────────────────────────

describe("VariantCard", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders gene symbol and rsid", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    expect(screen.getByText("rs80357906")).toBeInTheDocument()
  })

  it("renders ClinVar significance badge", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByText("Pathogenic")).toBeInTheDocument()
  })

  it("renders Likely pathogenic badge", () => {
    render(<VariantCard variant={TP53_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByText("Likely pathogenic")).toBeInTheDocument()
  })

  it("renders genotype and zygosity", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByText("C/T")).toBeInTheDocument()
    expect(screen.getByText("(het)")).toBeInTheDocument()
  })

  it("renders cancer type tags", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByText("Breast")).toBeInTheDocument()
    expect(screen.getByText("Ovarian")).toBeInTheDocument()
  })

  it("renders syndromes", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(
      screen.getByText("Hereditary Breast and Ovarian Cancer"),
    ).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByLabelText("4 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders inheritance pattern", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByText("Autosomal Dominant")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    await user.click(screen.getByTestId("cancer-variant-card"))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    screen.getByTestId("cancer-variant-card").focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it("shows BRCA1/2 cross-link banner for carrier genes", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.getByTestId("brca-cross-link")).toBeInTheDocument()
    expect(
      screen.getByText(/implications for both cancer risk and reproductive carrier status/),
    ).toBeInTheDocument()
    expect(screen.getByText("View Carrier Status")).toBeInTheDocument()
  })

  it("does not show cross-link banner for non-carrier genes", () => {
    render(<VariantCard variant={TP53_VARIANT} onClick={onClick} sampleId={1} />)
    expect(screen.queryByTestId("brca-cross-link")).not.toBeInTheDocument()
  })

  it("cross-link navigates to carrier status page", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={42} />)
    const link = screen.getByText("View Carrier Status")
    expect(link).toHaveAttribute("href", "/carrier-status?sample_id=42")
  })

  it("has accessible role and label", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={onClick} sampleId={1} />)
    expect(
      screen.getByRole("button", {
        name: "BRCA1 rs80357906 — Pathogenic",
      }),
    ).toBeInTheDocument()
  })
})

// ── PRSGaugeCard tests ────────────────────────────────────────────────

describe("PRSGaugeCard", () => {
  it("renders trait name", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.getByText("Breast Cancer")).toBeInTheDocument()
  })

  it("renders Research Use Only badge", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.getByTestId("research-use-badge")).toBeInTheDocument()
    expect(screen.getByText("Research Use Only")).toBeInTheDocument()
  })

  it("renders percentile for sufficient PRS", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.getByText("72")).toBeInTheDocument()
    expect(screen.getByText("th percentile")).toBeInTheDocument()
  })

  it("renders bootstrap CI range", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.getByText("95% CI: 65–80th")).toBeInTheDocument()
  })

  it("renders SNP coverage", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.getByText("280/313 SNPs (89%)")).toBeInTheDocument()
  })

  it("renders source study info", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.getByText(/BCAC.*EUR.*228,951/)).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.getByLabelText("2 of 4 stars evidence")).toBeInTheDocument()
  })

  it("shows ancestry mismatch warning", () => {
    render(<PRSGaugeCard prs={MISMATCH_PRS} />)
    expect(screen.getByTestId("ancestry-mismatch-warning")).toBeInTheDocument()
    expect(screen.getByText(/EUR population.*AFR.*Interpret with caution/)).toBeInTheDocument()
  })

  it("does not show ancestry mismatch warning when no mismatch", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(screen.queryByTestId("ancestry-mismatch-warning")).not.toBeInTheDocument()
  })

  it("shows insufficient coverage message", () => {
    render(<PRSGaugeCard prs={INSUFFICIENT_PRS} />)
    expect(screen.getByText(/Insufficient SNP coverage \(20%\)/)).toBeInTheDocument()
  })

  it("shows uncalibrated message (no percentile) when calibrated is false", () => {
    render(<PRSGaugeCard prs={UNCALIBRATED_PRS} />)
    expect(screen.getByTestId("prs-uncalibrated")).toBeInTheDocument()
    expect(screen.getByText(/no validated reference distribution/)).toBeInTheDocument()
    // The misleading percentile and the insufficient-coverage message must NOT appear.
    expect(screen.queryByText("th percentile")).not.toBeInTheDocument()
    expect(screen.queryByText(/Insufficient SNP coverage/)).not.toBeInTheDocument()
  })

  it("has accessible label", () => {
    render(<PRSGaugeCard prs={BREAST_PRS} />)
    expect(
      screen.getByRole("article", { name: "Breast Cancer polygenic risk score" }),
    ).toBeInTheDocument()
  })
})

// ── VariantDetailPanel tests ──────────────────────────────────────────

describe("VariantDetailPanel", () => {
  const onClose = vi.fn()

  beforeEach(() => {
    onClose.mockClear()
  })

  it("renders gene symbol and rsid", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
    expect(screen.getByText("rs80357906")).toBeInTheDocument()
  })

  it("renders ClinVar significance", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.getByText("Pathogenic")).toBeInTheDocument()
  })

  it("renders ClinVar accession", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.getByText("VCV000017661")).toBeInTheDocument()
  })

  it("renders conditions", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(
      screen.getByText("Hereditary breast and ovarian cancer syndrome"),
    ).toBeInTheDocument()
  })

  it("renders syndromes", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(
      screen.getByText("Hereditary Breast and Ovarian Cancer"),
    ).toBeInTheDocument()
  })

  it("renders cancer types", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.getByText("Breast")).toBeInTheDocument()
    expect(screen.getByText("Ovarian")).toBeInTheDocument()
  })

  it("renders PubMed references", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.getByText("PMID:20301425")).toBeInTheDocument()
    expect(screen.getByText("PMID:22006311")).toBeInTheDocument()
  })

  it("renders BRCA cross-link banner", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.getByTestId("brca-cross-link-panel")).toBeInTheDocument()
    expect(screen.getByText("View Carrier Status")).toBeInTheDocument()
  })

  it("does not render cross-link for non-carrier genes", () => {
    render(
      <VariantDetailPanel variant={TP53_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.queryByTestId("brca-cross-link-panel")).not.toBeInTheDocument()
  })

  it("calls onClose when close button clicked", async () => {
    const user = userEvent.setup()
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    await user.click(screen.getByLabelText("Close panel"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("has accessible panel label", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} onClose={onClose} />,
    )
    expect(screen.getByTestId("variant-detail-panel")).toHaveAttribute(
      "aria-label",
      "BRCA1 variant detail",
    )
  })
})
