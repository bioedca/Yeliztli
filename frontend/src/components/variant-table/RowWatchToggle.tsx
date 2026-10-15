/** Per-row watch toggle for the variant table (#2123).
 *
 * Discoverability follow-up to #1995: watching was reachable only by opening a
 * variant's detail panel and finding the button inside it, so a user browsing
 * the table had no in-place affordance. This renders one in the 36px control
 * column beside the evidence-conflict flag.
 *
 * Deliberately dumb: it owns no query and no mutation. The table calls
 * `useWatchedVariants` / `useWatchVariant` / `useUnwatchVariant` **once** and
 * passes membership and handlers down through table meta, so a paginated table
 * costs one watch request rather than one per row. `WatchButton` remains the
 * detail-panel control; both read the same per-sample list, so they stay in
 * sync through the shared query cache.
 */

import { Eye, EyeOff, Loader2 } from "lucide-react"

import { cn } from "@/lib/utils"

interface RowWatchToggleProps {
  rsid: string
  isWatched: boolean
  /** A watch/unwatch request for *this* row is in flight. */
  isPending?: boolean
  /**
   * Why the control cannot be acted on, or null/undefined when it can. Shown as
   * the tooltip so a disabled control always says why it is disabled.
   */
  unavailableReason?: string | null
  onToggle: (rsid: string) => void
  className?: string
}

export default function RowWatchToggle({
  rsid,
  isWatched,
  isPending = false,
  unavailableReason = null,
  onToggle,
  className,
}: RowWatchToggleProps) {
  const isDisabled = isPending || unavailableReason != null
  // The row itself is clickable and opens the detail panel
  // (VariantTable: onClick={() => setSelectedRsid(row.original.rsid)}), so the
  // toggle must stop the event or watching a variant would also navigate.
  // Keyboard activation of a <button> fires a click event too, so this covers
  // Enter/Space as well as the pointer.
  const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    if (isDisabled) return
    onToggle(rsid)
  }

  // The accessible name carries the rsID, because every row renders this
  // control and a bare "Watch this variant" would give a screen-reader user
  // dozens of identically named buttons with no way to tell them apart.
  //
  // It is deliberately *stable* across states, with the state carried by
  // `aria-pressed`: that is the ARIA toggle-button pattern, and a name that
  // flipped to "Unwatch rs…" would also collide with the WatchingSidebar's
  // own per-variant unwatch control, which already uses exactly that name.
  // Two differently-located buttons with one name is the ambiguity this
  // avoids. `title` still reads as the action for a sighted hover.
  const name = `Watch ${rsid}`
  // A disabled control states its reason rather than offering a bare action
  // the user cannot take.
  const action =
    unavailableReason ?? (isWatched ? `Unwatch ${rsid}` : `Watch ${rsid}`)

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isDisabled}
      aria-label={name}
      aria-pressed={isWatched}
      title={action}
      className={cn(
        "inline-flex h-6 w-6 items-center justify-center rounded transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        isWatched
          ? "text-amber-600 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-950/50"
          : "text-muted-foreground hover:bg-accent hover:text-foreground",
        isDisabled && "cursor-not-allowed opacity-60",
        className,
      )}
    >
      {isPending ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : isWatched ? (
        <EyeOff className="h-3.5 w-3.5" />
      ) : (
        <Eye className="h-3.5 w-3.5" />
      )}
    </button>
  )
}
