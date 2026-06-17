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

  it.each([
    "Pathogenic, low penetrance",
    "Pathogenic/Established risk allele",
    "Established risk allele",
    "Uncertain risk allele",
  ])("styles lower-penetrance/risk-allele terms as a distinct amber tier: %s", (significance) => {
    expect(getClinvarSignificanceTone(significance)).toBe("low-penetrance")
    expect(getClinvarSignificanceTextClass(significance)).toContain("text-amber")
    expect(getClinvarSignificanceCardConfig(significance).bg).toContain("bg-amber")
    expect(getClinvarSignificanceBadgeClass(significance)).toContain("bg-amber")
    expect(getClinvarSignificanceBadgeClass(significance)).not.toContain("bg-red")
  })

  it("keeps risk factor distinct from risk allele", () => {
    expect(getClinvarSignificanceTone("Pathogenic|risk factor")).toBe("pathogenic")
    expect(getClinvarSignificanceBadgeClass("Pathogenic|risk factor")).toContain("bg-red")
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
