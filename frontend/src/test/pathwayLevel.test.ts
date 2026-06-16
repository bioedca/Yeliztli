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
  pathwayLevelSvg,
  type PathwayLevel,
} from "@/lib/pathwayLevel"
import { SNP_CATEGORY_COLORS, SNP_CATEGORY_DOT } from "@/lib/snpCategory"

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

  it("keeps per-SNP shared levels on the pathway-level hue source (#864)", () => {
    for (const level of LEVELS) {
      expect(SNP_CATEGORY_COLORS[level]).toBe(PATHWAY_LEVEL_COLORS[level].color)
      expect(SNP_CATEGORY_DOT[level]).toBe(PATHWAY_LEVEL_COLORS[level].dot)
    }

    expect(SNP_CATEGORY_COLORS.Indeterminate).toContain("slate")
    expect(SNP_CATEGORY_DOT.Indeterminate).toContain("slate")
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

describe("pathwayLevel SVG variant — single source for PathwayFlowDiagram (#740)", () => {
  it("exposes an svg fill/stroke/text variant for every level", () => {
    for (const level of LEVELS) {
      const svg = PATHWAY_LEVEL_COLORS[level].svg
      expect(svg.bg).toContain("fill-")
      expect(svg.border).toContain("stroke-")
      expect(svg.text).toContain("fill-")
    }
  })

  it("pathwayLevelSvg() returns the level's svg classes on the same severity hues", () => {
    expect(pathwayLevelSvg("Elevated")).toBe(PATHWAY_LEVEL_COLORS.Elevated.svg)
    expect(pathwayLevelSvg("Elevated").bg).toContain("amber")
    expect(pathwayLevelSvg("Moderate").bg).toContain("blue")
    expect(pathwayLevelSvg("Standard").bg).toContain("emerald")
    // The severity scale must not invert in the SVG either (the #613 class).
    expect(pathwayLevelSvg("Elevated").bg).not.toContain("red")
    expect(pathwayLevelSvg("Moderate").bg).not.toContain("amber")
  })

  it("falls back to Standard svg for an unrecognised level", () => {
    expect(pathwayLevelSvg("Nonexistent")).toBe(PATHWAY_LEVEL_COLORS.Standard.svg)
  })
})
