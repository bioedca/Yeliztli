/**
 * @vitest-environment happy-dom
 */
import { describe, expect, it } from "vitest"
import RouteAnnouncer from "@/components/layout/RouteAnnouncer"
import { routeTitlePatterns } from "@/lib/route-titles"
import { navRoutes } from "@/lib/nav-routes"
import { render, screen } from "@/test/test-utils"

describe("RouteAnnouncer", () => {
  it("exposes an atomic polite status region", () => {
    render(<RouteAnnouncer />)

    const announcer = screen.getByTestId("route-announcer")
    expect(announcer).toHaveAttribute("role", "status")
    expect(announcer).toHaveAttribute("aria-live", "polite")
    expect(announcer).toHaveAttribute("aria-atomic", "true")
  })

  it.each(navRoutes)("announces the shared title for $to", (route) => {
    render(<RouteAnnouncer />, { route: route.to })

    expect(screen.getByTestId("route-announcer")).toHaveTextContent(
      `Navigated to ${route.announcementLabel ?? route.label}`,
    )
  })

  it.each([
    ["/setup", "Setup Wizard"],
    ["/login", "Login"],
    ["/settings/updates", "Settings"],
    ["/variants/rs123", "Variant Detail"],
    ["/genes/BRCA1?sample_id=1", "Gene Detail"],
    ["/individuals/42", "Individual Detail"],
    ["/samples/42/concordance", "Concordance Report"],
    ["/not-a-real-route", "Page"],
  ])("announces %s as %s", (route, title) => {
    render(<RouteAnnouncer />, { route })

    expect(screen.getByTestId("route-announcer")).toHaveTextContent(
      `Navigated to ${title}`,
    )
  })


  // ── the class this file has regressed on twice (#1781, #1961, #2046) ──────
  //
  // A `:param` detail route is its own destination; borrowing the list page's
  // title announces the same words twice and tells a screen reader user nothing
  // changed. A `/*` sub-section route legitimately shares its parent's title.
  // Sweeping the exported map means a NEW detail route is covered the moment it
  // lands, rather than waiting for someone to remember to add an assertion --
  // which is exactly how #1961 shipped this gap while fixing its siblings.

  const listTitles = new Set(
    routeTitlePatterns
      .filter(([pattern]) => !pattern.includes(":") && !pattern.includes("*"))
      .map(([, title]) => title),
  )
  const detailPatterns = routeTitlePatterns.filter(([pattern]) =>
    pattern.includes(":"),
  )

  it("sweeps a non-trivial number of detail routes", () => {
    // Anti-vacuity: a guard over an empty list would pass while proving nothing.
    expect(detailPatterns.length).toBeGreaterThanOrEqual(4)
    expect(listTitles.size).toBeGreaterThanOrEqual(10)
  })

  it.each(detailPatterns)(
    "detail route %s does not borrow a list page title",
    (_pattern, title) => {
      expect(listTitles.has(title)).toBe(false)
    },
  )

  it("gives every detail route a title of its own", () => {
    const titles = detailPatterns.map(([, title]) => title)
    expect(new Set(titles).size).toBe(titles.length)
  })

  it.each([
    "/genes",
    "/genes/BRCA1/extra",
    "/individuals/42/edit",
    "/samples/42",
    "/samples/42/concordance/extra",
    "/variants/rs123/extra",
    "/cancer/extra",
  ])("does not overmatch undeclared route %s", (route) => {
    render(<RouteAnnouncer />, { route })

    expect(screen.getByTestId("route-announcer")).toHaveTextContent(
      "Navigated to Page",
    )
  })
})
