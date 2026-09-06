/** Announces route changes to screen readers (P4-26c WCAG 2.1 AA).
 *
 * Uses an aria-live region to notify assistive technology when the
 * page title changes after a client-side navigation. The title map itself
 * lives in `@/lib/route-titles` so a guard can sweep it (#2046).
 */

import { useLocation } from "react-router-dom"
import { getPageTitle } from "@/lib/route-titles"

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
