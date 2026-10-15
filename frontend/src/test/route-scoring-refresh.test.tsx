/**
 * Route-only scoring with the real query and mutation hooks (#2118).
 *
 * The view tests mock the hooks; these exercise the cache: a revisit must
 * re-check a cached "current" state, and switching sample on the same page must
 * not carry one sample's failed run onto another.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import EBMDView from "@/pages/EBMDView"
import MetabolicView from "@/pages/MetabolicView"
import type { ScoringState } from "@/types/route-scoring"

const states = new Map<string, ScoringState>()
const getOutcome = new Map<string, number>()
let anchorGenotype = "CT"
// The scoring generation each Metabolic response reports, per request.
let prsGeneration = (): string | null => "g1"
let anchorsGeneration = (): string | null => "g1"
const runOutcome = new Map<string, number>()
const runCalls: string[] = []
// Called after each run; by default a successful run leaves its score current.
const markCurrentOnSuccess = (sample: string) => {
  if ((runOutcome.get(sample) ?? 200) === 200) states.set(sample, "current")
}
let afterRun: (sample: string) => void = markCurrentOnSuccess

function ebmdBody(scoring_state: ScoringState | undefined) {
  return {
    available: false,
    recommended_pgs_id: "PGS000657",
    prs: null,
    context: {
      not_a_substitute: "Not a DXA or FRAX substitute.",
      direction: "Higher score tracks higher heel eBMD.",
      utility: "Research-grade stratification only.",
      byo: "Bring your own licensed score bundle.",
    },
    research_use_only: true,
    scoring_state,
  }
}

function anchor(genotype: string) {
  return {
    trait: "t2d",
    trait_label: "Type 2 diabetes",
    gene: "TCF7L2",
    rsid: "rs7903146",
    effect_allele: "T",
    genotype,
    dosage: genotype.split("T").length - 1,
    indeterminate: false,
    summary: "Anchor variant.",
    evidence_level: 3,
    pmids: [],
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

beforeEach(() => {
  states.clear()
  getOutcome.clear()
  anchorGenotype = "CT"
  prsGeneration = () => "g1"
  anchorsGeneration = () => "g1"
  runOutcome.clear()
  runCalls.length = 0
  afterRun = markCurrentOnSuccess
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost")
      const sample = url.searchParams.get("sample_id") ?? ""
      if (url.pathname === "/api/analysis/ebmd/prs") {
        const status = getOutcome.get(sample) ?? 200
        return status === 200 ? json(ebmdBody(states.get(sample))) : json({ detail: "down" }, status)
      }
      if (url.pathname === "/api/analysis/metabolic/prs") {
        return json({
          items: [],
          total: 0,
          coverage_context: "",
          scoring_state: states.get(sample),
          scoring_generation: prsGeneration(),
        })
      }
      if (url.pathname === "/api/analysis/metabolic/anchors") {
        const generation = anchorsGeneration() // may move the anchors to a newer run
        return json({ items: [anchor(anchorGenotype)], total: 1, scoring_generation: generation })
      }
      if (url.pathname === "/api/analysis/ebmd/run" && init?.method === "POST") {
        runCalls.push(sample)
        afterRun(sample)
        if (runOutcome.get(sample) === 0) return new Promise<Response>(() => {}) // never settles
        const status = runOutcome.get(sample) ?? 200
        return status === 200
          ? json({ findings_count: 0, prs_computed: false })
          : json({ detail: "scoring failed" }, status)
      }
      return json({})
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function metabolicPage(queryClient: QueryClient) {
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/metabolic?sample_id=1"]}>
        <Routes>
          <Route path="/metabolic" element={<MetabolicView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function page(queryClient: QueryClient, path: string) {
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/ebmd"
            element={
              <>
                <EBMDView />
                <Link to="/ebmd?sample_id=1">Sample 1</Link>
                <Link to="/ebmd?sample_id=2">Sample 2</Link>
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("route-only scoring with the real hooks (#2118)", () => {
  it("re-checks a cached current score on the next visit", async () => {
    const queryClient = client()
    states.set("1", "current")
    const first = render(page(queryClient, "/ebmd?sample_id=1"))
    await screen.findByTestId("scoring-status")
    first.unmount()
    expect(runCalls).toEqual([])

    // A database update made the score stale while the page was closed.
    states.set("1", "stale")
    render(page(queryClient, "/ebmd?sample_id=1"))

    await waitFor(() => expect(runCalls).toEqual(["1"]))
  })

  it("decides on the re-checked state, not the cached one", async () => {
    const queryClient = client()
    states.set("1", "never")
    runOutcome.set("1", 500)
    const first = render(page(queryClient, "/ebmd?sample_id=1"))
    await within(await screen.findByTestId("scoring-status")).findByRole("alert")
    first.unmount()
    expect(runCalls).toEqual(["1"])

    // Scored elsewhere while the page was closed; the cache still says "never".
    states.set("1", "current")
    render(page(queryClient, "/ebmd?sample_id=1"))
    await screen.findByTestId("scoring-status")
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))

    expect(runCalls).toEqual(["1"])
  })

  it("does not score from a cached state the re-check could not confirm", async () => {
    const queryClient = client()
    states.set("1", "never")
    runOutcome.set("1", 500)
    const first = render(page(queryClient, "/ebmd?sample_id=1"))
    await within(await screen.findByTestId("scoring-status")).findByRole("alert")
    first.unmount()

    // The backend cannot be reached on the next visit; the cache still says "never".
    getOutcome.set("1", 503)
    render(page(queryClient, "/ebmd?sample_id=1"))
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    await waitFor(() => expect(queryClient.getQueryState(["ebmd", 1])?.status).toBe("error"))

    expect(runCalls).toEqual(["1"])
    // The cached results stay on screen, with a warning instead of a page error.
    expect(screen.getByText("Not a DXA or FRAX substitute.")).toBeInTheDocument()
    expect(screen.queryByText("Failed to load data")).toBeNull()
    expect(screen.getByText(/Couldn't check these results for changes/)).toBeInTheDocument()
  })

  it("shows anchors re-scored by another client on the next visit", async () => {
    const queryClient = client()
    states.set("1", "current")
    const first = render(metabolicPage(queryClient))
    expect(await screen.findByText("CT")).toBeInTheDocument()
    first.unmount()

    // Another tab re-scored after a re-annotation changed the anchor call.
    anchorGenotype = "TT"
    render(metabolicPage(queryClient))

    expect(await screen.findByText("TT")).toBeInTheDocument()
    expect(runCalls).toEqual([])
  })

  it("does not show one sample's failed run on another", async () => {
    states.set("1", "never")
    states.set("2", "current")
    runOutcome.set("1", 500)
    render(page(client(), "/ebmd?sample_id=1"))
    const status = await screen.findByTestId("scoring-status")
    await within(status).findByRole("alert")

    await userEvent.click(screen.getByRole("link", { name: "Sample 2" }))

    await waitFor(() =>
      expect(within(screen.getByTestId("scoring-status")).queryByRole("alert")).toBeNull(),
    )
    expect(screen.getByRole("button", { name: "Re-score" })).toBeEnabled()
    expect(runCalls).toEqual(["1"])
  })

  it("scores a sample again when it is shown again on the same page", async () => {
    states.set("1", "never")
    states.set("2", "current")
    runOutcome.set("1", 500)
    render(page(client(), "/ebmd?sample_id=1"))
    await within(await screen.findByTestId("scoring-status")).findByRole("alert")

    await userEvent.click(screen.getByRole("link", { name: "Sample 2" }))
    await waitFor(() =>
      expect(within(screen.getByTestId("scoring-status")).queryByRole("alert")).toBeNull(),
    )
    await userEvent.click(screen.getByRole("link", { name: "Sample 1" }))

    // Sample 1 is still unscored, so coming back to it tries again.
    await waitFor(() => expect(runCalls).toEqual(["1", "1"]))
  })

  it("runs again when an input changed during the run", async () => {
    states.set("1", "stale")
    // The first run finishes with the result recorded stale (an input changed
    // mid-run); the second one makes it current.
    afterRun = () => states.set("1", runCalls.length >= 2 ? "current" : "stale")
    render(page(client(), "/ebmd?sample_id=1"))

    await waitFor(() => expect(runCalls).toEqual(["1", "1"]), { timeout: 5000 })
    await waitFor(() => expect(screen.getByRole("button", { name: "Re-score" })).toBeEnabled())
    expect(runCalls).toEqual(["1", "1"])
  })

  it("stops starting runs automatically when the score never becomes current", async () => {
    states.set("1", "stale")
    afterRun = () => {} // every run leaves it stale
    render(page(client(), "/ebmd?sample_id=1"))

    await waitFor(() => expect(runCalls).toHaveLength(3), { timeout: 5000 })
    await waitFor(() => expect(screen.getByRole("button", { name: "Re-score" })).toBeEnabled())
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(runCalls).toHaveLength(3)
    // Having stopped, the page still says the results may be out of date.
    expect(within(screen.getByTestId("scoring-status")).getByRole("status")).toHaveTextContent(
      "may be out of date",
    )
  })

  it("runs again after a failure once the state was current in between", async () => {
    const queryClient = client()
    states.set("1", "stale")
    runOutcome.set("1", 500)
    render(page(queryClient, "/ebmd?sample_id=1"))
    await within(await screen.findByTestId("scoring-status")).findByRole("alert")

    // Another client scores it, then its inputs change again.
    runOutcome.delete("1")
    states.set("1", "current")
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["ebmd", 1] })
      // Let the page render the current state before it changes again.
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    states.set("1", "stale")
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["ebmd", 1] })
    })

    await waitFor(() => expect(runCalls).toEqual(["1", "1"]), { timeout: 5000 })
  })

  it("scores the next sample even when it is chosen while a run is still going", async () => {
    states.set("1", "never")
    states.set("2", "never")
    runOutcome.set("1", 0) // sample 1's run is still going when sample 2 is chosen
    render(page(client(), "/ebmd?sample_id=1"))
    await waitFor(() => expect(runCalls).toEqual(["1"]))

    await userEvent.click(screen.getByRole("link", { name: "Sample 2" }))

    await waitFor(() => expect(runCalls).toEqual(["1", "2"]), { timeout: 5000 })
  })

  it("scores once per visit when the response reports no state", async () => {
    // states has no entry, so the response omits scoring_state (an older backend).
    afterRun = () => {}
    render(page(client(), "/ebmd?sample_id=1"))

    await waitFor(() => expect(runCalls).toEqual(["1"]))
    await waitFor(() => expect(screen.getByRole("button", { name: "Re-score" })).toBeEnabled())
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(runCalls).toEqual(["1"])
  })

  it("re-checks a cached current sample when it is shown again on the same page", async () => {
    states.set("1", "current")
    states.set("2", "current")
    render(page(client(), "/ebmd?sample_id=1"))
    await screen.findByTestId("scoring-status")

    await userEvent.click(screen.getByRole("link", { name: "Sample 2" }))
    await screen.findByTestId("scoring-status")
    // Sample 1's inputs change while sample 2 is shown.
    states.set("1", "stale")
    await userEvent.click(screen.getByRole("link", { name: "Sample 1" }))

    await waitFor(() => expect(runCalls).toEqual(["1"]), { timeout: 5000 })
  })

  it("re-fetches anchors from a different run than the PRS", async () => {
    states.set("1", "current")
    // Another client's re-score lands between the two requests: the anchors
    // come from the previous run, the PRS from the new one.
    let anchorCalls = 0
    prsGeneration = () => "g2"
    anchorsGeneration = () => {
      anchorCalls += 1
      if (anchorCalls === 1) return "g1"
      anchorGenotype = "TT"
      return "g2"
    }
    render(metabolicPage(client()))

    expect(await screen.findByText("TT", undefined, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.queryByText("CT")).toBeNull()
    expect(runCalls).toEqual([])
  })

  it("re-checks a current score when the window regains focus", async () => {
    states.set("1", "current")
    render(page(client(), "/ebmd?sample_id=1"))
    await screen.findByTestId("scoring-status")
    expect(runCalls).toEqual([])

    // Another tab changed the sample's inputs while this page stayed open.
    states.set("1", "stale")
    await act(async () => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
    })

    await waitFor(() => expect(runCalls).toEqual(["1"]), { timeout: 5000 })
    act(() => focusManager.setFocused(undefined))
  })
})

