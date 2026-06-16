import { describe, expect, it } from "vitest"
import {
  formatClinvarConditions,
  formatClinvarConditionsText,
} from "@/lib/clinvar-conditions"

describe("formatClinvarConditions", () => {
  it("returns [] for null / undefined / empty", () => {
    expect(formatClinvarConditions(null)).toEqual([])
    expect(formatClinvarConditions(undefined)).toEqual([])
    expect(formatClinvarConditions("")).toEqual([])
    expect(formatClinvarConditions("   ")).toEqual([])
  })

  it("splits the '|'-delimited CLNDN blob and trims each entry", () => {
    expect(formatClinvarConditions("Cystic fibrosis|Hereditary pancreatitis")).toEqual([
      "Cystic fibrosis",
      "Hereditary pancreatitis",
    ])
    // surrounding whitespace and empty segments are dropped
    expect(formatClinvarConditions(" Cystic fibrosis | | Long QT syndrome ")).toEqual([
      "Cystic fibrosis",
      "Long QT syndrome",
    ])
  })

  it("drops the ClinVar placeholders 'not provided' / 'not specified' (case-insensitive)", () => {
    expect(formatClinvarConditions("not provided")).toEqual([])
    expect(formatClinvarConditions("Cystic fibrosis|not provided|Not Specified")).toEqual([
      "Cystic fibrosis",
    ])
  })

  it("drops non-disease drug-response entries (- Efficacy / - Dosage / - Toxicity)", () => {
    expect(
      formatClinvarConditions(
        "ivacaftor response - Efficacy|warfarin response - Dosage|drug response - Toxicity",
      ),
    ).toEqual([])
    // the drug name preceding a real disease is kept; only the response suffix is filtered
    expect(formatClinvarConditions("Cystic fibrosis|ivacaftor response - Efficacy")).toEqual([
      "Cystic fibrosis",
    ])
  })

  it("de-dupes case-insensitively, keeping the first casing seen", () => {
    expect(
      formatClinvarConditions("Cystic fibrosis|cystic fibrosis|CYSTIC FIBROSIS"),
    ).toEqual(["Cystic fibrosis"])
  })

  it("cleans the real CFTR rs78655421 blob from the issue (#832)", () => {
    const raw =
      "Respiratory ciliopathies including non-CF bronchiectasis|" +
      "Congenital bilateral aplasia of vas deferens from CFTR mutation|" +
      "Hereditary pancreatitis|" +
      "Bronchiectasis with or without elevated sweat chloride 1|" +
      "Cystic fibrosis|not provided|" +
      "Pseudomonas aeruginosa, susceptibility to chronic infection by, in cystic fibrosis|" +
      "CFTR-related disorder|Obstructive azoospermia|ivacaftor response - Efficacy"
    const cleaned = formatClinvarConditions(raw)
    // placeholder and drug-response entries are gone
    expect(cleaned).not.toContain("not provided")
    expect(cleaned).not.toContain("ivacaftor response - Efficacy")
    // genuine diseases survive
    expect(cleaned).toContain("Cystic fibrosis")
    expect(cleaned).toContain("Hereditary pancreatitis")
    expect(cleaned).toContain("CFTR-related disorder")
    expect(cleaned).toHaveLength(8)
  })
})

describe("formatClinvarConditionsText", () => {
  it("joins the cleaned conditions with a comma and a space", () => {
    expect(
      formatClinvarConditionsText("Cystic fibrosis|not provided|Hereditary pancreatitis"),
    ).toBe("Cystic fibrosis, Hereditary pancreatitis")
  })

  it("returns '' when only placeholder / drug-response entries remain (hides the row)", () => {
    expect(formatClinvarConditionsText("not provided|ivacaftor response - Efficacy")).toBe("")
    expect(formatClinvarConditionsText(null)).toBe("")
  })
})
