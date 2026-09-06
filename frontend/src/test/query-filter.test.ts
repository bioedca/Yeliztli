import { describe, it, expect } from "vitest"
import { normalizeFilterForApi, toEditorFilter } from "@/lib/query-filter"
import type { RuleGroupModel } from "@/types/query-builder"

function filterWith(rules: RuleGroupModel["rules"]): RuleGroupModel {
  return { combinator: "and", not: false, rules }
}

describe("normalizeFilterForApi", () => {
  it("turns a comma-joined between value into a two-element array", () => {
    const out = normalizeFilterForApi(
      filterWith([{ field: "gnomad_af_global", operator: "between", value: "0.1,0.5" }]),
    )
    expect(out.rules[0]).toEqual({
      field: "gnomad_af_global",
      operator: "between",
      value: ["0.1", "0.5"],
    })
  })

  it("trims whitespace around tokens", () => {
    const out = normalizeFilterForApi(
      filterWith([{ field: "cadd_phred", operator: "between", value: " 20 , 30 " }]),
    )
    expect((out.rules[0] as { value: unknown }).value).toEqual(["20", "30"])
  })

  it.each(["in", "notIn"])("turns a comma-joined %s value into a list, dropping empty tokens", (operator) => {
    const out = normalizeFilterForApi(
      filterWith([{ field: "gene_symbol", operator, value: "BRCA1, BRCA2," }]),
    )
    expect((out.rules[0] as { value: unknown }).value).toEqual(["BRCA1", "BRCA2"])
  })

  it.each(["in", "notIn"])("honours react-querybuilder's escaped comma in a %s value", (operator) => {
    const out = normalizeFilterForApi(
      filterWith([
        { field: "clinvar_conditions", operator, value: "Alzheimer disease\\, late onset,Parkinson disease" },
      ]),
    )
    expect((out.rules[0] as { value: unknown }).value).toEqual([
      "Alzheimer disease, late onset",
      "Parkinson disease",
    ])
  })

  it("keeps a half-filled between pair intact so the backend can reject it", () => {
    const out = normalizeFilterForApi(
      filterWith([{ field: "cadd_phred", operator: "between", value: "20," }]),
    )
    expect((out.rules[0] as { value: unknown }).value).toEqual(["20", ""])
  })

  it("keeps a malformed between pair intact so the backend can reject it", () => {
    const out = normalizeFilterForApi(
      filterWith([{ field: "cadd_phred", operator: "between", value: "20" }]),
    )
    expect((out.rules[0] as { value: unknown }).value).toEqual(["20"])
  })

  it("leaves array values and single-value operators untouched", () => {
    const rules: RuleGroupModel["rules"] = [
      { field: "chrom", operator: "in", value: ["17", "7"] },
      { field: "gene_symbol", operator: "=", value: "BRCA1,BRCA2" },
      { field: "gene_symbol", operator: "contains", value: "BRC" },
      { field: "cadd_phred", operator: "null" },
    ]
    const out = normalizeFilterForApi(filterWith(rules))
    expect(out.rules).toEqual(rules)
  })

  it("recurses into nested groups and preserves group metadata", () => {
    const input: RuleGroupModel = {
      combinator: "or",
      not: true,
      rules: [
        {
          combinator: "and",
          not: false,
          rules: [{ field: "pos", operator: "between", value: "100,200" }],
        },
        { field: "chrom", operator: "notIn", value: "X,Y" },
      ],
    }
    const out = normalizeFilterForApi(input)
    expect(out).toEqual({
      combinator: "or",
      not: true,
      rules: [
        { combinator: "and", not: false, rules: [{ field: "pos", operator: "between", value: ["100", "200"] }] },
        { field: "chrom", operator: "notIn", value: ["X", "Y"] },
      ],
    })
  })

  it("does not mutate its input", () => {
    const rule = { field: "pos", operator: "between", value: "100,200" }
    const input = filterWith([rule])
    normalizeFilterForApi(input)
    expect(rule.value).toBe("100,200")
    expect(input.rules[0]).toBe(rule)
  })
})

describe("toEditorFilter", () => {
  it("joins in / notIn arrays into the editor's escaped list string", () => {
    const out = toEditorFilter(
      filterWith([
        { field: "clinvar_conditions", operator: "in", value: ["Alzheimer disease, late onset", "Parkinson disease"] },
        { field: "chrom", operator: "notIn", value: ["X", "Y"] },
      ]),
    )
    expect(out.rules).toEqual([
      { field: "clinvar_conditions", operator: "in", value: "Alzheimer disease\\, late onset,Parkinson disease" },
      { field: "chrom", operator: "notIn", value: "X,Y" },
    ])
  })

  it("leaves between arrays, string values, and single-value operators untouched", () => {
    const rules: RuleGroupModel["rules"] = [
      { field: "gnomad_af_global", operator: "between", value: ["0.1", "0.5"] },
      { field: "chrom", operator: "in", value: "17,7" },
      { field: "gene_symbol", operator: "=", value: ["not", "a-list-op"] },
    ]
    expect(toEditorFilter(filterWith(rules)).rules).toEqual(rules)
  })

  it("round-trips a value containing a literal comma through the editor and back", () => {
    const stored = filterWith([
      {
        combinator: "or",
        not: false,
        rules: [{ field: "clinvar_conditions", operator: "in", value: ["Alzheimer disease, late onset", "Parkinson disease"] }],
      },
    ])
    expect(normalizeFilterForApi(toEditorFilter(stored))).toEqual(stored)
  })
})
