/** Focus management for the app's modal slide-in "dialog" detail panels
 *  (a11y — WAI-ARIA dialog pattern / WCAG 2.4.3, #703).
 *
 *  The panels are hand-rolled drawers that already (or now) carry
 *  `role="dialog"` + `aria-modal="true"`; this hook supplies the focus-order
 *  half of the dialog pattern that none of them had:
 *
 *    - on open, move focus into the panel (its first focusable element, falling
 *      back to the panel container — which must carry `tabIndex={-1}`);
 *    - while open, trap Tab / Shift+Tab inside the panel so a keyboard or
 *      screen-reader user cannot step out into the obscured background page;
 *    - on close, restore focus to the element that was focused when it opened
 *      (typically the row/button that triggered it).
 *
 *  Escape-to-close and the click-backdrop overlay are owned by the panels and
 *  their parent views; this hook deliberately only manages focus.
 *
 *  @param ref    Ref to the panel container (the `role="dialog"` element).
 *  @param active Whether the panel is open. Panels that are conditionally
 *                mounted only while open can omit this (defaults to `true`).
 *                A panel that stays mounted and renders `null` when closed must
 *                pass its open flag so focus is (re)entered on every open.
 */
import { useEffect, type RefObject } from "react"

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",")

export function useDialogFocus(ref: RefObject<HTMLElement | null>, active = true): void {
  useEffect(() => {
    if (!active) return
    const node = ref.current
    if (!node) return

    const previouslyFocused = document.activeElement as HTMLElement | null
    const focusable = () => Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))

    // Move focus into the panel on open.
    const initial = focusable()[0] ?? node
    initial.focus()

    // Trap Tab / Shift+Tab within the panel.
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return
      const items = focusable()
      if (items.length === 0) {
        e.preventDefault()
        node.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const activeEl = document.activeElement
      // Wrap at both ends; treat the container itself (tabIndex=-1, focusable
      // only programmatically / via click) as an edge so Tab from it stays
      // trapped in either direction.
      if (e.shiftKey && (activeEl === first || activeEl === node)) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (activeEl === last || activeEl === node)) {
        e.preventDefault()
        first.focus()
      }
    }

    node.addEventListener("keydown", handleKeyDown)
    return () => {
      node.removeEventListener("keydown", handleKeyDown)
      // Restore focus to the trigger after the panel closes.
      previouslyFocused?.focus?.()
    }
  }, [ref, active])
}
