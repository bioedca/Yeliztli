/** Cross-module "View in X" cards must show the canonical module display name
 * from the shared registry, not an ad-hoc capitalize of the raw module key
 * (#699). Before the fix, a target of `gene_health` rendered "Gene health"
 * (sentence case) and acronym modules like `ebmd`/`lhon` rendered "Ebmd"/"Lhon"
 * — none matching the sidebar/Command Palette. Renders the real CrossModuleCard
 * (via TraitsPersonalityView) so a regression back to inline capitalization is
 * caught, not just the registry in isolation.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import type { CrossModuleItem, PathwaySummary } from "@/types/traits"

const routerMock = vi.hoisted(() => ({ search: "sample_id=1" }))
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(routerMock.search), vi.fn()] as const,
  }
})

const mockPathways = vi.fn()
const mockPRS = vi.fn()
const mockDisclaimer = vi.fn()
const mockPathwayDetail = vi.fn()
vi.mock("@/api/traits", () => ({
  useTraitsPathways: () => mockPathways(),
  useTraitsPRS: () => mockPRS(),
  useTraitsDisclaimer: () => mockDisclaimer(),
  useTraitsPathwayDetail: () => mockPathwayDetail(),
}))

import TraitsPersonalityView from "@/pages/TraitsPersonalityView"

const PATHWAY: PathwaySummary = {
  pathway_id: "caffeine_metabolism",
  pathway_name: "Caffeine Metabolism",
  level: "Moderate",
  evidence_level: 2,
  prs_primary: false,
  called_snps: 2,
  total_snps: 2,
  missing_snps: [],
  pmids: [],
}

function crossModule(to_module: string): CrossModuleItem {
  return {
    rsid: `rs-${to_module}`,
    gene: "GENE1",
    from_trait: "BMI",
    to_module,
    link_type: "shared_snp",
    finding_text: "Shared variant also assessed elsewhere.",
    evidence_level: 2,
    pmids: [],
  }
}

function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPRS.mockReturnValue(q({ data: { items: [] } }))
  mockDisclaimer.mockReturnValue(q({ data: { disclaimer: "Research use only.", evidence_cap: 2 } }))
  mockPathwayDetail.mockReturnValue(q({ data: undefined }))
})

describe("Cross-module 'View in X' labels (#699)", () => {
  it.each([
    ["gene_health", "Gene Health", ["Gene health", "Gene_health"]],
    ["ebmd", "eBMD", ["Ebmd"]],
    ["lhon", "LHON", ["Lhon"]],
    ["apoe", "APOE", ["Apoe"]],
  ])(
    "renders %s as the canonical label %s, never the mis-cased form",
    (key, canonical, misCased) => {
      mockPathways.mockReturnValue(
        q({ data: { items: [PATHWAY], cross_module: [crossModule(key)] } }),
      )
      const { container } = render(<TraitsPersonalityView />)
      const text = container.textContent ?? ""

      // The card (chip "BMI → X" and/or "View in X") must show the canonical
      // display name — and none of the ad-hoc capitalize/underscore renderings.
      // Both checks discriminate the old inline label: gene_health rendered
      // "Gene health" (not "Gene Health"), ebmd "Ebmd" (not "eBMD"), etc.
      expect(text).toContain(canonical)
      for (const wrong of misCased) {
        expect(text).not.toContain(wrong)
      }
    },
  )
})

describe("Cross-module 'View in X' routes resolve via the shared registry (#838)", () => {
  it("page-backed module → Link to the canonical sidebar/router route", () => {
    // nutrigenomics was ABSENT from this view's old hand-duplicated MODULE_ROUTES
    // map, so the pre-#838 code rendered NO link for it. Routing through
    // getModuleMeta(key).route (the sidebar/App.tsx source of truth) now links it.
    mockPathways.mockReturnValue(
      q({ data: { items: [PATHWAY], cross_module: [crossModule("nutrigenomics")] } }),
    )
    render(<TraitsPersonalityView />)
    const link = screen.getByRole("link", { name: /View in Nutrigenomics/i })
    expect(link).toHaveAttribute("href", "/nutrigenomics?sample_id=1")
  })

  it("panel-only module (route null) → non-navigable label, no link", () => {
    // LHON is route: null in MODULE_META (no dedicated page) → the chip shows
    // the label but no "View in" Link is rendered.
    mockPathways.mockReturnValue(
      q({ data: { items: [PATHWAY], cross_module: [crossModule("lhon")] } }),
    )
    const { container } = render(<TraitsPersonalityView />)
    expect(screen.queryByRole("link", { name: /View in LHON/i })).toBeNull()
    // The chip still shows the canonical label (combined with the from_trait,
    // so assert on the container text rather than an exact-text element).
    expect(container.textContent).toContain("LHON")
  })
})
