import { describe, expect, it } from "vitest"

import {
  gnomadNoFrequencyDetail,
  gnomadNoFrequencyLabel,
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
