/** Empty state for a route-only result that has not been computed yet (#2307).
 *
 * Metabolic, FH and eBMD score a sample on demand, so a sample nobody has opened
 * them for has no stored results. Its empty section must not give a reason that
 * only fits a scored sample ("the score bundle may not be installed"): the score
 * may well be installed, and the result arrives when scoring finishes.
 */

import { Clock } from "lucide-react"
import PageEmpty from "@/components/ui/PageEmpty"

export default function NotYetScored({ label }: { label: string }) {
  return (
    <PageEmpty
      icon={Clock}
      title={`${label} results have not been computed for this sample yet.`}
      description="They appear here once this sample has been scored. This is not the same as having no results."
    />
  )
}
