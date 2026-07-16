/** Announces route changes to screen readers (P4-26c WCAG 2.1 AA).
 *
 * Uses an aria-live region to notify assistive technology when the
 * page title changes after a client-side navigation.
 */

import { matchPath, useLocation } from "react-router-dom"
import { navRoutes } from "@/lib/nav-routes"

const primaryRouteTitles = new Map(
  navRoutes.map(
    ({ to, label, announcementLabel }) =>
      [to, announcementLabel ?? label] as const,
  ),
)

function primaryRouteTitle(path: string): string {
  const title = primaryRouteTitles.get(path)
  if (!title) throw new Error(`Missing primary route title for ${path}`)
  return title
}

/**
 * Announcement metadata for every declared route shape. Dynamic detail routes
 * stay outside navRoutes because they are not Sidebar or Command Palette
 * destinations.
 */
const routeTitlePatterns: ReadonlyArray<readonly [string, string]> = [
  ...primaryRouteTitles,
  ["/setup", "Setup Wizard"],
  ["/login", "Login"],
  ["/variants/:rsid", primaryRouteTitle("/variants")],
  ["/settings/*", primaryRouteTitle("/settings")],
  ["/genes/:symbol", "Gene Detail"],
  ["/individuals/:id", "Individual Detail"],
  ["/samples/:id/concordance", "Concordance Report"],
]

/** Resolve a pathname to a human-readable page title for screen reader announcements. */
function getPageTitle(pathname: string): string {
  return (
    routeTitlePatterns.find(([pattern]) => matchPath(pattern, pathname))?.[1] ??
    "Page"
  )
}

export default function RouteAnnouncer() {
  const location = useLocation()
  const title = getPageTitle(location.pathname)

  return (
    <div
      data-testid="route-announcer"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="sr-only"
    >
      {`Navigated to ${title}`}
    </div>
  )
}
