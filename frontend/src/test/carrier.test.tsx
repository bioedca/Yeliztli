/** Tests for the Carrier Status UI. */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "./test-utils"
import VariantDetailPanel from "@/components/carrier/VariantDetailPanel"
import VariantCard from "@/components/carrier/VariantCard"
import { DEFAULT_COPY_NUMBER_CAVEAT, type CarrierVariant } from "@/types/carrier"

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

const CFTR_COMPOUND_VARIANT: CarrierVariant = {
  ...CFTR_VARIANT,
  rsid: "rs78655421, i4000299",
  genotype: "rs78655421:AG; i4000299:CT",
  zygosity: "possible_compound_heterozygous",
  clinvar_significance: "Pathogenic",
  finding_type: "possible_compound_heterozygote",
  variant_ids: ["rs78655421", "i4000299"],
  component_variants: [
    {
      rsid: "rs78655421",
      chrom: "7",
      pos: 117171029,
      ref: "A",
      alt: "G",
      genotype: "AG",
      zygosity: "het",
      clinvar_significance: "Pathogenic",
      clinvar_review_stars: 3,
      clinvar_accession: "VCV000007105",
      clinvar_conditions: "Cystic fibrosis",
    },
    {
      rsid: "i4000299",
      chrom: "7",
      pos: 117199683,
      ref: "C",
      alt: "T",
      genotype: "CT",
      zygosity: "het",
      clinvar_significance: "Likely pathogenic",
      clinvar_review_stars: 2,
      clinvar_accession: "VCV000007107",
      clinvar_conditions: "Cystic fibrosis",
    },
  ],
  phase_caveat:
    "Genotyping arrays do not phase these variants, so this result cannot distinguish in-trans affected status from same-chromosome variants.",
}

const CFTR_HOMO_AFFECTED_VARIANT: CarrierVariant = {
  ...CFTR_VARIANT,
  rsid: "rs75961395",
  genotype: "TT",
  zygosity: "hom_alt",
  finding_type: "affected_homozygous",
  variant_ids: ["rs75961395"],
  component_variants: [
    {
      rsid: "rs75961395",
      chrom: "7",
      pos: 117559600,
      ref: null,
      alt: null,
      genotype: "TT",
      zygosity: "hom_alt",
      clinvar_significance: "Pathogenic",
      clinvar_review_stars: 2,
      clinvar_accession: "VCV000007106",
      clinvar_conditions: "Cystic fibrosis",
    },
  ],
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

const SMN1_COPY_NUMBER_CAVEAT = DEFAULT_COPY_NUMBER_CAVEAT

const SMN1_VARIANT: CarrierVariant = {
  rsid: "rs121909192",
  gene_symbol: "SMN1",
  genotype: "A/G",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000012345",
  clinvar_review_stars: 2,
  clinvar_conditions: "Spinal muscular atrophy",
  conditions: ["Spinal Muscular Atrophy"],
  inheritance: "AR",
  evidence_level: 4,
  cross_links: [],
  pmids: ["35289093", "21673580"],
  notes: "Point mutations account for a minority of pathogenic SMN1 alleles.",
  copy_number_limited: true,
  copy_number_caveat: SMN1_COPY_NUMBER_CAVEAT,
}

const SMN1_FLAG_ONLY_VARIANT: CarrierVariant = {
  ...SMN1_VARIANT,
  copy_number_caveat: null,
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

  it("shows SMN1 copy-number limitation instead of plain unaffected carrier wording", () => {
    render(
      <VariantDetailPanel
        variant={SMN1_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    const panel = screen.getByTestId("carrier-detail-panel")
    const caveatPanel = screen.getByTestId("carrier-copy-number-caveat-panel")
    const describedBy = panel.getAttribute("aria-describedby")
    expect(caveatPanel).toBeInTheDocument()
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy as string)).toBe(caveatPanel)
    expect(screen.getByText(/copy-number not assessed/i)).toBeInTheDocument()
    expect(screen.getByText(/dosage\/CNV assessment/i)).toBeInTheDocument()
    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
  })

  it("uses the default SMN1 copy-number caveat when only the limitation flag is set", () => {
    render(
      <VariantDetailPanel
        variant={SMN1_FLAG_ONLY_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByTestId("carrier-copy-number-caveat-panel")).toHaveTextContent(
      /SMN1 exon 7 dosage\/copy-number/i,
    )
    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
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

  it("uses affected-status wording for homozygous AR findings", () => {
    render(
      <VariantDetailPanel
        variant={CFTR_HOMO_AFFECTED_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
    expect(screen.getByText(/affected-status result/i)).toBeInTheDocument()
    expect(screen.getByText(/clinical-grade testing/i)).toBeInTheDocument()
    expect(screen.getByText(/\(homozygous affected-status\)/i)).toBeInTheDocument()
    const panel = screen.getByTestId("carrier-detail-panel")
    expect(panel.getAttribute("aria-label")).toMatch(/CFTR affected-status finding detail/i)
    expect(panel.getAttribute("aria-label")).not.toMatch(/carrier/i)
  })

  it("uses phase-aware affected-status wording for possible compound het findings", () => {
    render(
      <VariantDetailPanel
        variant={CFTR_COMPOUND_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByText(/typically unaffected/i)).not.toBeInTheDocument()
    expect(screen.getByText(/if they are in trans/i)).toBeInTheDocument()
    expect(screen.getByText(/do not phase these variants/i)).toBeInTheDocument()
    expect(screen.getByText(/\(possible compound heterozygote\)/i)).toBeInTheDocument()
    expect(screen.getByTestId("carrier-component-variants")).toBeInTheDocument()
    expect(screen.getByText("rs78655421")).toBeInTheDocument()
    expect(screen.getByText("i4000299")).toBeInTheDocument()
  })

  it("keeps 'carrier variant detail' accessible name for AR genes (CFTR)", () => {
    render(
      <VariantDetailPanel variant={CFTR_VARIANT} sampleId={1} geneNote={undefined} onClose={vi.fn()} />,
    )
    const panel = screen.getByTestId("carrier-detail-panel")
    expect(panel.getAttribute("aria-label")).toMatch(/CFTR carrier variant detail/i)
  })

  it("drops 'carrier' from the accessible name for AD genes (BRCA1)", () => {
    render(
      <VariantDetailPanel variant={BRCA1_VARIANT} sampleId={1} geneNote={undefined} onClose={vi.fn()} />,
    )
    const panel = screen.getByTestId("carrier-detail-panel")
    expect(panel.getAttribute("aria-label")).not.toMatch(/carrier/i)
    expect(panel.getAttribute("aria-label")).toMatch(/BRCA1 variant detail/i)
  })
})

describe("Carrier VariantCard genotype-line label (#540)", () => {
  it("styles combined Pathogenic/Likely pathogenic as a red pathogenic card (#687)", () => {
    render(
      <VariantCard
        variant={{
          ...HBB_VARIANT,
          clinvar_significance: "Pathogenic/Likely pathogenic",
        }}
        onClick={vi.fn()}
        sampleId={1}
      />,
    )

    expect(screen.getByTestId("carrier-variant-card")).toHaveClass("bg-red-50")
    expect(screen.getByText("Pathogenic/Likely pathogenic")).toHaveClass("bg-red-100")
  })

  it("keeps '(heterozygous carrier)' for autosomal-recessive genes (CFTR)", () => {
    render(<VariantCard variant={CFTR_VARIANT} onClick={vi.fn()} sampleId={1} />)
    expect(screen.getByText(/\(heterozygous carrier\)/i)).toBeInTheDocument()
    // The accessible name keeps "carrier" for a recessive gene.
    expect(screen.getByRole("button", { name: /CFTR.*carrier/i })).toBeInTheDocument()
  })

  it("drops the 'carrier' framing for autosomal-dominant BRCA1 (personal-risk gene)", () => {
    render(<VariantCard variant={BRCA1_VARIANT} onClick={vi.fn()} sampleId={1} />)
    // The genotype line must not call a dominant-risk variant a "carrier"...
    expect(screen.queryByText(/\(heterozygous carrier\)/i)).not.toBeInTheDocument()
    // ...but should still annotate the genotype as heterozygous.
    expect(screen.getByText(/\(heterozygous\)/i)).toBeInTheDocument()
    // ...and the footer still labels the gene Autosomal Dominant (no contradiction).
    expect(screen.getByText(/Autosomal Dominant/i)).toBeInTheDocument()
    // The accessible name is also de-"carrier"-ed for screen-reader users.
    const card = screen.getByTestId("carrier-variant-card")
    expect(card.getAttribute("aria-label")).not.toMatch(/carrier/i)
    expect(card.getAttribute("aria-label")).toMatch(/heterozygous variant/i)
  })

  it("drops the 'carrier' framing for any AD gene, even without a cancer cross-link", () => {
    render(<VariantCard variant={AD_NON_CANCER_VARIANT} onClick={vi.fn()} sampleId={1} />)
    expect(screen.queryByText(/\(heterozygous carrier\)/i)).not.toBeInTheDocument()
    expect(screen.getByText(/\(heterozygous\)/i)).toBeInTheDocument()
  })

  it("labels possible compound het cards as affected-status findings", () => {
    render(<VariantCard variant={CFTR_COMPOUND_VARIANT} onClick={vi.fn()} sampleId={1} />)

    expect(screen.queryByText(/\(heterozygous carrier\)/i)).not.toBeInTheDocument()
    expect(screen.getByText(/\(possible compound heterozygote\)/i)).toBeInTheDocument()
    expect(screen.getByText(/possible affected-status pattern/i)).toBeInTheDocument()
    const card = screen.getByTestId("carrier-variant-card")
    expect(card.getAttribute("aria-label")).toMatch(/possible compound heterozygote affected-status/i)
    expect(card.getAttribute("aria-label")).not.toMatch(/carrier/i)
  })

  it("labels homozygous AR cards as affected-status findings", () => {
    render(<VariantCard variant={CFTR_HOMO_AFFECTED_VARIANT} onClick={vi.fn()} sampleId={1} />)

    expect(screen.queryByText(/\(heterozygous carrier\)/i)).not.toBeInTheDocument()
    expect(screen.getByText(/\(homozygous affected-status\)/i)).toBeInTheDocument()
    expect(screen.getByText(/affected-status result/i)).toBeInTheDocument()
    const card = screen.getByTestId("carrier-variant-card")
    expect(card.getAttribute("aria-label")).toMatch(/homozygous affected-status finding/i)
    expect(card.getAttribute("aria-label")).not.toMatch(/carrier/i)
  })

  it("shows SMN1 copy-number limitation on carrier cards", () => {
    render(<VariantCard variant={SMN1_VARIANT} onClick={vi.fn()} sampleId={1} />)

    const card = screen.getByTestId("carrier-variant-card")
    const caveat = screen.getByTestId("carrier-copy-number-caveat")
    const describedBy = card.getAttribute("aria-describedby")
    expect(caveat).toBeInTheDocument()
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy as string)).toBe(caveat)
    expect(screen.getByText(/copy-number not assessed/i)).toBeInTheDocument()
    expect(screen.getByText(/dosage\/CNV assessment/i)).toBeInTheDocument()
  })

  it("uses the default SMN1 copy-number caveat on cards when only the flag is set", () => {
    render(<VariantCard variant={SMN1_FLAG_ONLY_VARIANT} onClick={vi.fn()} sampleId={1} />)

    expect(screen.getByTestId("carrier-copy-number-caveat")).toHaveTextContent(
      /SMN1 exon 7 dosage\/copy-number/i,
    )
  })
})
