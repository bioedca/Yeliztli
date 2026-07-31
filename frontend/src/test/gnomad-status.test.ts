import { describe, expect, it } from "vitest"

import {
  gnomadNoFrequencyDetail,
  gnomadNoFrequencyLabel,
  gnomadNoFrequencyShortLabel,
  isGnomadAlleleAmbiguous,
} from "@/lib/gnomad-status"

describe("gnomadNoFrequencyLabel", () => {
  // #2171: a shared rsID whose carried ALT cannot be resolved has its frequency
  // withheld. gnomAD *does* list the variant, so falling through to the absent
  // branch would tell the user something untrue about gnomAD's contents.
  it("does not claim a withheld allele is absent from gnomAD", () => {
    expect(gnomadNoFrequencyLabel("allele_ambiguous")).toBe("Allele not resolved in gnomAD")
    expect(gnomadNoFrequencyLabel("allele_ambiguous")).not.toBe("Not in gnomAD")
  })

  it("explains why no frequency is shown", () => {
    expect(gnomadNoFrequencyDetail("allele_ambiguous")).toContain("several alternate alleles")
    expect(gnomadNoFrequencyDetail("allele_ambiguous")).not.toContain("Not found in gnomAD")
  })

  it("outranks the novel flag, which would also be untrue here", () => {
    // The variant is catalogued, so "Novel" is wrong for the same reason.
    expect(gnomadNoFrequencyLabel("allele_ambiguous", true)).toBe("Allele not resolved in gnomAD")
  })

  // Discriminating controls: the new branch must not swallow the existing states.
  it("keeps the uncovered-source wording", () => {
    expect(gnomadNoFrequencyLabel("source_uncovered")).toBe(
      "Not assessed by current gnomAD exome source",
    )
  })

  it("keeps novel and absent unchanged", () => {
    expect(gnomadNoFrequencyLabel(null, true)).toBe("Novel")
    expect(gnomadNoFrequencyLabel(null)).toBe("Not in gnomAD")
    expect(gnomadNoFrequencyDetail(null)).toBe("Not found in gnomAD")
  })

  it("recognises only the exact status string", () => {
    expect(isGnomadAlleleAmbiguous("allele_ambiguous")).toBe(true)
    expect(isGnomadAlleleAmbiguous("observed")).toBe(false)
    expect(isGnomadAlleleAmbiguous(null)).toBe(false)
  })
})

describe("gnomadNoFrequencyShortLabel", () => {
  // #2214 review: the variant table, side panel and detail page each
  // special-cased `source_uncovered` inline, so an allele-ambiguous row rendered
  // as an empty cell or a dash -- indistinguishable from genuine absence. They
  // now share this helper so they cannot drift apart again.
  it("labels an unresolved allele instead of leaving the cell blank", () => {
    expect(gnomadNoFrequencyShortLabel("allele_ambiguous")).toBe("Allele unresolved")
  })

  it("keeps the uncovered-source label", () => {
    expect(gnomadNoFrequencyShortLabel("source_uncovered")).toBe("Not assessed")
  })

  it("returns null for genuine absence so callers render their own empty state", () => {
    // Discriminating control: labelling everything would put text in every
    // blank AF cell in the variant table.
    expect(gnomadNoFrequencyShortLabel(null)).toBeNull()
    expect(gnomadNoFrequencyShortLabel("observed")).toBeNull()
  })
})

describe("locus_unresolved", () => {
  // #2214 review: gnomAD lists the rsID, just not at the sample's coordinate.
  // The alleles may be identical, so the allele-ambiguity wording would be a
  // false explanation and would hide a build/mapping mismatch.
  it("does not reuse the allele-ambiguity explanation", () => {
    expect(gnomadNoFrequencyDetail("locus_unresolved")).toContain("other genomic positions")
    expect(gnomadNoFrequencyDetail("locus_unresolved")).not.toContain("alternate alleles")
  })

  it("does not claim absence from gnomAD", () => {
    expect(gnomadNoFrequencyLabel("locus_unresolved")).toBe("Position not matched in gnomAD")
    expect(gnomadNoFrequencyLabel("locus_unresolved")).not.toBe("Not in gnomAD")
  })

  it("has its own compact label", () => {
    expect(gnomadNoFrequencyShortLabel("locus_unresolved")).toBe("Position unmatched")
  })

  it("leaves the allele-ambiguous wording alone", () => {
    // Discriminating control: the two states must stay distinguishable.
    expect(gnomadNoFrequencyShortLabel("allele_ambiguous")).toBe("Allele unresolved")
    expect(gnomadNoFrequencyDetail("allele_ambiguous")).toContain("alternate alleles")
  })
})

describe("alias_unresolved", () => {
  // #2214 review: gnomAD lists a row at each of the sample's positions, so both
  // the allele and the position explanations would be false statements.
  it("does not blame the position or the allele", () => {
    const detail = gnomadNoFrequencyDetail("alias_unresolved")
    expect(detail).toContain("More than one call in your file")
    expect(detail).not.toContain("other genomic positions")
    expect(detail).not.toContain("alternate alleles")
  })

  it("has its own labels", () => {
    expect(gnomadNoFrequencyLabel("alias_unresolved")).toBe("Shared rsID across positions")
    expect(gnomadNoFrequencyShortLabel("alias_unresolved")).toBe("Shared rsID")
  })

  it("leaves the sibling states alone", () => {
    // Discriminating control: four withhold reasons must stay distinguishable.
    expect(gnomadNoFrequencyShortLabel("locus_unresolved")).toBe("Position unmatched")
    expect(gnomadNoFrequencyShortLabel("allele_ambiguous")).toBe("Allele unresolved")
    expect(gnomadNoFrequencyShortLabel("source_uncovered")).toBe("Not assessed")
  })
})
