import { describe, it, expect } from "vitest"
import { polyphen2Display, siftDisplay } from "@/lib/insilico"

describe("siftDisplay (#1753)", () => {
  it("maps the single-char dbNSFP codes to readable labels + category colours", () => {
    expect(siftDisplay("D")).toEqual({
      label: "Damaging",
      colorClass: "text-red-700 dark:text-red-400",
    })
    expect(siftDisplay("T")).toEqual({
      label: "Tolerated",
      colorClass: "text-green-700 dark:text-green-400",
    })
  })

  it("accepts full-word aliases and normalizes case and whitespace", () => {
    expect(siftDisplay(" deleterious ").label).toBe("Damaging")
    expect(siftDisplay("damaging").label).toBe("Damaging")
    expect(siftDisplay("tolerated").label).toBe("Tolerated")
  })

  it("shows an unrecognised value verbatim in a neutral colour", () => {
    expect(siftDisplay("UNKNOWN_CODE")).toEqual({
      label: "UNKNOWN CODE",
      colorClass: "text-muted-foreground",
    })
  })
})

describe("polyphen2Display (#680)", () => {
  it("maps the single-char dbNSFP codes to readable labels + category colours", () => {
    expect(polyphen2Display("D")).toEqual({
      label: "Probably Damaging",
      colorClass: "text-red-700 dark:text-red-400",
    })
    expect(polyphen2Display("P")).toEqual({
      label: "Possibly Damaging",
      colorClass: "text-amber-700 dark:text-amber-400",
    })
    expect(polyphen2Display("B")).toEqual({
      label: "Benign",
      colorClass: "text-green-700 dark:text-green-400",
    })
  })

  it("accepts the full-word aliases defensively", () => {
    expect(polyphen2Display("probably_damaging").label).toBe("Probably Damaging")
    expect(polyphen2Display("possibly_damaging").label).toBe("Possibly Damaging")
    expect(polyphen2Display("probably damaging").label).toBe("Probably Damaging")
    expect(polyphen2Display("possibly   damaging").label).toBe("Possibly Damaging")
    expect(polyphen2Display("benign").label).toBe("Benign")
  })

  it("is case- and whitespace-insensitive", () => {
    expect(polyphen2Display(" d ").label).toBe("Probably Damaging")
    expect(polyphen2Display("Probably_Damaging").colorClass).toContain("text-red-700")
  })

  it("shows an unrecognised value verbatim in a neutral colour — never silently benign", () => {
    const result = polyphen2Display("X")
    expect(result.label).toBe("X")
    expect(result.colorClass).toBe("text-muted-foreground")
  })
})
