import { expect, it } from "vitest"
import userEvent from "@testing-library/user-event"
import { render, screen, within } from "./test-utils"
import HpoTermList from "@/components/HpoTermList"

it("renders HPO labels first while retaining accessions and legacy fallback", () => {
  render(
    <HpoTermList
      termIds={["HP:0003002", "HP:0003003"]}
      termDetails={[{ id: "HP:0003002", name: "Breast carcinoma" }]}
    />,
  )

  expect(screen.getByText("Breast carcinoma (HP:0003002)")).toBeInTheDocument()
  expect(screen.getByText("HP:0003003")).toBeInTheDocument()
})

it("bounds long HPO lists behind an accessible disclosure", async () => {
  const user = userEvent.setup()
  const termDetails = Array.from({ length: 10 }, (_, index) => ({
    id: `HP:${String(index + 1).padStart(7, "0")}`,
    name: `Phenotype ${index + 1}`,
  }))

  render(
    <HpoTermList
      termIds={termDetails.map((term) => term.id)}
      termDetails={termDetails}
    />,
  )

  const list = screen.getByRole("list", { name: "Human Phenotype Ontology terms" })
  expect(within(list).getAllByRole("listitem")).toHaveLength(8)
  expect(screen.queryByText("Phenotype 9 (HP:0000009)")).not.toBeInTheDocument()

  const showMore = screen.getByRole("button", { name: "Show 2 more HPO terms" })
  expect(showMore).toHaveAttribute("aria-expanded", "false")
  await user.click(showMore)

  expect(within(list).getAllByRole("listitem")).toHaveLength(10)
  expect(screen.getByText("Phenotype 9 (HP:0000009)")).toBeInTheDocument()
  const showFewer = screen.getByRole("button", { name: "Show fewer HPO terms" })
  expect(showFewer).toHaveAttribute("aria-expanded", "true")

  await user.click(showFewer)
  expect(within(list).getAllByRole("listitem")).toHaveLength(8)
})
