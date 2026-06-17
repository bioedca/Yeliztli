import { describe, it, expect } from "vitest"
import { getRareVariantCategoryMeta } from "@/lib/rare-variant-category"

// Every category the backend emits in rare_variant_finder.py.
const BACKEND_CATEGORIES = [
  "clinvar_pathogenic",
  "clinvar_pathogenic_low_confidence",
  "clinvar_low_penetrance_or_risk_allele",
  "ensemble_pathogenic",
  "novel",
  "rare",
]

describe("getRareVariantCategoryMeta", () => {
  it("never surfaces a raw snake_case key for any backend category (#919)", () => {
    for (const cat of BACKEND_CATEGORIES) {
      const { label } = getRareVariantCategoryMeta(cat)
      expect(label).not.toMatch(/_/) // no dev key
      expect(label).not.toBe(cat)
    }
  })

  it("gives the 0★ low-confidence tier its own cautionary, non-gray style", () => {
    const lowConf = getRareVariantCategoryMeta("clinvar_pathogenic_low_confidence")
    const rare = getRareVariantCategoryMeta("rare")
    // The whole bug: it must NOT look like an unremarkable `rare` row.
    expect(lowConf.className).not.toBe(rare.className)
    expect(lowConf.className).toContain("amber")
    expect(lowConf.label).toBe("Pathogenic (low confidence)")
    // …and distinct from the bold-red high-confidence tier.
    expect(lowConf.className).not.toBe(getRareVariantCategoryMeta("clinvar_pathogenic").className)
  })

  it("gives low-penetrance/risk-allele ClinVar findings a friendly cautionary tier", () => {
    const meta = getRareVariantCategoryMeta("clinvar_low_penetrance_or_risk_allele")
    expect(meta.label).toBe("Low-penetrance / risk allele")
    expect(meta.className).toContain("amber")
    expect(meta.className).not.toContain("red")
  })

  it("styles the handled tiers with their established tones", () => {
    expect(getRareVariantCategoryMeta("clinvar_pathogenic").className).toContain("red")
    expect(getRareVariantCategoryMeta("clinvar_low_penetrance_or_risk_allele").className).toContain(
      "amber",
    )
    expect(getRareVariantCategoryMeta("ensemble_pathogenic").className).toContain("orange")
    expect(getRareVariantCategoryMeta("novel").className).toContain("blue")
    expect(getRareVariantCategoryMeta("rare").className).toContain("muted")
  })

  it("gives friendly labels for the handled tiers", () => {
    expect(getRareVariantCategoryMeta("clinvar_pathogenic").label).toBe("ClinVar Pathogenic")
    expect(getRareVariantCategoryMeta("clinvar_low_penetrance_or_risk_allele").label).toBe(
      "Low-penetrance / risk allele",
    )
    expect(getRareVariantCategoryMeta("ensemble_pathogenic").label).toBe("Predicted Pathogenic")
    expect(getRareVariantCategoryMeta("novel").label).toBe("Novel")
    expect(getRareVariantCategoryMeta("rare").label).toBe("Rare")
  })

  it("humanizes an unforeseen category instead of leaking the raw key", () => {
    const meta = getRareVariantCategoryMeta("some_future_tier")
    expect(meta.label).toBe("Some Future Tier")
    expect(meta.className).toContain("muted") // safe neutral fallback tone
  })
})
