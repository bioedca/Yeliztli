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
      called_snps: 2,
      total_snps: 3,
      missing_snps: ["rs1049434"],
      no_call_snps: [],
      indeterminate_snps: ["rs4341"],
    }

    expect(pathwayLevelDisplayLabel(pathway, "Standard")).toBe("Tested Standard")
    expect(pathwayCoverageCaveat(pathway)).toBe(
      "Standard result is based on interpreted SNPs only; 1 tracked SNP (1 off-chip) not assessed.",
    )
  })

  // #2178: `called_snps` counts observed SNPs including indeterminate ones, so
  // these render a clean negative unless the interpreted count is derived. The
  // same helper backs the traits card, the fitness card, and the backend
  // report/SVG paths, so one result cannot be labelled differently per surface.
  it("reports not assessed when every observed SNP is indeterminate", () => {
    const pathway = {
      level: "Standard",
      called_snps: 1,
      total_snps: 1,
      missing_snps: [],
      no_call_snps: [],
      indeterminate_snps: ["rs9939609"],
    }

    expect(pathwayLevelDisplayLabel(pathway, "Standard")).toBe("Not Assessed")
    // "based on interpreted SNPs only" would presume an interpreted SNP.
    expect(pathwayCoverageCaveat(pathway)).toBeNull()
  })

  it("reports not assessed when nothing is interpreted and SNPs are missing", () => {
    const pathway = {
      level: "Standard",
      called_snps: 1,
      total_snps: 2,
      missing_snps: ["rs1049434"],
      no_call_snps: [],
      indeterminate_snps: ["rs4341"],
    }

    expect(pathwayLevelDisplayLabel(pathway, "Standard")).toBe("Not Assessed")
  })

  it("keeps a non-Standard level unchanged when nothing is interpreted", () => {
    const pathway = {
      level: "Elevated",
      called_snps: 1,
      total_snps: 1,
      missing_snps: [],
      no_call_snps: [],
      indeterminate_snps: ["rs9939609"],
    }

    expect(pathwayLevelDisplayLabel(pathway, "Elevated")).toBe("Elevated")
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
