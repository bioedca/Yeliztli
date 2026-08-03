/**
 * Keeps a Query Builder rule tree alive while StaleSampleGate temporarily
 * fences its outlet during a new sample's freshness probe. Results and error
 * state deliberately remain inside QueryBuilderView and are cleared per
 * sample, so no prior sample data is retained or rendered.
 */

import { useCallback, useMemo, useState, type ReactNode } from "react"
import { Outlet } from "react-router-dom"
import {
  createEmptyQuery,
  QueryBuilderDraftContext,
} from "@/components/query-builder/QueryBuilderDraftContext"

export function QueryBuilderDraftProvider({ children }: { children: ReactNode }) {
  const [query, setQuery] = useState(createEmptyQuery)
  const resetQuery = useCallback(() => setQuery(createEmptyQuery()), [])

  const value = useMemo(
    () => ({ query, setQuery, resetQuery }),
    [query, resetQuery],
  )

  return <QueryBuilderDraftContext.Provider value={value}>{children}</QueryBuilderDraftContext.Provider>
}

/** Route-layout adapter that keeps the draft outside StaleSampleRouteGate. */
export function QueryBuilderDraftRouteProvider() {
  return (
    <QueryBuilderDraftProvider>
      <Outlet />
    </QueryBuilderDraftProvider>
  )
}
