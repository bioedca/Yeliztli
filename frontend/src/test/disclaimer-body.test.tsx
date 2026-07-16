import { describe, expect, it } from "vitest"
import { render, screen } from "./test-utils"
import DisclaimerBody from "@/components/ui/DisclaimerBody"

const DISCLAIMER = [
  "Introductory disclaimer copy.",
  "**Please understand the following before reviewing:**",
  "1. **Predisposition is not diagnosis.** Supporting explanation.",
  "2. *Relative* risk is not absolute risk.",
  "**Resources:**\n- National Cancer Institute: https://www.cancer.gov/about-cancer/genetics\n- Genetic counselor finder: https://example.org/find/",
].join("\n\n")

describe("DisclaimerBody", () => {
  it("renders disclaimer Markdown as semantic text, lists, and safe resource links", () => {
    const { container } = render(<DisclaimerBody text={DISCLAIMER} />)

    expect(container).not.toHaveTextContent("**")
    expect(screen.getByText("Please understand the following before reviewing:").tagName).toBe(
      "STRONG",
    )
    expect(screen.getByText("Predisposition is not diagnosis.").tagName).toBe("STRONG")
    expect(screen.getByText("Relative").tagName).toBe("EM")

    const lists = screen.getAllByRole("list")
    expect(lists).toHaveLength(2)
    expect(lists[0].tagName).toBe("OL")
    expect(lists[1].tagName).toBe("UL")

    const nciLink = screen.getByRole("link", {
      name: "https://www.cancer.gov/about-cancer/genetics",
    })
    expect(nciLink).toHaveAttribute("href", "https://www.cancer.gov/about-cancer/genetics")
    expect(nciLink).toHaveAttribute("target", "_blank")
    expect(nciLink).toHaveAttribute("rel", "noopener noreferrer")
  })

  it("does not create elements from raw HTML or unsafe link protocols", () => {
    const { container } = render(
      <DisclaimerBody
        text={'Safe text <script>alert("xss")</script> <img src=x onerror=alert(1)> [Unsafe](javascript:alert(1))'}
      />,
    )

    expect(container.querySelector("script")).toBeNull()
    expect(container.querySelector("img")).toBeNull()
    expect(screen.queryByRole("link", { name: "Unsafe" })).not.toBeInTheDocument()
    expect(screen.getByText(/Safe text/)).toBeInTheDocument()
  })
})
