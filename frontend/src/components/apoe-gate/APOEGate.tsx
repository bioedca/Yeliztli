/** APOE opt-in disclosure gate component (P3-22d).
 *
 * Non-dismissible gate that must be actively acknowledged before APOE
 * findings are shown. Displays the hardcoded gate text from disclaimers.py
 * with resource links. User can accept (show results) or decline (skip).
 *
 * PRD spec: Gate cannot be dismissed. User must actively choose.
 */

import { ShieldAlert } from "lucide-react"
import DisclaimerBody from "@/components/ui/DisclaimerBody"
import type { APOEGateDisclaimerResponse } from "@/types/apoe"

interface APOEGateProps {
  disclaimer: APOEGateDisclaimerResponse
  onAccept: () => void
  onDecline: () => void
  isAcknowledging: boolean
}

export default function APOEGate({
  disclaimer,
  onAccept,
  onDecline,
  isAcknowledging,
}: APOEGateProps) {
  return (
    <div
      className="max-w-2xl mx-auto"
      data-testid="apoe-gate"
    >
      <div className="rounded-lg border-2 border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/40 p-6 shadow-sm">
        {/* Header */}
        <div className="flex items-start gap-4 mb-5">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-amber-100 dark:bg-amber-900/50 text-amber-600 dark:text-amber-400 shrink-0">
            <ShieldAlert className="h-6 w-6" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-amber-900 dark:text-amber-200">
              {disclaimer.title}
            </h2>
            <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
              Please read carefully before proceeding
            </p>
          </div>
        </div>

        {/* Gate text */}
        <div
          id="apoe-gate-text"
          className="text-sm text-amber-800 dark:text-amber-300 leading-relaxed mb-6"
        >
          <DisclaimerBody text={disclaimer.text} />
        </div>

        {/* Action buttons */}
        <div className="flex flex-col sm:flex-row gap-3 pt-4 border-t border-amber-200 dark:border-amber-800">
          <button
            type="button"
            onClick={onAccept}
            disabled={isAcknowledging}
            className="flex-1 inline-flex items-center justify-center rounded-lg px-4 py-2.5 text-sm font-medium bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            data-testid="apoe-gate-accept"
          >
            {isAcknowledging ? "Processing..." : disclaimer.accept_label}
          </button>
          <button
            type="button"
            onClick={onDecline}
            disabled={isAcknowledging}
            className="flex-1 inline-flex items-center justify-center rounded-lg px-4 py-2.5 text-sm font-medium border border-amber-300 dark:border-amber-700 text-amber-700 dark:text-amber-300 hover:bg-amber-100 dark:hover:bg-amber-900/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            data-testid="apoe-gate-decline"
          >
            {disclaimer.decline_label}
          </button>
        </div>
      </div>
    </div>
  )
}
