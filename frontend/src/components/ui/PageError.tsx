/** Shared error state; missing resources and withheld results cannot be retried. */

import { AlertCircle, RefreshCw } from "lucide-react"
import {
  isPatientResponseWithheldError,
  isResourceNotFoundError,
  resourceErrorMessage,
} from "@/api/errors"
import WithheldResults from "@/components/ui/WithheldResults"

interface PageErrorProps {
  /** Preserve the typed API error so status and withheld codes reach the UI. */
  error?: unknown
  /** Error message to display. */
  message?: string
  /** Callback to retry the failed operation. */
  onRetry?: () => void
}

export default function PageError({
  error,
  message = "An unexpected error occurred.",
  onRetry,
}: PageErrorProps) {
  if (isPatientResponseWithheldError(error)) return <WithheldResults label="These" />
  const notFound = isResourceNotFoundError(error)
  return (
    <div
      className="rounded-lg border border-destructive/50 bg-destructive/5 p-6"
      role="alert"
    >
      <div className="flex items-start gap-3">
        <AlertCircle className="h-5 w-5 text-destructive-text mt-0.5 shrink-0" />
        <div className="flex-1">
          <p className="font-medium text-foreground">
            {notFound ? "Data not found" : "Failed to load data"}
          </p>
          <p className="text-sm text-muted-foreground mt-1">
            {resourceErrorMessage(error, message)}
          </p>
          {onRetry && !notFound && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 py-1.5 text-sm font-medium hover:bg-accent transition-colors"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
