/** Route-layout adapter for the active sample freshness gate. */

import { Outlet } from "react-router-dom"
import StaleSampleGate from "@/components/layout/StaleSampleGate"

/**
 * Blocks query-scoped analysis routes and the direct concordance-report route
 * until the active sample is fresh, while leaving Settings available to start
 * or monitor re-annotation.
 */
export default function StaleSampleRouteGate() {
  return (
    <StaleSampleGate>
      <Outlet />
    </StaleSampleGate>
  )
}
