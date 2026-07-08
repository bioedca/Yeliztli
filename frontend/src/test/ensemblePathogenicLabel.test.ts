import { describe, expect, it } from "vitest"
import {
  formatEnsemblePathogenicBadgeLabel,
  formatEnsemblePathogenicEvidenceLabel,
  formatEnsemblePathogenicStatus,
} from "@/lib/ensemblePathogenicLabel"

describe("ensemblePathogenicLabel", () => {
  it("includes bounded assessed-axis counts in evidence labels", () => {
    expect(formatEnsemblePathogenicEvidenceLabel(2, 2)).toBe(
      "strict majority of assessed independent axes deleterious (2/2)",
    )
  })

  it("includes bounded assessed-axis counts in badge labels", () => {
    expect(formatEnsemblePathogenicBadgeLabel(3, 4)).toBe(
      "Ensemble pathogenic: strict majority of assessed independent axes deleterious (3/4)",
    )
  })

  it("includes bounded assessed-axis counts in positive status labels", () => {
    expect(formatEnsemblePathogenicStatus(true, 4, 5)).toBe(
      "Yes - strict majority of assessed independent axes deleterious (4/5)",
    )
  })

  it("returns No when the ensemble is not pathogenic", () => {
    expect(formatEnsemblePathogenicStatus(false, 2, 2)).toBe("No")
    expect(formatEnsemblePathogenicStatus(null, 2, 2)).toBe("No")
    expect(formatEnsemblePathogenicStatus(undefined, 2, 2)).toBe("No")
  })

  it("falls back when deleterious count exceeds total assessed", () => {
    expect(formatEnsemblePathogenicEvidenceLabel(3, 2)).toBe(
      "strict majority of assessed independent axes deleterious",
    )
    expect(formatEnsemblePathogenicBadgeLabel(3, 2)).toBe(
      "Ensemble pathogenic: strict majority of assessed independent axes deleterious",
    )
    expect(formatEnsemblePathogenicStatus(true, 3, 2)).toBe(
      "Yes - strict majority of assessed independent axes deleterious",
    )
  })

  it("falls back when total assessed is zero", () => {
    expect(formatEnsemblePathogenicEvidenceLabel(0, 0)).toBe(
      "strict majority of assessed independent axes deleterious",
    )
    expect(formatEnsemblePathogenicBadgeLabel(0, 0)).toBe(
      "Ensemble pathogenic: strict majority of assessed independent axes deleterious",
    )
    expect(formatEnsemblePathogenicStatus(true, 0, 0)).toBe(
      "Yes - strict majority of assessed independent axes deleterious",
    )
  })

  it("falls back when count inputs are missing", () => {
    expect(formatEnsemblePathogenicEvidenceLabel()).toBe(
      "strict majority of assessed independent axes deleterious",
    )
    expect(formatEnsemblePathogenicEvidenceLabel(null, 2)).toBe(
      "strict majority of assessed independent axes deleterious",
    )
    expect(formatEnsemblePathogenicBadgeLabel(undefined, 2)).toBe(
      "Ensemble pathogenic: strict majority of assessed independent axes deleterious",
    )
    expect(formatEnsemblePathogenicStatus(true, 2, undefined)).toBe(
      "Yes - strict majority of assessed independent axes deleterious",
    )
  })
})
