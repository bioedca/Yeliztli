/**
 * @vitest-environment happy-dom
 */
import { describe, expect, it } from "vitest"
import RouteAnnouncer from "@/components/layout/RouteAnnouncer"
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
    ["/variants/rs123", "Variant Explorer"],
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
