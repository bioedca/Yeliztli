/** A guard-withheld pathway detail says so instead of offering a retry (#2520).
 *
 * The route answers 404 with ``detail.code === "patient_response_withheld"``
 * when the presentability guard refuses the assembled detail. Every panel used
 * to show the hook's generic "… pathway detail failed. Please try again." copy
 * for it, a retry that can never succeed. An ordinary failure keeps that copy.
 */

import type { ComponentType } from "react"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import { ApiError } from "@/api/errors"

const detail = vi.hoisted(() => ({ result: undefined as unknown }))

vi.mock("@/api/allergy", async (orig) => ({
  ...(await orig<typeof import("@/api/allergy")>()),
  useAllergyPathwayDetail: () => detail.result,
}))
vi.mock("@/api/fitness", async (orig) => ({
  ...(await orig<typeof import("@/api/fitness")>()),
  useFitnessPathwayDetail: () => detail.result,
}))
vi.mock("@/api/gene-health", async (orig) => ({
  ...(await orig<typeof import("@/api/gene-health")>()),
  useGeneHealthPathwayDetail: () => detail.result,
}))
vi.mock("@/api/methylation", async (orig) => ({
  ...(await orig<typeof import("@/api/methylation")>()),
  useMethylationPathwayDetail: () => detail.result,
}))
vi.mock("@/api/nutrigenomics", async (orig) => ({
  ...(await orig<typeof import("@/api/nutrigenomics")>()),
  useNutrigenomicsPathwayDetail: () => detail.result,
}))
vi.mock("@/api/skin", async (orig) => ({
  ...(await orig<typeof import("@/api/skin")>()),
  useSkinPathwayDetail: () => detail.result,
}))
vi.mock("@/api/sleep", async (orig) => ({
  ...(await orig<typeof import("@/api/sleep")>()),
  useSleepPathwayDetail: () => detail.result,
}))
vi.mock("@/api/traits", async (orig) => ({
  ...(await orig<typeof import("@/api/traits")>()),
  useTraitsPathwayDetail: () => detail.result,
}))

import AllergyPanel from "@/components/allergy/PathwayDetailPanel"
import FitnessPanel from "@/components/fitness/PathwayDetailPanel"
import GeneHealthPanel from "@/components/gene-health/PathwayDetailPanel"
import MethylationPanel from "@/components/methylation/PathwayDetailPanel"
import NutrigenomicsPanel from "@/components/nutrigenomics/PathwayDetailPanel"
import SkinPanel from "@/components/skin/PathwayDetailPanel"
import SleepPanel from "@/components/sleep/PathwayDetailPanel"
import TraitsPanel from "@/components/traits/PathwayDetailPanel"

type PanelProps = { pathwayId: string; pathwayName: string; sampleId: number; onClose: () => void }

const PANELS: [string, ComponentType<PanelProps>][] = [
  ["Allergy", AllergyPanel],
  ["Fitness", FitnessPanel],
  ["Gene Health", GeneHealthPanel],
  ["Methylation", MethylationPanel],
  ["Nutrigenomics", NutrigenomicsPanel],
  ["Skin", SkinPanel],
  ["Sleep", SleepPanel],
  ["Traits", TraitsPanel],
]

const RETRY = "Pathway detail failed. Please try again."

function failed(error: Error) {
  return { data: undefined, isLoading: false, isError: true, error, refetch: vi.fn() }
}

function renderPanel(Panel: ComponentType<PanelProps>) {
  render(<Panel pathwayId="example" pathwayName="Example pathway" sampleId={1} onClose={vi.fn()} />)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })))
})

describe("withheld pathway detail (#2520)", () => {
  it.each(PANELS)("%s says the pathway's details are withheld, with no retry", (_name, Panel) => {
    detail.result = failed(
      new ApiError(404, RETRY, {
        detail: { code: "patient_response_withheld", message: "server text, never shown" },
      }),
    )
    renderPanel(Panel)

    expect(screen.getByText("This pathway's results are withheld for this sample.")).toBeInTheDocument()
    expect(screen.queryByText(RETRY)).not.toBeInTheDocument()
    expect(screen.queryByText("server text, never shown")).not.toBeInTheDocument()
  })

  it.each(PANELS)("%s still shows an ordinary failure as a failure", (_name, Panel) => {
    detail.result = failed(new ApiError(500, RETRY, { detail: "Internal Server Error" }))
    renderPanel(Panel)

    expect(screen.getByText(RETRY)).toBeInTheDocument()
    expect(screen.queryByText(/results are withheld/)).not.toBeInTheDocument()
  })

  it.each(PANELS)("%s keeps a plain not-found as a failure, not withheld", (_name, Panel) => {
    detail.result = failed(new ApiError(404, RETRY, { detail: "Pathway 'example' not found for sample." }))
    renderPanel(Panel)

    expect(screen.getByText(RETRY)).toBeInTheDocument()
    expect(screen.queryByText(/results are withheld/)).not.toBeInTheDocument()
  })
})
