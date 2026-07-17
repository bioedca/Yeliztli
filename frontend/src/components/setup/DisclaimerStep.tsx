/** Step 1: Global disclaimer — blocks progression until acknowledged. */

import { useState } from 'react'
import { useDisclaimer, useAcceptDisclaimer } from '@/api/setup'
import DisclaimerBody from '@/components/ui/DisclaimerBody'
import { cn } from '@/lib/utils'
import { AlertTriangle, CheckCircle2, ScrollText } from 'lucide-react'

interface DisclaimerStepProps {
  onAccepted: () => void
}

export default function DisclaimerStep({ onAccepted }: DisclaimerStepProps) {
  const { data: disclaimer, isLoading, error } = useDisclaimer()
  const acceptMutation = useAcceptDisclaimer()
  const [hasScrolledToBottom, setHasScrolledToBottom] = useState(false)
  const [isChecked, setIsChecked] = useState(false)

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20
    if (atBottom) setHasScrolledToBottom(true)
  }

  async function handleAccept() {
    try {
      await acceptMutation.mutateAsync()
      onAccepted()
    } catch {
      // Error state handled by acceptMutation.isError
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    )
  }

  if (error || !disclaimer) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-destructive" />
        <p className="mt-3 text-sm text-destructive">
          Failed to load disclaimer. Please restart the application.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center space-y-2">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
          <ScrollText className="h-7 w-7 text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground">
          {disclaimer.title}
        </h2>
        <p className="text-sm text-muted-foreground">
          Please read the following carefully before continuing.
        </p>
      </div>

      {/* Disclaimer text in scrollable container */}
      <div
        onScroll={handleScroll}
        role="region"
        aria-label="Disclaimer text"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard-accessible (axe: scrollable-region-focusable)
        tabIndex={0}
        className={cn(
          'max-h-80 overflow-y-auto rounded-lg border bg-card p-5 text-sm leading-relaxed text-foreground/90',
          'scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent',
        )}
      >
        <DisclaimerBody
          text={disclaimer.text}
          className="[&>p:first-child]:font-medium"
        />
      </div>

      {/* Scroll hint */}
      {!hasScrolledToBottom && (
        <p className="text-center text-xs text-muted-foreground animate-pulse">
          Scroll down to read the full disclaimer
        </p>
      )}

      {/* Acknowledgment checkbox */}
      <label
        className={cn(
          'flex items-start gap-3 rounded-lg border p-4 cursor-pointer transition-colors',
          hasScrolledToBottom
            ? 'hover:bg-accent/50 border-border'
            : 'opacity-50 cursor-not-allowed border-border/50',
        )}
      >
        <input
          type="checkbox"
          checked={isChecked}
          disabled={!hasScrolledToBottom}
          onChange={(e) => setIsChecked(e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-input accent-primary"
          aria-label="I have read and understand the disclaimer"
        />
        <span className="text-sm text-foreground">
          I have read and understand the information above. I acknowledge that
          Yeliztli is for educational and research purposes only and is not
          a substitute for professional medical advice.
        </span>
      </label>

      {/* Accept button */}
      <button
        type="button"
        onClick={handleAccept}
        disabled={!isChecked || acceptMutation.isPending}
        className={cn(
          'w-full rounded-lg px-6 py-3 text-sm font-medium transition-all',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          isChecked
            ? 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm'
            : 'bg-muted text-muted-foreground cursor-not-allowed',
        )}
        aria-label={disclaimer.accept_label}
      >
        {acceptMutation.isPending ? (
          <span className="flex items-center justify-center gap-2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
            Processing...
          </span>
        ) : (
          <span className="flex items-center justify-center gap-2">
            <CheckCircle2 className="h-4 w-4" />
            {disclaimer.accept_label}
          </span>
        )}
      </button>

      {acceptMutation.isError && (
        <p className="text-center text-sm text-destructive">
          Failed to save acceptance. Please try again.
        </p>
      )}
    </div>
  )
}
