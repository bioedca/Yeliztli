/** Tests for the Carrier Status UI. */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "./test-utils"
import VariantDetailPanel from "@/components/carrier/VariantDetailPanel"
import type { CarrierVariant } from "@/types/carrier"

const CFTR_VARIANT: CarrierVariant = {
  rsid: "rs113993960",
  gene_symbol: "CFTR",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000007105",
  clinvar_review_stars: 3,
  clinvar_conditions: "Cystic fibrosis",
  conditions: ["Cystic Fibrosis"],
  inheritance: "AR",
  evidence_level: 4,
  cross_links: [],
  pmids: ["20301428"],
  notes: "Most common autosomal recessive condition in populations of European descent.",
}

const BRCA1_VARIANT: CarrierVariant = {
  rsid: "rs80357906",
  gene_symbol: "BRCA1",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000017661",
  clinvar_review_stars: 3,
  clinvar_conditions: "Hereditary breast and ovarian cancer syndrome",
  conditions: ["Hereditary Breast and Ovarian Cancer Syndrome"],
  inheritance: "AD",
  evidence_level: 4,
  cross_links: ["cancer"],
  pmids: ["20301425", "29446198", "28632866", "33406487"],
  notes: "Dual-role gene: cancer predisposition and reproductive carrier context.",
}

const AD_NON_CANCER_VARIANT: CarrierVariant = {
  rsid: "rs_ad_example",
  gene_symbol: "ADGENE",
  genotype: "A/G",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000000001",
  clinvar_review_stars: 2,
  clinvar_conditions: "Example autosomal dominant condition",
  conditions: ["Example Autosomal Dominant Condition"],
  inheritance: "AD",
  evidence_level: 3,
  cross_links: [],
  pmids: [],
  notes: "Synthetic AD non-cancer carrier-panel example.",
}

const HBB_VARIANT: CarrierVariant = {
  rsid: "rs334",
  gene_symbol: "HBB",
  genotype: "A/T",
  zygosity: "het",
  clinvar_significance: "Likely pathogenic",
  clinvar_accession: "VCV000015333",
  clinvar_review_stars: 2,
  clinvar_conditions: "Sickle cell disease",
  conditions: ["Sickle Cell Disease"],
  inheritance: "AR",
  evidence_level: 4,
  cross_links: [],
  pmids: ["20301551", "20301357", "30383109", "25393378"],
  notes: "HBB carrier-panel example.",
}

const HBB_VARIANT_CASED_RSID: CarrierVariant = {
  ...HBB_VARIANT,
  rsid: " RS334 ",
}

describe("Carrier VariantDetailPanel", () => {
  it("keeps classic AR carrier wording for CFTR", () => {
    render(
      <VariantDetailPanel
        variant={CFTR_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText(/heterozygous carrier - typically unaffected/i)).toBeInTheDocument()
    expect(screen.getByText(/family planning/i)).toBeInTheDocument()
  })

  it("uses dual-role cancer-risk wording for BRCA variants", () => {
    render(
      <VariantDetailPanel
        variant={BRCA1_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
    expect(screen.getByText(/personal hereditary cancer risk/i)).toBeInTheDocument()
    expect(screen.getByText(/review the cancer module/i)).toBeInTheDocument()
    expect(screen.getByTestId("brca-cross-link-panel")).toBeInTheDocument()
  })

  it("does not show cancer-module wording for AD variants without cancer cross-links", () => {
    render(
      <VariantDetailPanel
        variant={AD_NON_CANCER_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText(/review this result with a genetics professional/i)).toBeInTheDocument()
    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/personal hereditary cancer risk/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/review the cancer module/i)).not.toBeInTheDocument()
  })

  it("uses sickle-cell trait context for HBB HbS carriers", () => {
    render(
      <VariantDetailPanel
        variant={HBB_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
    expect(screen.getAllByText(/sickle-cell trait/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/not\s+sickle-cell disease/i)).toBeInTheDocument()
    expect(screen.getByText(/usually asymptomatic/i)).toBeInTheDocument()
    expect(screen.getByText(/kidney findings/i)).toBeInTheDocument()
    expect(screen.getByText(/exertional-stress/i)).toBeInTheDocument()
    expect(screen.getByText(/family planning/i)).toBeInTheDocument()
  })

  it("normalizes HBB HbS rsid casing and whitespace", () => {
    render(
      <VariantDetailPanel
        variant={HBB_VARIANT_CASED_RSID}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
    expect(screen.getByText(/sickle-cell trait/i)).toBeInTheDocument()
  })
})
