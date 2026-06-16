/** Tests for the Cardiovascular UI (P3-21). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import VariantCard from "@/components/cardiovascular/VariantCard"
import VariantDetailPanel from "@/components/cardiovascular/VariantDetailPanel"
import FHStatusCard from "@/components/cardiovascular/FHStatusCard"
import type { CardiovascularVariant, FHStatusResponse } from "@/types/cardiovascular"

// ── Fixtures ──────────────────────────────────────────────────────────

const LDLR_VARIANT: CardiovascularVariant = {
  rsid: "rs28942082",
  gene_symbol: "LDLR",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000003657",
  clinvar_review_stars: 3,
  clinvar_conditions: "Familial hypercholesterolemia",
  conditions: ["Familial hypercholesterolemia"],
  cardiovascular_category: "familial_hypercholesterolemia",
  inheritance: "AD",
  evidence_level: 4,
  cross_links: [],
  pmids: ["19657116", "20139205"],
}

const SCN5A_VARIANT: CardiovascularVariant = {
  rsid: "rs199473163",
  gene_symbol: "SCN5A",
  genotype: "G/A",
  zygosity: "het",
  clinvar_significance: "Likely pathogenic",
  clinvar_accession: "VCV000067890",
  clinvar_review_stars: 2,
  clinvar_conditions: "Long QT syndrome",
  conditions: ["Brugada syndrome", "Long QT syndrome"],
  cardiovascular_category: "channelopathy",
  inheritance: "AD",
  evidence_level: 3,
  cross_links: [],
  pmids: ["18483608"],
}

const MYBPC3_VARIANT: CardiovascularVariant = {
  rsid: "rs397516083",
  gene_symbol: "MYBPC3",
  genotype: "A/G",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000045678",
  clinvar_review_stars: 2,
  clinvar_conditions: "Hypertrophic cardiomyopathy",
  conditions: ["Hypertrophic cardiomyopathy"],
  cardiovascular_category: "cardiomyopathy",
  inheritance: "AD",
  evidence_level: 4,
  cross_links: [],
  pmids: [],
}

const FH_POSITIVE: FHStatusResponse = {
  status: "Positive",
  summary_text:
    "Pathogenic variant(s) identified in LDLR associated with familial hypercholesterolemia.",
  affected_genes: ["LDLR"],
  variant_count: 1,
  has_homozygous: false,
  highest_evidence_level: 4,
  variants: [
    {
      rsid: "rs28942082",
      gene_symbol: "LDLR",
      genotype: "C/T",
      zygosity: "het",
      clinvar_significance: "Pathogenic",
      clinvar_review_stars: 3,
      clinvar_accession: "VCV000003657",
      evidence_level: 4,
    },
  ],
}

const FH_NEGATIVE: FHStatusResponse = {
  status: "Negative",
  summary_text:
    "No pathogenic or likely pathogenic variants identified in FH-associated genes (LDLR, PCSK9, APOB).",
  affected_genes: [],
  variant_count: 0,
  has_homozygous: false,
  highest_evidence_level: 0,
  variants: [],
}

const FH_HOMOZYGOUS: FHStatusResponse = {
  status: "Positive",
  summary_text:
    "Homozygous pathogenic variant identified in LDLR — consistent with homozygous familial hypercholesterolemia.",
  affected_genes: ["LDLR"],
  variant_count: 1,
  has_homozygous: true,
  highest_evidence_level: 4,
  variants: [
    {
      rsid: "rs28942082",
      gene_symbol: "LDLR",
      genotype: "T/T",
      zygosity: "hom",
      clinvar_significance: "Pathogenic",
      clinvar_review_stars: 3,
      clinvar_accession: "VCV000003657",
      evidence_level: 4,
    },
  ],
}

// ── VariantCard tests ─────────────────────────────────────────────────

describe("VariantCard", () => {
  const onClick = vi.fn()

  beforeEach(() => {
    onClick.mockClear()
  })

  it("renders gene symbol and rsid", () => {
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    expect(screen.getByText("LDLR")).toBeInTheDocument()
    expect(screen.getByText("rs28942082")).toBeInTheDocument()
  })

  it("renders ClinVar significance badge", () => {
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    expect(screen.getByText("Pathogenic")).toBeInTheDocument()
  })

  it("renders Likely pathogenic badge", () => {
    render(<VariantCard variant={SCN5A_VARIANT} onClick={onClick} />)
    expect(screen.getByText("Likely pathogenic")).toBeInTheDocument()
  })

  it("styles combined Pathogenic/Likely pathogenic as a red pathogenic card (#687)", () => {
    render(
      <VariantCard
        variant={{
          ...LDLR_VARIANT,
          clinvar_significance: "Pathogenic/Likely pathogenic",
        }}
        onClick={onClick}
      />,
    )

    expect(screen.getByTestId("cardiovascular-variant-card")).toHaveClass("bg-red-50")
    expect(screen.getByText("Pathogenic/Likely pathogenic")).toHaveClass("bg-red-100")
  })

  it("renders genotype and zygosity", () => {
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    expect(screen.getByText("C/T")).toBeInTheDocument()
    expect(screen.getByText("(het)")).toBeInTheDocument()
  })

  it("renders zygosity even when genotype is null (#802)", () => {
    // The pre-fix real API shape: zygosity present but genotype null. The card must
    // still surface het/hom rather than dropping it nested inside the genotype gate.
    render(<VariantCard variant={{ ...LDLR_VARIANT, genotype: null }} onClick={onClick} />)
    expect(screen.getByText("(het)")).toBeInTheDocument()
    expect(screen.queryByText("C/T")).not.toBeInTheDocument()
  })

  it("renders cardiovascular category badge", () => {
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    expect(screen.getByTestId("category-badge")).toHaveTextContent(
      "Familial Hypercholesterolemia",
    )
  })

  it("renders Channelopathy category", () => {
    render(<VariantCard variant={SCN5A_VARIANT} onClick={onClick} />)
    expect(screen.getByTestId("category-badge")).toHaveTextContent("Channelopathy")
  })

  it("renders Cardiomyopathy category", () => {
    render(<VariantCard variant={MYBPC3_VARIANT} onClick={onClick} />)
    expect(screen.getByTestId("category-badge")).toHaveTextContent("Cardiomyopathy")
  })

  it("renders conditions", () => {
    render(<VariantCard variant={SCN5A_VARIANT} onClick={onClick} />)
    expect(screen.getByText("Brugada syndrome")).toBeInTheDocument()
    expect(screen.getAllByText("Long QT syndrome").length).toBeGreaterThanOrEqual(1)
  })

  it("renders evidence stars", () => {
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    expect(screen.getByLabelText("4 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders inheritance pattern", () => {
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    expect(screen.getByText("Autosomal Dominant")).toBeInTheDocument()
  })

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup()
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    await user.click(screen.getByTestId("cardiovascular-variant-card"))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it("calls onClick on Enter key", async () => {
    const user = userEvent.setup()
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    screen.getByTestId("cardiovascular-variant-card").focus()
    await user.keyboard("{Enter}")
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it("has accessible role and label", () => {
    render(<VariantCard variant={LDLR_VARIANT} onClick={onClick} />)
    expect(
      screen.getByRole("button", {
        name: "LDLR rs28942082 — Pathogenic",
      }),
    ).toBeInTheDocument()
  })
})

// ── FHStatusCard tests ────────────────────────────────────────────────

describe("FHStatusCard", () => {
  it("renders positive status badge", () => {
    render(<FHStatusCard fhStatus={FH_POSITIVE} />)
    expect(screen.getByTestId("fh-status-badge")).toHaveTextContent("Positive")
  })

  it("renders negative status badge", () => {
    render(<FHStatusCard fhStatus={FH_NEGATIVE} />)
    expect(screen.getByTestId("fh-status-badge")).toHaveTextContent("Negative")
  })

  it("renders summary text", () => {
    render(<FHStatusCard fhStatus={FH_POSITIVE} />)
    expect(
      screen.getByText(/Pathogenic variant.*identified in LDLR/),
    ).toBeInTheDocument()
  })

  it("renders affected genes for positive status", () => {
    render(<FHStatusCard fhStatus={FH_POSITIVE} />)
    expect(screen.getByText("LDLR")).toBeInTheDocument()
  })

  it("renders variant count for positive status", () => {
    render(<FHStatusCard fhStatus={FH_POSITIVE} />)
    expect(screen.getByText("1 variant found")).toBeInTheDocument()
  })

  it("shows homozygous flag when present", () => {
    render(<FHStatusCard fhStatus={FH_HOMOZYGOUS} />)
    expect(screen.getByText("Homozygous variant present")).toBeInTheDocument()
  })

  it("does not show homozygous flag when absent", () => {
    render(<FHStatusCard fhStatus={FH_POSITIVE} />)
    expect(screen.queryByText("Homozygous variant present")).not.toBeInTheDocument()
  })

  it("renders FH variant details for positive status", () => {
    render(<FHStatusCard fhStatus={FH_POSITIVE} />)
    expect(screen.getByText(/LDLR rs28942082/)).toBeInTheDocument()
  })

  it("does not render variant details for negative status", () => {
    render(<FHStatusCard fhStatus={FH_NEGATIVE} />)
    expect(screen.queryByText("FH Variants")).not.toBeInTheDocument()
  })

  it("has accessible test id", () => {
    render(<FHStatusCard fhStatus={FH_POSITIVE} />)
    expect(screen.getByTestId("fh-status-card")).toBeInTheDocument()
  })
})

// ── VariantDetailPanel tests ──────────────────────────────────────────

describe("VariantDetailPanel", () => {
  const onClose = vi.fn()

  beforeEach(() => {
    onClose.mockClear()
  })

  it("renders gene symbol and rsid", () => {
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    expect(screen.getByText("LDLR")).toBeInTheDocument()
    expect(screen.getByText("rs28942082")).toBeInTheDocument()
  })

  it("renders ClinVar significance", () => {
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    expect(screen.getByText("Pathogenic")).toBeInTheDocument()
  })

  it("renders ClinVar accession", () => {
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    expect(screen.getByText("VCV000003657")).toBeInTheDocument()
  })

  it("renders cardiovascular category", () => {
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    expect(screen.getByText("Familial Hypercholesterolemia")).toBeInTheDocument()
  })

  it("renders channelopathy category", () => {
    render(<VariantDetailPanel variant={SCN5A_VARIANT} onClose={onClose} />)
    expect(screen.getByText("Channelopathy")).toBeInTheDocument()
  })

  it("renders conditions", () => {
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    expect(screen.getAllByText("Familial hypercholesterolemia").length).toBeGreaterThanOrEqual(1)
  })

  it("renders associated conditions list", () => {
    render(<VariantDetailPanel variant={SCN5A_VARIANT} onClose={onClose} />)
    expect(screen.getByText("Brugada syndrome")).toBeInTheDocument()
    expect(screen.getAllByText("Long QT syndrome").length).toBeGreaterThanOrEqual(1)
  })

  it("renders PubMed references", () => {
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    expect(screen.getByText("PMID:19657116")).toBeInTheDocument()
    expect(screen.getByText("PMID:20139205")).toBeInTheDocument()
  })

  it("does not render references when none exist", () => {
    render(<VariantDetailPanel variant={MYBPC3_VARIANT} onClose={onClose} />)
    expect(screen.queryByText("References")).not.toBeInTheDocument()
  })

  it("calls onClose when close button clicked", async () => {
    const user = userEvent.setup()
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    await user.click(screen.getByLabelText("Close panel"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("has accessible panel label", () => {
    render(<VariantDetailPanel variant={LDLR_VARIANT} onClose={onClose} />)
    expect(screen.getByTestId("variant-detail-panel")).toHaveAttribute(
      "aria-label",
      "LDLR variant detail",
    )
  })
})
