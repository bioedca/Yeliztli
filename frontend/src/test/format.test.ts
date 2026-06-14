/** Tests for formatAlleleFrequency — the single gnomAD-AF display unit (#564). */

import { describe, it, expect } from "vitest"
import { formatAlleleFrequency } from "@/lib/format"

describe("formatAlleleFrequency", () => {
  it("renders null as an em-dash", () => {
    expect(formatAlleleFrequency(null)).toBe("—")
  })

  it("renders exact zero as 0 (not 0.00e+0)", () => {
    expect(formatAlleleFrequency(0)).toBe("0")
  })

  it("renders common frequencies as a 4-dp raw fraction", () => {
    expect(formatAlleleFrequency(0.012)).toBe("0.0120")
    expect(formatAlleleFrequency(0.0002)).toBe("0.0002")
  })

  it("renders sub-0.0001 frequencies in scientific notation (still a raw fraction)", () => {
    expect(formatAlleleFrequency(0.00009)).toBe("9.00e-5")
    expect(formatAlleleFrequency(0.000005)).toBe("5.00e-6")
  })

  it("never mixes units — no value renders as a percentage (#564 regression)", () => {
    // The bug: AF >= 0.0001 rendered as a percentage while AF < 0.0001 rendered
    // as a bare fraction, so two near-identical frequencies looked ~100x apart.
    // Every output must now be a fraction (no "%"), across the full range that
    // spans the old 0.0001 split point.
    const afs = [0.5, 0.012, 0.0002, 0.00012, 0.0001, 0.00009, 0.000005, 1e-8]
    for (const af of afs) {
      expect(formatAlleleFrequency(af), `af=${af}`).not.toContain("%")
    }
  })

  it("keeps the two adjacent issue values in the same (fraction) unit and order", () => {
    // 0.00012 (0.012%) and 0.00009 (0.009%) are near-equal; pre-fix one rendered
    // as "0.012%" and the other as "9.0e-5". Both must be bare fractions now, and
    // the larger frequency must read back as larger.
    const higher = formatAlleleFrequency(0.00012)
    const lower = formatAlleleFrequency(0.00009)
    expect(higher).not.toContain("%")
    expect(lower).toBe("9.00e-5")
    expect(Number(higher)).toBeGreaterThan(Number(lower))
  })
})
