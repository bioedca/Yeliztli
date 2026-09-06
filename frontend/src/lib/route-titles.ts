/** Screen-reader announcement titles for every declared route shape.
 *
 * Lives outside the component so the map can be swept by a guard: a `:param`
 * detail route is its own destination and must not borrow the list page's
 * title. Announcing "Variant Explorer" on `/variants/:rsid` gave screen reader
 * users the same words twice and no signal the route had changed (#2046) --
 * the leftover gap from #1961, which gave the sibling detail routes their own
 * titles. A `/*` sub-section route legitimately shares its parent's title:
 * Settings sub-pages ARE Settings.
 */

import { matchPath } from "react-router-dom"
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
export const routeTitlePatterns: ReadonlyArray<readonly [string, string]> = [
  ...primaryRouteTitles,
  ["/setup", "Setup Wizard"],
  ["/login", "Login"],
  ["/variants/:rsid", "Variant Detail"],
  ["/settings/*", primaryRouteTitle("/settings")],
  ["/genes/:symbol", "Gene Detail"],
  ["/individuals/:id", "Individual Detail"],
  ["/samples/:id/concordance", "Concordance Report"],
]

/** Resolve a pathname to a human-readable page title for screen reader announcements. */
export function getPageTitle(pathname: string): string {
  return (
    routeTitlePatterns.find(([pattern]) => matchPath(pattern, pathname))?.[1] ??
    "Page"
  )
}
