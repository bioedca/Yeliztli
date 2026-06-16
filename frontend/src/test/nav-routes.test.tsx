/**
 * @vitest-environment happy-dom
 *
 * Drift-guard for the shared navigation registry (#638).
 *
 * The Sidebar and the Command Palette's "Pages" quick-jump used to be two
 * hand-maintained arrays that drifted: the palette was missing 5 routes the
 * sidebar had (/findings, /metabolic, /fh, /ebmd, /rare-variants), so ⌘K
 * couldn't reach them. Both now render from `@/lib/nav-routes`; these tests lock
 * that contract so a future edit can't silently re-introduce the drift.
 */
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@/test/test-utils"
import Sidebar from "@/components/layout/Sidebar"
import CommandPalette from "@/components/CommandPalette"
import { navRoutes } from "@/lib/nav-routes"

// The palette's page list is what this guard checks; its variant/sample groups
// are irrelevant here, so stub their data hooks to keep only the Pages group.
vi.mock("@/api/variants", () => ({
  useVariantSearch: () => ({ data: undefined }),
}))
vi.mock("@/api/samples", () => ({
  useSamples: () => ({ data: undefined }),
}))

// Routes that were sidebar-only before #638 — explicit regression anchors.
const PREVIOUSLY_PALETTE_MISSING = [
  "/findings",
  "/metabolic",
  "/fh",
  "/ebmd",
  "/rare-variants",
]

describe("nav route registry (#638)", () => {
  it("is well-formed: unique absolute paths, non-empty labels", () => {
    const paths = navRoutes.map((r) => r.to)
    expect(new Set(paths).size).toBe(paths.length) // no duplicate routes
    for (const route of navRoutes) {
      expect(route.to).toMatch(/^\//)
      expect(route.label.length).toBeGreaterThan(0)
      expect(route.icon).toBeTruthy()
    }
  })

  it("includes the 5 routes the Command Palette previously dropped", () => {
    const paths = new Set(navRoutes.map((r) => r.to))
    for (const route of PREVIOUSLY_PALETTE_MISSING) {
      expect(paths.has(route)).toBe(true)
    }
  })

  it("Sidebar renders a link for every registry route", () => {
    render(<Sidebar />)
    for (const route of navRoutes) {
      const link = screen.getByRole("link", { name: route.label })
      expect(link).toHaveAttribute("href", route.to)
    }
  })

  it("Command Palette renders a Pages entry for every registry route (⊇ sidebar)", () => {
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)
    for (const route of navRoutes) {
      expect(screen.getByText(route.label)).toBeInTheDocument()
    }
  })

  it("Command Palette now reaches the 5 routes it previously dropped (#638)", () => {
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)
    const labels = navRoutes
      .filter((r) => PREVIOUSLY_PALETTE_MISSING.includes(r.to))
      .map((r) => r.label)
    expect(labels).toHaveLength(PREVIOUSLY_PALETTE_MISSING.length)
    for (const label of labels) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })
})
