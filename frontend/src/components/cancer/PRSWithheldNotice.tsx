/** Names each sex-specific cancer PRS model the biological-sex gate keeps off
 * this sample, and why (#2498).
 *
 * Without it a withheld prostate (or breast) score simply vanishes from the
 * page, indistinguishable from a model that was never installed. The reasons
 * read differently on purpose: a confident other-sex result means the model
 * does not apply, an ambiguous or undetermined sex means a score that might
 * apply is being withheld, and a sex recorded after the analysis ran means the
 * score simply has not been computed yet (#2509). A bundled model that is not
 * scored for anyone is named here too, so it does not vanish either (#2504).
 */

import { Info } from "lucide-react"
import type { CancerPRSUnavailable, CancerPRSWithheld } from "@/types/cancer"

/** How a resolved sex context reads in a sentence. */
function sexState(sex: string | null): string {
  if (sex === "XX" || sex === "XY") return sex
  return sex === "manual_review" ? "ambiguous" : "undetermined"
}

function withheldReason(item: CancerPRSWithheld): string {
  if (item.reason === "sex_mismatch") {
    return `it is computed only for ${item.required_sex} samples, and this sample's biological sex is ${item.resolved_sex}.`
  }
  if (item.reason === "sex_changed_since_scoring") {
    return (
      `it has not been computed yet. The analysis ran while this sample's biological sex was ` +
      `${sexState(item.scored_sex)}, and it is now ${item.resolved_sex}. Re-running the analysis ` +
      `computes it.`
    )
  }
  const state = sexState(item.resolved_sex)
  return (
    `it is computed only for ${item.required_sex} samples, and this sample's biological sex ` +
    `is ${state}, so the score is withheld. Recording the individual's biological sex and ` +
    `re-running the analysis resolves this.`
  )
}

function unavailableReason(item: CancerPRSUnavailable): string {
  if (item.reason === "runtime_blocked") {
    return "this model is not currently scored or reported. Its source is verified, but it cannot yet be scored safely."
  }
  return "this model is not currently scored or reported."
}

export default function PRSWithheldNotice({
  withheld = [],
  unavailable = [],
}: {
  /** Optional so a response without the field degrades to no notice, never a crash. */
  withheld?: CancerPRSWithheld[]
  unavailable?: CancerPRSUnavailable[]
}) {
  const lines = [
    ...withheld.map((item) => ({ key: item.trait, name: item.name, text: withheldReason(item) })),
    ...unavailable.map((item) => ({ key: item.trait, name: item.name, text: unavailableReason(item) })),
  ]
  if (lines.length === 0) return null
  return (
    <div
      role="note"
      aria-label="Polygenic risk scores not shown for this sample"
      data-testid="prs-withheld-notice"
      className="mb-3 rounded-md bg-sky-50 p-2 dark:bg-sky-950/30"
    >
      <ul className="space-y-1">
        {lines.map((item) => (
          <li key={item.key} className="flex gap-1.5">
            <Info
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-700 dark:text-sky-300"
              aria-hidden="true"
            />
            <p className="text-xs text-sky-800 dark:text-sky-200">
              <span className="font-medium">{item.name}</span> is not shown: {item.text}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}
