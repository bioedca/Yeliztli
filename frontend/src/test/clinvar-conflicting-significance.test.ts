/** #799: ClinVar "Conflicting classifications of pathogenicity" must never render
 * as a confirmed Pathogenic (red) call.
 *
 * The string contains the substring "pathogenic" (inside "pathogeni*city*") and no
 * "benign", so a raw `significance.toLowerCase().includes("pathogenic")` test
 * coloured it identically to a true Pathogenic finding — a false-positive direction
 * error. It is an aggregate of *disagreeing* submitter classifications, not a
 * pathogenic assertion. These assertions lock the shared classifier that the fixed
 * surfaces now route through: the variant-detail / query-results badges
 * (getClinvarSignificanceBadgeClass) and the Nightingale protein-viewer dot
 * (getClinvarSignificanceHexColor). The ClinvarBreakdown bar is covered in
 * clinvar-breakdown.test.tsx.
 */

import { describe, it, expect } from "vitest"
import {
  getClinvarSignificanceTone,
  getClinvarSignificanceBadgeClass,
  getClinvarSignificanceHexColor,
} from "@/lib/clinvar-significance"

const CONFLICTING = "Conflicting classifications of pathogenicity" // current ClinVar wording
const CONFLICTING_LEGACY = "Conflicting interpretations of pathogenicity" // pre-rename wording

describe("ClinVar conflicting classification is not styled pathogenic (#799)", () => {
  it("the shared tone classifier maps conflicting → uncertain (not pathogenic)", () => {
    expect(getClinvarSignificanceTone(CONFLICTING)).toBe("uncertain")
    expect(getClinvarSignificanceTone(CONFLICTING_LEGACY)).toBe("uncertain")
    // Sanity — a genuine pathogenic call still classifies pathogenic.
    expect(getClinvarSignificanceTone("Pathogenic")).toBe("pathogenic")
  })

  it("the variant-detail / query-results badge is not the red pathogenic pill", () => {
    expect(getClinvarSignificanceBadgeClass(CONFLICTING)).not.toContain("red")
    expect(getClinvarSignificanceBadgeClass("Pathogenic")).toContain("red")
  })

  it("the Nightingale protein-viewer dot is amber (VUS), not the #DC2626 red", () => {
    expect(getClinvarSignificanceHexColor(CONFLICTING)).toBe("#D97706")
    expect(getClinvarSignificanceHexColor(CONFLICTING)).not.toBe("#DC2626")
    // Sanity — the discriminating cases still resolve correctly.
    expect(getClinvarSignificanceHexColor("Pathogenic")).toBe("#DC2626")
    expect(getClinvarSignificanceHexColor("Benign")).toBe("#16A34A")
    expect(getClinvarSignificanceHexColor("Likely pathogenic")).toBe("#EA580C")
  })
})
