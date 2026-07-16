/** Modal management for the app's slide-in "dialog" detail panels
 *  (a11y - WAI-ARIA dialog pattern / WCAG 2.4.3, #703/#846).
 *
 *  The panels are hand-rolled drawers that already (or now) carry
 *  `role="dialog"` + `aria-modal="true"`; this hook supplies the modal
 *  behavior that none of them had:
 *
 *    - on open, move focus into the panel (its first focusable element, falling
 *      back to the panel container — which must carry `tabIndex={-1}`);
 *    - while open, trap Tab / Shift+Tab inside the panel so a keyboard or
 *      screen-reader user cannot step out into the obscured background page;
 *    - on close, restore focus to the element that was focused when it opened
 *      (typically the row/button that triggered it).
 *    - while open, mark background siblings as `inert` and lock background
 *      scroll containers.
 *
 *  Escape-to-close is wired here — while the panel is active, Escape calls
 *  `onClose`, so every panel that adopts the hook gets modal semantics, the Tab
 *  trap, AND dismissal together and none can drift into advertising
 *  `aria-modal` while ignoring Escape (#1977). The click-backdrop overlay is
 *  still owned by the panels and their parent views.
 *
 *  @param ref     Ref to the panel container (the `role="dialog"` element).
 *  @param onClose Called when the user presses Escape while the panel is active.
 *  @param active  Whether the panel is open. Panels that are conditionally
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

const activeDialogNodes = new Set<HTMLElement>()
const inertedElements = new Map<HTMLElement, string | null>()
const scrollLockedElements = new Map<HTMLElement, string>()

const SCROLL_LOCK_OVERFLOW_VALUES = new Set(["auto", "scroll", "overlay"])

function connectedDialogNodes(): HTMLElement[] {
  for (const node of Array.from(activeDialogNodes)) {
    if (!node.isConnected) {
      activeDialogNodes.delete(node)
    }
  }
  return Array.from(activeDialogNodes)
}

function collectBackgroundInertTargets(dialogNodes: HTMLElement[]): Set<HTMLElement> {
  const targets = new Set<HTMLElement>()
  const containsDialog = (element: HTMLElement) =>
    dialogNodes.some((dialog) => element === dialog || element.contains(dialog))

  for (const dialog of dialogNodes) {
    let current: HTMLElement | null = dialog
    while (current && current !== document.body) {
      const parent: HTMLElement | null = current.parentElement
      if (!parent) break

      for (const child of Array.from(parent.children)) {
        if (!(child instanceof HTMLElement)) continue
        if (child === current) continue
        if (containsDialog(child)) continue
        // Backdrops are owned by the panel's parent and must remain clickable to close.
        if (child.getAttribute("aria-hidden") === "true") continue
        targets.add(child)
      }

      current = parent
    }
  }

  return targets
}

function collectScrollLockTargets(dialogNodes: HTMLElement[]): Set<HTMLElement> {
  const targets = new Set<HTMLElement>([document.body, document.documentElement])
  const mainContent = document.getElementById("main-content")
  if (mainContent instanceof HTMLElement) {
    targets.add(mainContent)
  }
  if (document.scrollingElement instanceof HTMLElement) {
    targets.add(document.scrollingElement)
  }

  for (const dialog of dialogNodes) {
    let current = dialog.parentElement
    while (current && current !== document.body) {
      const style = window.getComputedStyle(current)
      if (
        SCROLL_LOCK_OVERFLOW_VALUES.has(style.overflow) ||
        SCROLL_LOCK_OVERFLOW_VALUES.has(style.overflowY)
      ) {
        targets.add(current)
      }
      current = current.parentElement
    }
  }

  for (const dialog of dialogNodes) {
    targets.delete(dialog)
    for (const target of Array.from(targets)) {
      if (dialog.contains(target)) {
        targets.delete(target)
      }
    }
  }

  return targets
}

function restoreInert(element: HTMLElement): void {
  const previous = inertedElements.get(element)
  if (previous == null) {
    element.removeAttribute("inert")
  } else {
    element.setAttribute("inert", previous)
  }
  inertedElements.delete(element)
}

function restoreScrollLock(element: HTMLElement): void {
  const previous = scrollLockedElements.get(element)
  element.style.overflow = previous ?? ""
  scrollLockedElements.delete(element)
}

function applyPageModalState(): void {
  const dialogNodes = connectedDialogNodes()
  const inertTargets = collectBackgroundInertTargets(dialogNodes)

  for (const element of Array.from(inertedElements.keys())) {
    if (!inertTargets.has(element)) {
      restoreInert(element)
    }
  }
  for (const element of inertTargets) {
    if (!inertedElements.has(element)) {
      inertedElements.set(element, element.getAttribute("inert"))
    }
    element.setAttribute("inert", "")
  }

  const scrollTargets =
    dialogNodes.length > 0 ? collectScrollLockTargets(dialogNodes) : new Set<HTMLElement>()
  for (const element of Array.from(scrollLockedElements.keys())) {
    if (!scrollTargets.has(element)) {
      restoreScrollLock(element)
    }
  }
  for (const element of scrollTargets) {
    if (!scrollLockedElements.has(element)) {
      scrollLockedElements.set(element, element.style.overflow)
    }
    element.style.overflow = "hidden"
  }
}

export function useDialogFocus(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  active = true,
): void {
  // Escape-to-close, centralized so it can never be omitted per-panel (#1977).
  // Kept in its own effect (deps: active, onClose) so a changing onClose
  // identity re-binds only this listener and never re-enters focus below.
  useEffect(() => {
    if (!active) return
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      // When dialogs stack, only the topmost (most recently opened) dismisses,
      // so Escape closes the foreground dialog and leaves the one beneath open
      // (ARIA APG modal pattern). connectedDialogNodes() is insertion-ordered;
      // fall through to close when this node isn't registered yet (single
      // dialog whose focus effect hasn't run) so a lone dialog always closes.
      const stack = connectedDialogNodes()
      const node = ref.current
      if (node && stack.includes(node) && stack[stack.length - 1] !== node) return
      onClose()
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [ref, active, onClose])

  useEffect(() => {
    if (!active) return
    const node = ref.current
    if (!node) return

    const previouslyFocused = document.activeElement as HTMLElement | null
    const focusable = () => Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))

    activeDialogNodes.add(node)
    applyPageModalState()

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
      activeDialogNodes.delete(node)
      applyPageModalState()
      // Restore focus to the trigger after the panel closes.
      const remainingDialogs = connectedDialogNodes()
      if (
        remainingDialogs.length === 0 ||
        remainingDialogs.some((dialog) => previouslyFocused && dialog.contains(previouslyFocused))
      ) {
        previouslyFocused?.focus?.()
      }
    }
  }, [ref, active])
}
