/** Per-row watch toggle in the variant table (#2123).
 *
 * Discoverability follow-up to #1995: watching used to be reachable only by
 * opening a variant's detail panel. These tests cover the control itself and,
 * more importantly, the three things the issue flags as traps — that the click
 * must not also open the row's detail panel, that per-row state must come from
 * the single already-loaded watch list rather than one request per row, and
 * that the control is reachable and distinguishable by assistive technology.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import userEvent from "@testing-library/user-event"
import { act, render, screen, waitFor, within } from "./test-utils"

import RowWatchToggle from "@/components/variant-table/RowWatchToggle"
import VariantTable from "@/components/variant-table/VariantTable"
import type { VariantPage } from "@/types/variants"

const mockFetch = vi.fn()

function makePage(rsids: string[]): VariantPage {
  return {
    items: rsids.map((rsid, i) => ({
      rsid,
      chrom: "1",
      pos: 1000 + i * 100,
      ref: "A",
      alt: "G",
      genotype: "AG",
      zygosity: "het",
      gene_symbol: "GENE1",
      consequence: "missense_variant",
      clinvar_significance: null,
      clinvar_review_stars: null,
      gnomad_af_global: 0.01,
      rare_flag: false,
      cadd_phred: 10,
      sift_score: null,
      sift_pred: null,
      polyphen2_hsvar_score: null,
      polyphen2_hsvar_pred: null,
      revel: null,
      annotation_coverage: 0b111111,
      evidence_conflict: false,
      ensemble_pathogenic: false,
      chrom_grch38: null,
      pos_grch38: null,
    })) as VariantPage["items"],
    next_cursor_chrom: null,
    next_cursor_pos: null,
    has_more: false,
    limit: 100,
  }
}

/** Watched rsIDs the stubbed API reports, mutated by watch/unwatch calls. */
let watched: string[] = []

function setupFetchMock(rsids: string[]) {
  mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes("/api/watches")) {
      const method = init?.method ?? "GET"
      if (method === "POST") {
        const body = JSON.parse(String(init?.body ?? "{}"))
        if (!watched.includes(body.rsid)) watched.push(body.rsid)
        return { ok: true, json: async () => ({ rsid: body.rsid }) }
      }
      if (method === "DELETE") {
        const rsid = decodeURIComponent(url.split("/api/watches/")[1].split("?")[0])
        watched = watched.filter((w) => w !== rsid)
        return { ok: true, json: async () => ({}) }
      }
      return { ok: true, json: async () => watched.map((rsid) => ({ rsid })) }
    }
    if (url.includes("/api/column-presets")) {
      return { ok: true, json: async () => ({ presets: [] }) }
    }
    if (url.includes("/api/tags")) {
      return { ok: true, json: async () => [] }
    }
    if (url.includes("/api/samples/") && url.includes("/merge-provenance")) {
      return { ok: true, status: 200, json: async () => null }
    }
    if (url.includes("/api/variants/chromosomes")) {
      return { ok: true, json: async () => [{ chrom: "1", count: rsids.length }] }
    }
    if (url.includes("/api/variants/count")) {
      return { ok: true, json: async () => ({ total: rsids.length, filtered: false }) }
    }
    if (url.includes("/api/variants")) {
      return { ok: true, json: async () => makePage(rsids) }
    }
    return { ok: false, status: 404 }
  })
}

beforeEach(() => {
  watched = []
  vi.stubGlobal("fetch", mockFetch)
  mockFetch.mockReset()
  window.history.replaceState({}, "", window.location.pathname)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("RowWatchToggle (#2123)", () => {
  it("names itself with the rsID so rows are distinguishable", async () => {
    render(<RowWatchToggle rsid="rs42" isWatched={false} onToggle={() => {}} />)

    // Every row renders this control, so a bare "Watch this variant" would give
    // a screen-reader user many identically named buttons.
    const button = screen.getByRole("button", { name: "Watch rs42" })
    expect(button).toHaveAttribute("aria-pressed", "false")
  })

  it("carries state in aria-pressed and keeps a stable name", () => {
    render(<RowWatchToggle rsid="rs42" isWatched onToggle={() => {}} />)

    // The ARIA toggle-button pattern: the name does not change with state. It
    // also must not become "Unwatch rs42", which is exactly the name the
    // WatchingSidebar's own unwatch control already uses.
    const button = screen.getByRole("button", { name: "Watch rs42" })
    expect(button).toHaveAttribute("aria-pressed", "true")
    expect(button).toHaveAttribute("title", "Unwatch rs42")
    expect(screen.queryByRole("button", { name: "Unwatch rs42" })).toBeNull()
  })

  it("does not open the row while toggling", async () => {
    const onToggle = vi.fn()
    const onRowClick = vi.fn()
    const user = userEvent.setup()

    // Mirrors production exactly: VariantTable renders
    // <tr onClick={() => setSelectedRsid(row.original.rsid)}> around the cells.
    render(
      <table>
        <tbody>
          <tr onClick={onRowClick}>
            <td>
              <RowWatchToggle rsid="rs42" isWatched={false} onToggle={onToggle} />
            </td>
          </tr>
        </tbody>
      </table>,
    )

    await user.click(screen.getByRole("button", { name: "Watch rs42" }))

    expect(onToggle).toHaveBeenCalledWith("rs42")
    // The discriminating assertion: without stopPropagation the row handler
    // fires too and the user gets a detail panel they did not ask for.
    expect(onRowClick).not.toHaveBeenCalled()
  })

  it("is operable from the keyboard without opening the row", async () => {
    const onToggle = vi.fn()
    const onRowClick = vi.fn()
    const user = userEvent.setup()

    render(
      <table>
        <tbody>
          <tr onClick={onRowClick}>
            <td>
              <RowWatchToggle rsid="rs42" isWatched={false} onToggle={onToggle} />
            </td>
          </tr>
        </tbody>
      </table>,
    )

    await user.tab()
    expect(screen.getByRole("button", { name: "Watch rs42" })).toHaveFocus()
    await user.keyboard("{Enter}")

    expect(onToggle).toHaveBeenCalledWith("rs42")
    expect(onRowClick).not.toHaveBeenCalled()
  })

  it("is disabled and says why when watch state is unknown", async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(
      <RowWatchToggle
        rsid="rs42"
        isWatched={false}
        unavailableReason="Loading your watched variants…"
        onToggle={onToggle}
      />,
    )

    const button = screen.getByRole("button", { name: "Watch rs42" })
    expect(button).toBeDisabled()
    // A disabled control must say why, rather than offering an action the user
    // cannot take.
    expect(button).toHaveAttribute("title", "Loading your watched variants…")
    await user.click(button)
    expect(onToggle).not.toHaveBeenCalled()
  })

  it("ignores clicks while its own request is in flight", async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(<RowWatchToggle rsid="rs42" isWatched={false} isPending onToggle={onToggle} />)

    const button = screen.getByRole("button", { name: "Watch rs42" })
    expect(button).toBeDisabled()
    // The spinner carries no text, so the description is the only textual
    // signal that the write is still running — and it must not offer an action
    // that cannot be taken.
    expect(button).toHaveAttribute("title", "Watching rs42…")
    await user.click(button)

    expect(onToggle).not.toHaveBeenCalled()
  })

  it("describes an in-flight unwatch rather than offering Unwatch", () => {
    render(<RowWatchToggle rsid="rs42" isWatched isPending onToggle={() => {}} />)

    expect(screen.getByRole("button", { name: "Watch rs42" })).toHaveAttribute(
      "title",
      "Unwatching rs42…",
    )
  })
})

describe("VariantTable row watch toggle (#2123)", () => {
  it("renders a toggle on every row, reflecting the loaded watch list", async () => {
    watched = ["rs2"]
    setupFetchMock(["rs1", "rs2", "rs3"])
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeInTheDocument()
    })
    // rs2 is already watched, so its control reads as pressed.
    expect(screen.getByRole("button", { name: "Watch rs2" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    expect(screen.getByRole("button", { name: "Watch rs1" })).toHaveAttribute(
      "aria-pressed",
      "false",
    )
    expect(screen.getByRole("button", { name: "Watch rs3" })).toHaveAttribute(
      "aria-pressed",
      "false",
    )
  })

  it("costs one watch request for the whole table, not one per row", async () => {
    setupFetchMock(["rs1", "rs2", "rs3", "rs4", "rs5"])
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs5" })).toBeInTheDocument()
    })

    // The issue names this trap explicitly: deriving each row's state from its
    // own query would issue one GET per visible row. Five rows, one GET.
    const watchGets = mockFetch.mock.calls.filter(
      ([url, init]) =>
        String(url).includes("/api/watches") && (init?.method ?? "GET") === "GET",
    )
    expect(watchGets).toHaveLength(1)
  })

  it("watches a variant from the row without opening its detail panel", async () => {
    setupFetchMock(["rs1", "rs2"])
    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: "Watch rs1" }))

    // The control flips to the watched state once the list refetches.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toHaveAttribute(
        "aria-pressed",
        "true",
      )
    })
    expect(
      mockFetch.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/api/watches") && init?.method === "POST",
      ),
    ).toBe(true)
    // Rows open the detail panel on click; the toggle must not.
    expect(screen.queryByRole("dialog", { name: /Variant detail for rs1/ })).toBeNull()
  })

  it("unwatches an already-watched variant from the row", async () => {
    watched = ["rs1"]
    setupFetchMock(["rs1", "rs2"])
    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toHaveAttribute(
        "aria-pressed",
        "true",
      )
    })

    await user.click(screen.getByRole("button", { name: "Watch rs1" }))

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toHaveAttribute(
        "aria-pressed",
        "false",
      )
    })
    expect(
      mockFetch.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/api/watches/") && init?.method === "DELETE",
      ),
    ).toBe(true)
  })

  it("does not present an actionable toggle before the watch list loads", async () => {
    // Codex review of #2456: `watchedVariants ?? []` treated an unknown watch
    // list as empty, so an already-watched variant rendered as unpressed and a
    // click became a duplicate POST that the backend answers with 409.
    let releaseWatches: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      releaseWatches = resolve
    })
    setupFetchMock(["rs1"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/watches") && (init?.method ?? "GET") === "GET") {
        await held
      }
      return inner(url, init)
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeInTheDocument()
    })
    const button = screen.getByRole("button", { name: "Watch rs1" })
    expect(button).toBeDisabled()
    await user.click(button)
    expect(
      mockFetch.mock.calls.some(
        ([url, init]) => String(url).includes("/api/watches") && init?.method === "POST",
      ),
    ).toBe(false)

    await act(async () => {
      releaseWatches()
    })
    await waitFor(() => expect(button).toBeEnabled())
  })

  it("disables the toggle when the watch list fails to load", async () => {
    setupFetchMock(["rs1"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/watches") && (init?.method ?? "GET") === "GET") {
        return { ok: false, status: 500 }
      }
      return inner(url, init)
    })

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeDisabled()
    })
    expect(screen.getByRole("button", { name: "Watch rs1" })).toHaveAttribute(
      "title",
      expect.stringContaining("unavailable") as unknown as string,
    )
  })

  it("keeps every in-flight row disabled, not just the latest", async () => {
    // Codex review of #2456: a mutation hook's `variables` describes only the
    // call it currently observes, so toggling a second row re-enabled the first
    // while its request was still running and a second click could submit a
    // duplicate write.
    let releasePosts: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      releasePosts = resolve
    })
    setupFetchMock(["rs1", "rs2"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/watches") && init?.method === "POST") {
        await held
      }
      return inner(url, init)
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })

    await user.click(screen.getByRole("button", { name: "Watch rs1" }))
    await user.click(screen.getByRole("button", { name: "Watch rs2" }))

    // Both requests are still in flight, so both rows stay disabled.
    expect(screen.getByRole("button", { name: "Watch rs1" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Watch rs2" })).toBeDisabled()

    await act(async () => {
      releasePosts()
    })
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
      expect(screen.getByRole("button", { name: "Watch rs2" })).toBeEnabled()
    })
    // Exactly one POST per row — no duplicates.
    const posts = mockFetch.mock.calls.filter(
      ([url, init]) => String(url).includes("/api/watches") && init?.method === "POST",
    )
    expect(posts).toHaveLength(2)
  })

  it("stays pending until the refreshed watch list arrives", async () => {
    // Codex review round 2: the hooks invalidate in a block-bodied onSuccess
    // without returning the promise, so mutateAsync settles before the refetch.
    // Settling the row then re-enabled it while membership was still the
    // pre-mutation value, and another click repeated the same POST (409).
    let releaseGet: () => void = () => {}
    let holdGet = false
    setupFetchMock(["rs1"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/watches") && (init?.method ?? "GET") === "GET" && holdGet) {
        await new Promise<void>((resolve) => {
          releaseGet = resolve
        })
      }
      return inner(url, init)
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })

    holdGet = true
    await user.click(screen.getByRole("button", { name: "Watch rs1" }))

    // POST has resolved, but membership is not refreshed yet, so the row must
    // not be actionable again.
    await waitFor(() => {
      expect(
        mockFetch.mock.calls.some(
          ([url, init]) => String(url).includes("/api/watches") && init?.method === "POST",
        ),
      ).toBe(true)
    })
    expect(screen.getByRole("button", { name: "Watch rs1" })).toBeDisabled()

    await act(async () => {
      releaseGet()
    })
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toHaveAttribute(
        "aria-pressed",
        "true",
      )
    })
    const posts = mockFetch.mock.calls.filter(
      ([url, init]) => String(url).includes("/api/watches") && init?.method === "POST",
    )
    expect(posts).toHaveLength(1)
  })

  it("does not carry a row's pending state across a sample switch", async () => {
    // VariantExplorer renders VariantTable without a key, so this state
    // survives a sample_id change. A hung request for sample A must not leave
    // sample B's identically named variant disabled.
    let releasePost: () => void = () => {}
    setupFetchMock(["rs1"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/watches") && init?.method === "POST") {
        await new Promise<void>((resolve) => {
          releasePost = resolve
        })
      }
      return inner(url, init)
    })

    const user = userEvent.setup()
    const { rerender } = render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })
    await user.click(screen.getByRole("button", { name: "Watch rs1" }))
    expect(screen.getByRole("button", { name: "Watch rs1" })).toBeDisabled()

    rerender(<VariantTable sampleId={2} />)

    // Sample 2's rs1 is a different entity and must be actionable.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })

    await act(async () => {
      releasePost()
    })

    // Back to sample 1: its row settled rather than being stuck pending, which
    // also exercises A -> B -> A on a table that is never remounted.
    rerender(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })
  })

  it("tells the user when a row watch fails", async () => {
    // The round-1 fix swallowed the rejection to avoid an unhandled rejection,
    // which left a failed write indistinguishable from one that was ignored.
    setupFetchMock(["rs1"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/watches") && init?.method === "POST") {
        return { ok: false, status: 500, json: async () => ({ detail: "boom" }) }
      }
      return inner(url, init)
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })

    await user.click(screen.getByRole("button", { name: "Watch rs1" }))

    const alert = await waitFor(() => screen.getByRole("alert"))
    expect(alert).toHaveTextContent(/rs1/)
    // The control stays actionable so the user can retry, and says so.
    const button = screen.getByRole("button", { name: "Watch rs1" })
    expect(button).toBeEnabled()
    expect(button.getAttribute("title")).toMatch(/retry/i)
  })

  it.each([
    ["rs1 last", ["rs2", "rs1"]],
    ["rs2 last", ["rs1", "rs2"]],
  ])("keeps both concurrent row failures when %s settles", async (_label, order) => {
    // A single error slot meant whichever rejection settled last erased the
    // other row's alert, ring and retry tooltip. Both settlement orders are
    // covered because the bug is order-dependent.
    const gates = new Map<string, () => void>()
    setupFetchMock(["rs1", "rs2"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/watches") && init?.method === "POST") {
        const rsid = JSON.parse(String(init.body ?? "{}")).rsid as string
        await new Promise<void>((resolve) => gates.set(rsid, resolve))
        return { ok: false, status: 500, json: async () => ({ detail: `boom ${rsid}` }) }
      }
      return inner(url, init)
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })

    await user.click(screen.getByRole("button", { name: "Watch rs1" }))
    await user.click(screen.getByRole("button", { name: "Watch rs2" }))
    await waitFor(() => expect(gates.size).toBe(2))

    for (const rsid of order) {
      await act(async () => {
        gates.get(rsid)!()
      })
    }

    const alert = await waitFor(() => screen.getByRole("alert"))
    expect(alert).toHaveTextContent(/rs1/)
    expect(alert).toHaveTextContent(/rs2/)
    // Both controls offer a retry, not just the one that settled last.
    for (const rsid of ["rs1", "rs2"]) {
      const button = screen.getByRole("button", { name: `Watch ${rsid}` })
      expect(button).toBeEnabled()
      expect(button.getAttribute("title")).toMatch(/retry/i)
    }
  })

  it("drops a row's failure once the variant is watched elsewhere", async () => {
    // Codex review round 5: a failed row POST left its failure in place even
    // after another control (detail panel, sidebar) successfully watched the
    // same variant. The row then read "retry unwatching rs1" — following which
    // would have undone that success.
    let rs1WatchedExternally = false
    setupFetchMock(["rs1", "rs2"])
    const inner = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET"
      if (url.includes("/api/watches") && method === "POST") {
        const rsid = JSON.parse(String(init?.body ?? "{}")).rsid as string
        if (rsid === "rs1") {
          return { ok: false, status: 500, json: async () => ({ detail: "boom" }) }
        }
      }
      if (url.includes("/api/watches") && method === "GET" && rs1WatchedExternally) {
        return { ok: true, json: async () => [{ rsid: "rs1" }] }
      }
      return inner(url, init)
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeEnabled()
    })

    // rs1's own attempt fails, so the row reports it.
    await user.click(screen.getByRole("button", { name: "Watch rs1" }))
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/rs1/))

    // Something else watches rs1 successfully; clicking rs2 refreshes the
    // shared list, which is how this row learns about it without being clicked.
    rs1WatchedExternally = true
    await user.click(screen.getByRole("button", { name: "Watch rs2" }))

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toHaveAttribute(
        "aria-pressed",
        "true",
      )
    })
    // The stale failure is gone: no alert for rs1, and the tooltip offers the
    // real action rather than instructing the user to undo the success.
    expect(screen.queryByRole("alert")).toBeNull()
    const rs1 = screen.getByRole("button", { name: "Watch rs1" })
    expect(rs1.getAttribute("title")).toBe("Unwatch rs1")
    expect(rs1.getAttribute("title")).not.toMatch(/retry/i)
  })

  it("keeps the toggle out of the column-visibility menu", async () => {
    setupFetchMock(["rs1"])
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Watch rs1" })).toBeInTheDocument()
    })

    // The affordance is the point of #2123, so a preset or a persisted
    // visibility preference must not be able to remove it. It is pinned and
    // non-hideable, like the conflict flag beside it.
    const columnsButton = screen.queryByRole("button", { name: /columns/i })
    if (columnsButton) {
      const user = userEvent.setup()
      await user.click(columnsButton)
      const menu = screen.queryByRole("menu") ?? document.body
      expect(within(menu).queryByLabelText(/^watch$/i)).toBeNull()
    }
  })
})
