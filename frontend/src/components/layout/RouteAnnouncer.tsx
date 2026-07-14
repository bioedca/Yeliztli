/** Announces route changes to screen readers (P4-26c WCAG 2.1 AA).
 *
 * Uses an aria-live region to notify assistive technology when the
 * page title changes after a client-side navigation.
 */

import { useLocation } from "react-router-dom"
import { navRoutes } from "@/lib/nav-routes"

const routeTitles = new Map<string, string>([
  ...navRoutes.map(
    ({ to, label, announcementLabel }) =>
      [to, announcementLabel ?? label] as const,
  ),
  ["/setup", "Setup Wizard"],
  ["/login", "Login"],
])

/** Resolve a pathname to a human-readable page title for screen reader announcements. */
function getPageTitle(pathname: string): string {
  // Check exact match first, then try parent path for nested routes (e.g. /settings/updates → Settings)
  const exactTitle = routeTitles.get(pathname)
  if (exactTitle) return exactTitle
  const parent = pathname.split("/").slice(0, 2).join("/")
  return routeTitles.get(parent) ?? "Page"
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
