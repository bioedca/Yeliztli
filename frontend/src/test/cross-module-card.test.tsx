/** Focused unit tests for the shared CrossModuleCard (#1621).
 *
 * The card was extracted from 5 near-identical per-view definitions. These
 * exercise the component in isolation for the two registry-driven behaviors the
 * issue calls out — a page-backed target (route → "View in X" Link) and a
 * panel-only target (route null → canonical label, no link) — plus that the
 * caller-supplied sourceLabel and the finding fields render.
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "./test-utils"
import CrossModuleCard from "@/components/CrossModuleCard"

const ITEM = {
  gene: "GENE1",
  rsid: "rs123",
  finding_text: "Shared variant also assessed elsewhere.",
  evidence_level: 2,
}

describe("CrossModuleCard (#1621)", () => {
  it("page-backed target → 'View in {label}' Link to the registry route", () => {
    render(
      <CrossModuleCard item={ITEM} sourceLabel="Sleep" targetModule="nutrigenomics" sampleId={1} />,
    )
    const link = screen.getByRole("link", { name: /View in Nutrigenomics/i })
    expect(link).toHaveAttribute("href", "/nutrigenomics?sample_id=1")
  })

  it("panel-only target (route null) → canonical label, no link", () => {
    const { container } = render(
      <CrossModuleCard item={ITEM} sourceLabel="Traits" targetModule="lhon" sampleId={1} />,
    )
    expect(screen.queryByRole("link", { name: /View in LHON/i })).toBeNull()
    expect(container.textContent).toContain("LHON")
  })

  it("renders the caller's sourceLabel, the canonical target label, and the finding fields", () => {
    const { container } = render(
      <CrossModuleCard item={ITEM} sourceLabel="Gene Health" targetModule="apoe" sampleId={1} />,
    )
    const text = container.textContent ?? ""
    expect(text).toContain("Gene Health") // caller's source copy
    expect(text).toContain("APOE") // canonical target label from the registry
    expect(text).toContain("GENE1")
    expect(text).toContain("(rs123)")
    expect(text).toContain("Shared variant also assessed elsewhere.")
  })
})
