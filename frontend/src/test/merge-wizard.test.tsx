/** MergeWizard unit tests (Step 69 / MRG-05; Plan §10.7).
 *
 * Covers the 3-step wizard flow:
 *   - Step 1 (Strategy): `flag_only` is the default + explanatory text.
 *   - Step 2 (Preview): concordance summary renders after the dry-run
 *     mutation resolves.
 *   - Step 3 (Confirm): commit fires the mutation and binds to the SSE
 *     annotation channel; empty `job_id` surfaces the re-annotate CTA.
 *
 * Step 87 (MRG-12) is the canonical Phase 3 coverage gate; this file
 * lands the test surface in the same PR as the component implementation
 * per CLAUDE.md DoD ("Land new/changed tests in this same step.").
 */

import { act } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "./test-utils"

import { MergeWizard } from "@/components/individuals/MergeWizard"
import type { LinkedSample } from "@/types/individuals"

// ── Mock fetch ────────────────────────────────────────────────────────

const mockFetch = vi.fn()

// ── Mock EventSource ──────────────────────────────────────────────────

type EventSourceListener = (event: MessageEvent) => void

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  listeners: Record<string, EventSourceListener[]> = {}
  readyState = 0

  constructor(url: string) {
    this.url = url
    this.readyState = 1
    MockEventSource.instances.push(this)
  }

  addEventListener(event: string, listener: EventSourceListener) {
    if (!this.listeners[event]) this.listeners[event] = []
    this.listeners[event].push(listener)
  }

  close() {
    this.readyState = 2
  }

  _emit(event: string, data: unknown) {
    const listeners = this.listeners[event] ?? []
    for (const fn of listeners) {
      fn(new MessageEvent(event, { data: JSON.stringify(data) }))
    }
  }
}

// ── Test data ─────────────────────────────────────────────────────────

const LINKED_SAMPLES: LinkedSample[] = [
  {
    id: 11,
    name: "Mom 23andMe",
    file_format: "23andme_v5",
    vendor: "23andme",
    is_merged: false,
    created_at: "2026-05-01T00:00:00",
    updated_at: null,
  },
  {
    id: 22,
    name: "Mom AncestryDNA",
    file_format: "ancestrydna_v2.0",
    vendor: "ancestrydna",
    is_merged: false,
    created_at: "2026-05-02T00:00:00",
    updated_at: null,
  },
]

const CONCORDANCE_PAYLOAD = {
  concordance_summary: {
    match: 412_345,
    filled_nocall: 1234,
    discordant: 87,
    unique_S1: 5_000,
    unique_S2: 6_500,
    collapsed_rsid: 19,
  },
  est_duration_seconds: 8,
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this
    },
  } as unknown as Response
}

function renderWizard(overrides: { onClose?: () => void } = {}) {
  return render(
    <MergeWizard
      individualId={7}
      individualDisplayName="Mom"
      linkedSamples={LINKED_SAMPLES}
      sourceSampleIds={[11, 22]}
      onClose={overrides.onClose ?? (() => {})}
    />,
  )
}

beforeEach(() => {
  mockFetch.mockReset()
  MockEventSource.instances = []
  vi.stubGlobal("fetch", mockFetch)
  vi.stubGlobal("EventSource", MockEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ═══════════════════════════════════════════════════════════════════════
// Step 1 — strategy picker
// ═══════════════════════════════════════════════════════════════════════

describe("MergeWizard — strategy step", () => {
  it("defaults to flag_only with explanatory text (Plan §10.3)", () => {
    renderWizard()

    const flagOnly = screen.getByRole("radio", {
      name: /Flag discordant calls/i,
    }) as HTMLInputElement
    expect(flagOnly.checked).toBe(true)

    expect(
      screen.getByText(/Clinically safest — withholds a call/i),
    ).toBeInTheDocument()
  })

  it("renders the three Plan §10.3 strategies", () => {
    renderWizard()
    const radios = screen.getAllByRole("radio")
    expect(radios).toHaveLength(3)
    const values = radios.map((r) => (r as HTMLInputElement).value).sort()
    expect(values).toEqual(["flag_only", "prefer_23andme", "prefer_ancestrydna"])
  })

  it("surfaces the chosen S1/S2 pair", () => {
    renderWizard()
    expect(screen.getByTestId("merge-source-pair")).toHaveTextContent(
      "Mom 23andMe",
    )
    expect(screen.getByTestId("merge-source-pair")).toHaveTextContent(
      "Mom AncestryDNA",
    )
  })

  it("fires onClose when Cancel is clicked", () => {
    const onClose = vi.fn()
    renderWizard({ onClose })
    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

// ═══════════════════════════════════════════════════════════════════════
// Step 2 — preview
// ═══════════════════════════════════════════════════════════════════════

describe("MergeWizard — preview step", () => {
  it("posts to /merge/preview with the selected strategy and renders the summary", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CONCORDANCE_PAYLOAD))

    renderWizard()

    fireEvent.click(screen.getByRole("button", { name: /^Preview$/ }))

    await waitFor(() => {
      expect(screen.getByTestId("merge-preview-summary")).toBeInTheDocument()
    })

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/individuals/7/merge/preview",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          source_sample_ids: [11, 22],
          strategy: "flag_only",
        }),
      }),
    )

    expect(screen.getByTestId("concordance-match")).toHaveTextContent("412,345")
    expect(screen.getByTestId("concordance-discordant")).toHaveTextContent("87")
    expect(screen.getByTestId("concordance-collapsed_rsid")).toHaveTextContent(
      "19",
    )
    expect(
      screen.getByText(/Estimated merge \+ annotation: ~8s/),
    ).toBeInTheDocument()
  })

  it("surfaces the API error message on preview failure", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ detail: "Source sample 11 is stale" }, 423),
    )

    renderWizard()

    fireEvent.click(screen.getByRole("button", { name: /^Preview$/ }))

    await waitFor(() => {
      expect(
        screen.getByText(/Source sample 11 is stale/),
      ).toBeInTheDocument()
    })
  })

  it("back button returns to strategy step", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CONCORDANCE_PAYLOAD))

    renderWizard()
    fireEvent.click(screen.getByRole("button", { name: /^Preview$/ }))

    await waitFor(() =>
      expect(screen.getByTestId("merge-preview-summary")).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole("button", { name: /^Back$/ }))

    expect(
      screen.getByRole("radio", { name: /Flag discordant calls/i }),
    ).toBeInTheDocument()
  })
})

// ═══════════════════════════════════════════════════════════════════════
// Step 3 — confirm + commit + SSE
// ═══════════════════════════════════════════════════════════════════════

describe("MergeWizard — confirm step", () => {
  async function advanceToConfirm() {
    fireEvent.click(screen.getByRole("button", { name: /^Preview$/ }))
    await waitFor(() =>
      expect(screen.getByTestId("merge-preview-summary")).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }))
  }

  it("commits via POST /merge and binds SSE to the returned job_id", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CONCORDANCE_PAYLOAD))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ merged_sample_id: 99, job_id: "merge-job-1" }, 201),
    )

    renderWizard()
    await advanceToConfirm()

    fireEvent.click(screen.getByRole("button", { name: /^Merge$/ }))

    await waitFor(() =>
      expect(screen.getByTestId("merge-progress")).toBeInTheDocument(),
    )

    const commitCall = mockFetch.mock.calls.find(
      (call) => call[0] === "/api/individuals/7/merge",
    )
    expect(commitCall).toBeTruthy()
    expect(commitCall![1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        source_sample_ids: [11, 22],
        strategy: "flag_only",
        display_name: "Mom (merged)",
      }),
    })

    // EventSource opened against the returned job_id.
    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1)
    })
    expect(MockEventSource.instances[0].url).toBe(
      "/api/annotation/status/merge-job-1",
    )

    // Drive a progress event — UI updates message + pct.
    act(() => {
      MockEventSource.instances[0]._emit("progress", {
        job_id: "merge-job-1",
        status: "running",
        progress_pct: 42,
        message: "Annotating merged sample…",
        error: null,
      })
    })
    expect(
      screen.getByText("Annotating merged sample…"),
    ).toBeInTheDocument()
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "42",
    )
  })

  it("renders the re-annotate CTA when the commit response carries an empty job_id", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CONCORDANCE_PAYLOAD))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ merged_sample_id: 99, job_id: "" }, 201),
    )

    renderWizard()
    await advanceToConfirm()

    fireEvent.click(screen.getByRole("button", { name: /^Merge$/ }))

    await waitFor(() =>
      expect(screen.getByTestId("merge-progress")).toBeInTheDocument(),
    )

    expect(
      screen.getByText(/Annotation was not enqueued automatically/i),
    ).toBeInTheDocument()
    expect(MockEventSource.instances).toHaveLength(0)
  })

  it("surfaces the commit error and keeps the user on confirm", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CONCORDANCE_PAYLOAD))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ detail: "Source not fresh" }, 423),
    )

    renderWizard()
    await advanceToConfirm()

    fireEvent.click(screen.getByRole("button", { name: /^Merge$/ }))

    await waitFor(() =>
      expect(screen.getByText(/Source not fresh/)).toBeInTheDocument(),
    )
    expect(screen.queryByTestId("merge-progress")).not.toBeInTheDocument()
  })
})
