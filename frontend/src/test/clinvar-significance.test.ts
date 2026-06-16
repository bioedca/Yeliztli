import { describe, expect, it } from "vitest"
import {
  getClinvarSignificanceBadgeClass,
  getClinvarSignificanceCardConfig,
  getClinvarSignificanceTextClass,
  getClinvarSignificanceTone,
} from "@/lib/clinvar-significance"

describe("ClinVar significance styling", () => {
  it.each([
    "Pathogenic/Likely pathogenic",
    "Likely pathogenic/Pathogenic",
    "Pathogenic/Likely_pathogenic",
  ])("treats combined P/LP forms as pathogenic red: %s", (significance) => {
    expect(getClinvarSignificanceTone(significance)).toBe("pathogenic")
    expect(getClinvarSignificanceTextClass(significance)).toContain("text-red")
    expect(getClinvarSignificanceCardConfig(significance).bg).toContain("bg-red")
    expect(getClinvarSignificanceBadgeClass(significance)).toContain("bg-red")
  })

  it("keeps standalone likely pathogenic orange", () => {
    expect(getClinvarSignificanceTone("Likely pathogenic")).toBe("likely-pathogenic")
    expect(getClinvarSignificanceTextClass("Likely pathogenic")).toContain("text-orange")
  })

  it("keeps benign and uncertain forms non-red", () => {
    expect(getClinvarSignificanceTone("Benign/Likely benign")).toBe("benign")
    expect(getClinvarSignificanceTextClass("Benign/Likely benign")).toContain("text-green")
    expect(getClinvarSignificanceTone("Conflicting interpretations of pathogenicity")).toBe(
      "uncertain",
    )
    expect(
      getClinvarSignificanceTextClass("Conflicting interpretations of pathogenicity"),
    ).not.toContain("red")
  })

  it.each([
    "Pathogenic/Likely benign",
    "Likely pathogenic/Benign",
    "VUS/Pathogenic",
    "Pathogenic/VUS",
  ])("keeps mixed or VUS combinations non-red: %s", (significance) => {
    expect(getClinvarSignificanceTone(significance)).toBe("uncertain")
    expect(getClinvarSignificanceTextClass(significance)).toContain("text-yellow")
    expect(getClinvarSignificanceTextClass(significance)).not.toContain("red")
  })
})
