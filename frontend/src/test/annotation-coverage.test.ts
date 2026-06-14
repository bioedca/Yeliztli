import { describe, expect, it } from "vitest"

import {
  COVERAGE_BITS,
  coverageTooltip,
  decodeCoverageSources,
} from "@/components/variant-table/annotation-coverage"
import { allColumns } from "@/components/variant-table/columns"

describe("decodeCoverageSources", () => {
  it("decodes single-bit masks to one source", () => {
    expect(decodeCoverageSources(0b0000001)).toEqual(["VEP"])
    expect(decodeCoverageSources(0b0100000)).toEqual(["GWAS"]) // 32
    // bit 7 (AlphaMissense) — the old `padStart(6)` binary display dropped it
    expect(decodeCoverageSources(0b10000000)).toEqual(["AlphaMissense"]) // 128
  })

  it("decodes multi-bit masks in ascending bit order", () => {
    // 45 = 0b00101101 → bits 0,2,3,5
    expect(decodeCoverageSources(45)).toEqual(["VEP", "gnomAD", "dbNSFP", "GWAS"])
    // 5 = bits 0,2
    expect(decodeCoverageSources(5)).toEqual(["VEP", "gnomAD"])
    // 63 = bits 0-5 (six sources)
    expect(decodeCoverageSources(63)).toEqual([
      "VEP",
      "ClinVar",
      "gnomAD",
      "dbNSFP",
      "gene_phenotype",
      "GWAS",
    ])
    // 255 = all eight bits
    expect(decodeCoverageSources(255)).toHaveLength(8)
  })

  it("returns [] for 0, null, and undefined", () => {
    expect(decodeCoverageSources(0)).toEqual([])
    expect(decodeCoverageSources(null)).toEqual([])
    expect(decodeCoverageSources(undefined)).toEqual([])
  })
})

describe("coverageTooltip", () => {
  it("lists the present sources with a count", () => {
    expect(coverageTooltip(5)).toBe("Annotation sources (2): VEP, gnomAD")
  })

  it("states explicitly when no source covered the variant", () => {
    expect(coverageTooltip(0)).toBe("No annotation sources covered this variant")
    expect(coverageTooltip(null)).toBe("No annotation sources covered this variant")
  })
})

describe("annotation_coverage column (#580)", () => {
  const col = allColumns.find((c) => "accessorKey" in c && c.accessorKey === "annotation_coverage")

  it("is labelled 'Annotations', not the misleading 'Coverage'", () => {
    expect(col).toBeDefined()
    expect(col?.header).toBe("Annotations")
  })

  it("renders the source COUNT (not a raw binary bitmask)", () => {
    // The cell renderer must turn the bitmask into a count, never `toString(2)`.
    const cell = (col as unknown as { cell: (info: { getValue: () => number | null }) => unknown })
      .cell
    const rendered = cell({ getValue: () => 45 }) as {
      props: { children: string; title: string }
    }
    expect(rendered.props.children).toBe("4") // 45 → 4 sources
    expect(rendered.props.title).toContain("VEP")
    expect(cell({ getValue: () => null })).toBe("") // null → blank, as before
  })

  it("COVERAGE_BITS is ascending and covers all eight sources", () => {
    expect(COVERAGE_BITS).toHaveLength(8)
    const bits = COVERAGE_BITS.map(([b]) => b)
    expect(bits).toEqual([...bits].sort((a, b) => a - b))
  })
})
