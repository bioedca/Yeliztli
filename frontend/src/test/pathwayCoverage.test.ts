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
})
