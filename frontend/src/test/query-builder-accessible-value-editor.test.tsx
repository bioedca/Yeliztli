import { describe, it, expect } from "vitest"
import { useState } from "react"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryBuilder, type RuleGroupType } from "react-querybuilder"
import QueryBuilderPanel from "@/components/query-builder/QueryBuilderPanel"
import AccessibleValueEditor from "@/components/query-builder/AccessibleValueEditor"
import type { QueryFieldInfo } from "@/types/query-builder"

// #2378: react-querybuilder's default editor leaves both ``between`` bounds unnamed.

const FIELDS: QueryFieldInfo[] = [
  { name: "gnomad_af_global", type: "number", label: "Gnomad Af Global" },
  { name: "gene_symbol", type: "text", label: "Gene Symbol" },
]

function rule(field: string, operator: string, value: unknown) {
  return { combinator: "and", rules: [{ field, operator, value }] } as RuleGroupType
}

function Harness({ initial, onChange }: { initial: RuleGroupType; onChange?: (q: RuleGroupType) => void }) {
  const [query, setQuery] = useState(initial)
  return (
    <QueryBuilderPanel
      fields={FIELDS}
      query={query}
      onQueryChange={(q) => {
        setQuery(q)
        onChange?.(q)
      }}
    />
  )
}

describe("AccessibleValueEditor (#2378)", () => {
  it("names each bound of a between rule after its field", () => {
    render(<Harness initial={rule("gnomad_af_global", "between", ["0.1", "0.5"])} />)

    const lower = screen.getByRole("spinbutton", { name: "Gnomad Af Global lower bound" })
    const upper = screen.getByRole("spinbutton", { name: "Gnomad Af Global upper bound" })
    expect(lower).toHaveValue(0.1)
    expect(upper).toHaveValue(0.5)
  })

  it("still stores the bounds as a two-element array", async () => {
    const user = userEvent.setup()
    const seen: RuleGroupType[] = []
    render(<Harness initial={rule("gnomad_af_global", "between", ["", ""])} onChange={(q) => seen.push(q)} />)

    await user.type(screen.getByRole("spinbutton", { name: /lower bound/ }), "0.1")
    await user.type(screen.getByRole("spinbutton", { name: /upper bound/ }), "0.5")

    expect(seen.length).toBeGreaterThan(0)
    const last = seen[seen.length - 1].rules[0] as { value: unknown }
    expect(last.value).toEqual(["0.1", "0.5"])
  })

  it("keeps the value-editor test id the e2e specs locate the bounds by", () => {
    const { container } = render(<Harness initial={rule("gnomad_af_global", "between", ["1", "2"])} />)
    const editor = container.querySelector('[data-testid="value-editor"]')
    expect(editor).not.toBeNull()
    expect(editor!.querySelectorAll("input")).toHaveLength(2)
  })

  it("leaves a single-value rule on the default editor, which carries its own name", () => {
    render(<Harness initial={rule("gene_symbol", "=", "BRCA1")} />)
    expect(screen.getByRole("textbox", { name: "Value" })).toHaveValue("BRCA1")
    expect(screen.queryByRole("textbox", { name: /bound/ })).toBeNull()
  })

  it("names both selectors of a between rule on a select field", () => {
    render(
      <QueryBuilder
        fields={[
          {
            name: "tier",
            label: "Tier",
            valueEditorType: "select",
            values: [
              { name: "1", label: "One" },
              { name: "2", label: "Two" },
            ],
            operators: [{ name: "between", label: "between" }],
          },
        ]}
        query={rule("tier", "between", ["1", "2"])}
        controlElements={{ valueEditor: AccessibleValueEditor }}
        listsAsArrays
      />,
    )
    expect(screen.getByTitle("Tier lower bound")).toHaveValue("1")
    expect(screen.getByTitle("Tier upper bound")).toHaveValue("2")
  })
})
