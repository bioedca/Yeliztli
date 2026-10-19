/** Empty state for results the server withheld rather than found empty (#2297, #2519).
 *
 * A route whose assembled response the patient-presentability guard refuses
 * answers with an empty response marked `withheld`. It must not read as "no
 * results" -- and above all not as advice to run annotation, which cannot help.
 * It says only that results were withheld, never what was held.
 */

import { AlertTriangle } from "lucide-react"
import PageEmpty from "@/components/ui/PageEmpty"

export default function WithheldResults({ label }: { label: string }) {
  return (
    <PageEmpty
      icon={AlertTriangle}
      title={`${label} results are withheld for this sample.`}
      description="Part of these results could not be shown safely, so none are shown here. This is not the same as having no results."
    />
  )
}
