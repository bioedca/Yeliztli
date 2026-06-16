/**
 * #613: the pathway-level (Elevated / Moderate / Standard) badge colour must be
 * identical on every surface. Previously the module PathwayCards used
 * Elevated→amber / Moderate→blue while All Findings used Elevated→red /
 * Moderate→amber, so the same amber badge meant different severities. These tests
 * lock the single shared map so that can't drift back.
 */

import { describe, it, expect } from "vitest"
import {
  PATHWAY_LEVEL_COLORS,
  PATHWAY_LEVEL_CONFIG,
  pathwayLevelBadge,
  type PathwayLevel,
} from "@/lib/pathwayLevel"

const LEVELS: PathwayLevel[] = ["Elevated", "Moderate", "Standard"]

describe("pathwayLevel shared colour map (#613)", () => {
  it("the ready-made config badge matches the colours map for every level", () => {
    for (const level of LEVELS) {
      expect(PATHWAY_LEVEL_CONFIG[level].badge).toBe(PATHWAY_LEVEL_COLORS[level].badge)
    }
  })

  it("pathwayLevelBadge() returns the same badge the PathwayCards use", () => {
    // The All Findings page (pathwayLevelBadge) and the module PathwayCards
    // (PATHWAY_LEVEL_CONFIG) must resolve a level to the *same* badge class.
    for (const level of LEVELS) {
      expect(pathwayLevelBadge(level)).toBe(PATHWAY_LEVEL_CONFIG[level].badge)
    }
  })

  it("uses the amber/blue/emerald severity hues (aligned with snpCategory.ts)", () => {
    expect(pathwayLevelBadge("Elevated")).toContain("amber")
    expect(pathwayLevelBadge("Moderate")).toContain("blue")
    expect(pathwayLevelBadge("Standard")).toContain("emerald")
  })

  it("amber never means Moderate and Elevated is never red (the #613 regression)", () => {
    // The exact inversion that made amber ambiguous: All Findings had
    // Moderate→amber and Elevated→red.
    expect(pathwayLevelBadge("Moderate")).not.toContain("amber")
    expect(pathwayLevelBadge("Elevated")).not.toContain("red")
  })

  it("falls back to Standard styling for an unrecognised level", () => {
    expect(pathwayLevelBadge("Nonexistent")).toBe(PATHWAY_LEVEL_COLORS.Standard.badge)
  })
})
