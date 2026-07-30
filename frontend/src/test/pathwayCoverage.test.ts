import { describe, expect, it } from "vitest"

import { pathwayCoverageCaveat, pathwayLevelDisplayLabel } from "@/lib/pathwayCoverage"

describe("pathwayCoverage", () => {
  it("qualifies a Standard level when tracked SNPs are off-chip", () => {
    const pathway = {
      level: "Standard",
      called_snps: 1,
      total_snps: 5,
      missing_snps: ["rs10156191", "rs1049742", "rs1049793", "rs2052129"],
      no_call_snps: [],
    }

    expect(pathwayLevelDisplayLabel(pathway, "Standard")).toBe("Tested Standard")
    expect(pathwayCoverageCaveat(pathway)).toBe(
      "No variants of concern among tested SNPs; 4 tracked SNPs (4 off-chip) not assessed.",
    )
  })

  it("reports not assessed when no tracked SNPs were called", () => {
    const pathway = {
      level: "Standard",
      called_snps: 0,
      total_snps: 2,
      missing_snps: ["rs1", "rs2"],
      no_call_snps: ["rs2"],
    }

    expect(pathwayLevelDisplayLabel(pathway, "Standard")).toBe("Not Assessed")
    expect(pathwayCoverageCaveat(pathway)).toBe(
      "No tracked SNPs were assessed; 2 tracked SNPs (1 off-chip, 1 no-call) not assessed.",
    )
  })

  it("does not claim no variants of concern when observed SNPs are indeterminate", () => {
    const pathway = {
      level: "Standard",
      called_snps: 1,
      total_snps: 2,
      missing_snps: ["rs1049434"],
      no_call_snps: [],
      indeterminate_snps: ["rs4341"],
    }

    expect(pathwayCoverageCaveat(pathway)).toBe(
      "Standard result is based on interpreted SNPs only; 1 tracked SNP (1 off-chip) not assessed.",
    )
  })

  it("keeps complete Standard coverage unchanged", () => {
    const pathway = {
      level: "Standard",
      called_snps: 2,
      total_snps: 2,
      missing_snps: [],
      no_call_snps: [],
    }

    expect(pathwayLevelDisplayLabel(pathway, "Standard")).toBe("Standard")
    expect(pathwayCoverageCaveat(pathway)).toBeNull()
  })

  it("flags an indeterminate call even at full coverage (#2178)", () => {
    // The DRD4 rs747302 witness: every tracked SNP was called, so the
    // missing-SNP guard short-circuited and no caveat was produced — the
    // uncertainty was visible only by opening SNP detail.
    const pathway = {
      level: "Standard",
      called_snps: 2,
      total_snps: 2,
      missing_snps: [],
      no_call_snps: [],
      indeterminate_snps: ["rs747302"],
    }

    expect(pathwayCoverageCaveat(pathway)).toBe(
      "Standard result is based on interpreted SNPs only; 1 observed SNP could not be interpreted.",
    )
  })

  it("pluralises multiple indeterminate calls at full coverage", () => {
    const pathway = {
      level: "Standard",
      called_snps: 3,
      total_snps: 3,
      missing_snps: [],
      no_call_snps: [],
      indeterminate_snps: ["rs747302", "rs1800497"],
    }

    expect(pathwayCoverageCaveat(pathway)).toBe(
      "Standard result is based on interpreted SNPs only; 2 observed SNPs could not be interpreted.",
    )
  })

  it("does not flag a non-Standard pathway at full coverage", () => {
    // Discriminating control: an Elevated/Moderate card already communicates a
    // finding, so the clean-negative problem does not arise and the caveat must
    // stay null rather than being added unconditionally.
    const pathway = {
      level: "Moderate",
      called_snps: 2,
      total_snps: 2,
      missing_snps: [],
      no_call_snps: [],
      indeterminate_snps: ["rs747302"],
    }

    expect(pathwayCoverageCaveat(pathway)).toBeNull()
  })
})
