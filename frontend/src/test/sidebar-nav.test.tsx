/** Tests for Sidebar navigation entries.
 *
 * Regression guard for #558: the Annotation Overlays page (/overlays) is a fully
 * built route but had no sidebar entry, so it was unreachable from the UI.
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "./test-utils"
import Sidebar from "@/components/layout/Sidebar"

describe("Sidebar navigation", () => {
  it("exposes an Annotation Overlays link to /overlays (#558)", () => {
    render(<Sidebar />)
    const link = screen.getByRole("link", { name: "Annotation Overlays" })
    expect(link).toHaveAttribute("href", "/overlays")
  })

  it("links every nav item to a non-empty route", () => {
    render(<Sidebar />)
    const links = screen.getAllByRole("link")
    expect(links.length).toBeGreaterThan(1)
    for (const link of links) {
      expect(link.getAttribute("href")).toMatch(/^\//)
    }
  })
})
