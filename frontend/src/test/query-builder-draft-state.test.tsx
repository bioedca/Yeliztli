import { describe, expect, it } from "vitest"
import { screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import { render as rtlRender } from "@testing-library/react"
import { useContext } from "react"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import type { RuleGroupType } from "react-querybuilder"
import { QueryBuilderDraftContext } from "@/components/query-builder/QueryBuilderDraftContext"
import { QueryBuilderDraftRouteProvider } from "@/components/query-builder/QueryBuilderDraftState"

const draftWithRule: RuleGroupType = {
  combinator: "and",
  rules: [{ field: "gene_symbol", operator: "=", value: "BRCA1" }],
}

function DraftProbe() {
  const draft = useContext(QueryBuilderDraftContext)
  if (!draft) throw new Error("Draft probe requires QueryBuilderDraftRouteProvider")

  return (
    <>
      <output data-testid="draft-rule-count">{draft.query.rules.length}</output>
      <button type="button" onClick={() => draft.setQuery(draftWithRule)}>
        Add draft rule
      </button>
      <Link to="/query-builder?sample_id=2">Switch sample</Link>
      <Link to="/elsewhere">Leave query builder</Link>
    </>
  )
}

function Elsewhere() {
  return <Link to="/query-builder?sample_id=2">Return to query builder</Link>
}

describe("QueryBuilderDraftRouteProvider", () => {
  it("retains a draft for a sample switch but resets it after leaving the route", async () => {
    const user = userEvent.setup()

    rtlRender(
      <MemoryRouter initialEntries={["/query-builder?sample_id=1"]}>
        <Routes>
          <Route path="/query-builder" element={<QueryBuilderDraftRouteProvider />}>
            <Route index element={<DraftProbe />} />
          </Route>
          <Route path="/elsewhere" element={<Elsewhere />} />
        </Routes>
      </MemoryRouter>,
    )

    const ruleCount = screen.getByTestId("draft-rule-count")
    expect(ruleCount).toHaveTextContent("0")

    await user.click(screen.getByRole("button", { name: "Add draft rule" }))
    expect(ruleCount).toHaveTextContent("1")

    await user.click(screen.getByRole("link", { name: "Switch sample" }))
    expect(screen.getByTestId("draft-rule-count")).toHaveTextContent("1")

    await user.click(screen.getByRole("link", { name: "Leave query builder" }))
    await user.click(screen.getByRole("link", { name: "Return to query builder" }))
    expect(screen.getByTestId("draft-rule-count")).toHaveTextContent("0")
  })
})
