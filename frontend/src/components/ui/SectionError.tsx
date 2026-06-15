/** Localized error for a single page section that failed to load while the rest
 * of the page rendered (issue #642).
 *
 * Distinct from {@link PageError}, which blanks the page and is reserved for a
 * failed PRIMARY/required query. A SECONDARY/optional section (e.g. a
 * "Research Use Only" PRS, a shared reference list) that errors shows this
 * compact inline notice with its own retry, so a user's successfully-computed
 * results stay visible instead of being hidden behind a whole-page error.
 */

import { AlertCircle, RefreshCw } from "lucide-react"

interface SectionErrorProps {
  /** What failed, e.g. "polygenic risk scores" — completes "Couldn't load …". */
  label: string
  /** Optional underlying error detail. */
  message?: string
  /** Callback to retry just this section's query. */
  onRetry?: () => void
}

export default function SectionError({ label, message, onRetry }: SectionErrorProps) {
  return (
    <div
      className="rounded-lg border border-destructive/40 bg-destructive/5 p-4"
      role="alert"
    >
      <div className="flex items-start gap-2">
        <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
        <div className="flex-1 text-sm">
          <p className="font-medium text-destructive">Couldn&rsquo;t load {label}</p>
          {message && <p className="text-muted-foreground mt-0.5">{message}</p>}
          <p className="text-muted-foreground mt-0.5">
            The rest of this page is unaffected.
          </p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-2.5 py-1 text-xs font-medium hover:bg-accent transition-colors"
            >
              <RefreshCw className="h-3 w-3" />
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
