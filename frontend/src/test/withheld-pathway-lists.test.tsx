/** Guard-withheld pathway-module lists say so instead of reading as empty (#2519).
 *
 * Each route answers a response the presentability guard refused with an empty
 * one marked `withheld`. Every pathway-module page must then say the results are
 * withheld, never "No … results yet" with advice to run annotation; an ordinary
 * empty response must still read as empty.
 */

import type { ComponentType } from "react"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams("sample_id=1"), vi.fn()] as const,
  }
})

const lists = vi.hoisted(() => ({ pathways: vi.fn(), prs: vi.fn() }))

vi.mock("@/api/fitness", async (orig) => ({
  ...(await orig<typeof import("@/api/fitness")>()),
  useFitnessPathways: () => lists.pathways(),
}))
vi.mock("@/api/gene-health", async (orig) => ({
  ...(await orig<typeof import("@/api/gene-health")>()),
  useGeneHealthPathways: () => lists.pathways(),
}))
vi.mock("@/api/methylation", async (orig) => ({
  ...(await orig<typeof import("@/api/methylation")>()),
  useMethylationPathways: () => lists.pathways(),
}))
vi.mock("@/api/nutrigenomics", async (orig) => ({
  ...(await orig<typeof import("@/api/nutrigenomics")>()),
  useNutrigenomicsPathways: () => lists.pathways(),
}))
vi.mock("@/api/skin", async (orig) => ({
  ...(await orig<typeof import("@/api/skin")>()),
  useSkinPathways: () => lists.pathways(),
}))
vi.mock("@/api/sleep", async (orig) => ({
  ...(await orig<typeof import("@/api/sleep")>()),
  useSleepPathways: () => lists.pathways(),
}))
vi.mock("@/api/traits", async (orig) => ({
  ...(await orig<typeof import("@/api/traits")>()),
  useTraitsPathways: () => lists.pathways(),
  useTraitsPRS: () => lists.prs(),
  useTraitsDisclaimer: () => q({ data: undefined }),
}))

import FitnessView from "@/pages/FitnessView"
import GeneHealthView from "@/pages/GeneHealthView"
import MethylationView from "@/pages/MethylationView"
import NutrigenomicsView from "@/pages/NutrigenomicsView"
import SkinView from "@/pages/SkinView"
import SleepView from "@/pages/SleepView"
import TraitsPersonalityView from "@/pages/TraitsPersonalityView"

function q(over: Record<string, unknown> = {}) {
  return { data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn(), ...over }
}

/** An empty list response carrying every module's extra fields. */
function emptyList(withheld: boolean) {
  return q({
    data: {
      items: [],
      total: 0,
      cross_module: [],
      cross_context: [],
      compound_het: null,
      mc1r_aggregate: null,
      insufficient_data: [],
      metabolizer: null,
      module_disclaimer: "",
      withheld,
    },
  })
}

const PAGES: [string, ComponentType, string, RegExp][] = [
  ["Fitness", FitnessView, "Fitness", /No fitness results yet/],
  ["Gene health", GeneHealthView, "Gene health", /No gene health results yet/],
  ["Methylation", MethylationView, "Methylation", /No methylation results yet/],
  ["Nutrigenomics", NutrigenomicsView, "Nutrigenomics", /No nutrigenomics results yet/],
  ["Skin", SkinView, "Skin", /No skin results yet/],
  ["Sleep", SleepView, "Sleep", /No sleep results yet/],
]

beforeEach(() => {
  // Child components fetch what these mocks don't cover; keep them off the network.
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })))
  vi.clearAllMocks()
  lists.prs.mockReturnValue(q({ data: { items: [], total: 0, module_disclaimer: "" } }))
})

describe("withheld pathway-module lists (#2519)", () => {
  it.each(PAGES)("%s says its results are withheld, not that there are none", (_name, Page, label, empty) => {
    lists.pathways.mockReturnValue(emptyList(true))
    render(<Page />)

    expect(screen.getByText(`${label} results are withheld for this sample.`)).toBeInTheDocument()
    expect(screen.getByText(/This is not the same as having no results/)).toBeInTheDocument()
    expect(screen.queryByText(empty)).not.toBeInTheDocument()
    expect(screen.queryByText(/Run annotation/)).not.toBeInTheDocument()
  })

  it.each(PAGES)("%s still reads an ordinary empty response as empty", (_name, Page, label, empty) => {
    lists.pathways.mockReturnValue(emptyList(false))
    render(<Page />)

    expect(screen.getByText(empty)).toBeInTheDocument()
    expect(screen.queryByText(`${label} results are withheld for this sample.`)).not.toBeInTheDocument()
  })

  it("Traits says its pathways are withheld", () => {
    lists.pathways.mockReturnValue(emptyList(true))
    render(<TraitsPersonalityView />)

    expect(screen.getByText("Trait pathway results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText(/No traits results yet/)).not.toBeInTheDocument()
  })

  it("Traits says its polygenic risk scores are withheld", () => {
    lists.pathways.mockReturnValue(emptyList(false))
    lists.prs.mockReturnValue(q({ data: { items: [], total: 0, module_disclaimer: "", withheld: true } }))
    render(<TraitsPersonalityView />)

    expect(
      screen.getByText("Polygenic risk score results are withheld for this sample."),
    ).toBeInTheDocument()
    expect(screen.queryByText(/No traits results yet/)).not.toBeInTheDocument()
  })

  it("Traits still reads two ordinary empty responses as empty", () => {
    lists.pathways.mockReturnValue(emptyList(false))
    render(<TraitsPersonalityView />)

    expect(screen.getByText(/No traits results yet/)).toBeInTheDocument()
    expect(screen.queryByText(/results are withheld/)).not.toBeInTheDocument()
  })
})
