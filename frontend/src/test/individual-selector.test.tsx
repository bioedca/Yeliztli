/** IndividualSelector unit tests (Step 49 / IND-05; Plan §9.5).
 *
 * Covers:
 *   - loading + empty states
 *   - two-level grouping: individuals → their linked samples
 *   - "Unassigned" group for samples with no individual linkage
 *   - expand/collapse of individual groups
 *   - click-to-select writes `?sample_id=` to the URL
 *   - active sample highlights with `aria-selected` + Check icon
 *   - button label renders "Individual / Sample" when both apply,
 *     falls back to bare sample name for Unassigned, and "Select sample"
 *     when nothing is chosen yet
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, within } from "./test-utils"
import userEvent from "@testing-library/user-event"
import IndividualSelector from "@/components/layout/IndividualSelector"

const mockFetch = vi.fn()
globalThis.fetch = mockFetch as unknown as typeof fetch

interface MockSample {
  id: number
  name: string
  file_format: string
  created_at: string | null
}

interface MockIndividual {
  id: number
  display_name: string
  sample_ids: number[]
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    clone() {
      return this
    },
  } as unknown as Response
}

function installFetchScenario(
  samples: MockSample[],
  individuals: MockIndividual[],
) {
  const sampleById = new Map(samples.map((s) => [s.id, s]))
  mockFetch.mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString()
    if (url === "/api/samples") {
      return Promise.resolve(
        jsonResponse(
          samples.map((s) => ({
            id: s.id,
            name: s.name,
            db_path: `samples/sample_${s.id}.db`,
            file_format: s.file_format,
            file_hash: `hash${s.id}`,
            notes: null,
            date_collected: null,
            source: null,
            extra: null,
            created_at: s.created_at,
            updated_at: null,
          })),
        ),
      )
    }
    if (url === "/api/individuals") {
      return Promise.resolve(
        jsonResponse(
          individuals.map((ind) => ({
            id: ind.id,
            display_name: ind.display_name,
            notes: null,
            biological_sex: null,
            created_at: "2026-05-01T00:00:00",
            updated_at: null,
            sample_count: ind.sample_ids.length,
            vendors: [],
            last_activity: null,
          })),
        ),
      )
    }
    const m = /^\/api\/individuals\/(\d+)$/.exec(url)
    if (m) {
      const id = Number(m[1])
      const ind = individuals.find((x) => x.id === id)
      if (!ind) return Promise.resolve(jsonResponse({ detail: "not found" }, 404))
      return Promise.resolve(
        jsonResponse({
          id: ind.id,
          display_name: ind.display_name,
          notes: null,
          biological_sex: null,
          created_at: "2026-05-01T00:00:00",
          updated_at: null,
          linked_samples: ind.sample_ids.map((sid) => {
            const s = sampleById.get(sid)!
            return {
              id: s.id,
              name: s.name,
              file_format: s.file_format,
              vendor: s.file_format.split("_", 1)[0],
              created_at: s.created_at,
              updated_at: null,
            }
          }),
          aggregated_findings_count: 0,
        }),
      )
    }
    return Promise.resolve(jsonResponse({ detail: "unhandled" }, 500))
  })
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe("IndividualSelector", () => {
  it("shows the loading placeholder while samples + individuals load", () => {
    // Never-resolving promises keep both queries in flight.
    mockFetch.mockReturnValue(new Promise(() => {}))
    render(<IndividualSelector />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("renders 'No sample loaded' when both lists are empty", async () => {
    installFetchScenario([], [])
    render(<IndividualSelector />)
    await waitFor(() => {
      expect(screen.getByText("No sample loaded")).toBeInTheDocument()
    })
  })

  it("renders the selector button when samples exist", async () => {
    installFetchScenario(
      [
        {
          id: 1,
          name: "alice_23andme.txt",
          file_format: "23andme_v5",
          created_at: "2026-05-01T00:00:00",
        },
      ],
      [],
    )
    render(<IndividualSelector />)
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /switch sample/i }),
      ).toBeInTheDocument()
    })
  })

  it("groups linked samples under each individual and lists unassigned separately", async () => {
    installFetchScenario(
      [
        {
          id: 1,
          name: "alice_23andme.txt",
          file_format: "23andme_v5",
          created_at: "2026-05-01T00:00:00",
        },
        {
          id: 2,
          name: "alice_ancestry.txt",
          file_format: "ancestrydna_v2.0",
          created_at: "2026-05-02T00:00:00",
        },
        {
          id: 3,
          name: "orphan.txt",
          file_format: "23andme_v4",
          created_at: "2026-05-03T00:00:00",
        },
      ],
      [{ id: 10, display_name: "Alice", sample_ids: [1, 2] }],
    )

    render(<IndividualSelector />)
    const trigger = await screen.findByRole("button", { name: /switch sample/i })
    fireEvent.click(trigger)

    // Tree opens with the individual row + Unassigned section.
    const tree = await screen.findByRole("tree", {
      name: /individuals and samples/i,
    })
    expect(within(tree).getByText("Alice")).toBeInTheDocument()
    expect(within(tree).getByText(/Unassigned \(1\)/)).toBeInTheDocument()

    // The collapsed individual row hides its samples.
    expect(within(tree).queryByText("alice_23andme.txt")).not.toBeInTheDocument()

    // Unassigned sample is visible immediately.
    expect(within(tree).getByText("orphan.txt")).toBeInTheDocument()

    // Expand Alice to reveal both linked samples.
    fireEvent.click(within(tree).getByTestId("individual-row-10"))
    await waitFor(() => {
      expect(within(tree).getByText("alice_23andme.txt")).toBeInTheDocument()
      expect(within(tree).getByText("alice_ancestry.txt")).toBeInTheDocument()
    })

    // Collapsing again hides them.
    fireEvent.click(within(tree).getByTestId("individual-row-10"))
    await waitFor(() => {
      expect(
        within(tree).queryByText("alice_23andme.txt"),
      ).not.toBeInTheDocument()
    })
  })

  it("selects a linked sample on click, writes ?sample_id=, and labels with individual / sample", async () => {
    installFetchScenario(
      [
        {
          id: 1,
          name: "alice_23andme.txt",
          file_format: "23andme_v5",
          created_at: "2026-05-01T00:00:00",
        },
      ],
      [{ id: 10, display_name: "Alice", sample_ids: [1] }],
    )

    render(<IndividualSelector />)
    fireEvent.click(await screen.findByRole("button", { name: /switch sample/i }))

    const tree = await screen.findByRole("tree", {
      name: /individuals and samples/i,
    })
    // Wait until the per-individual detail fetch resolves (count chip = "1").
    await waitFor(() => {
      expect(within(tree).getByText("1")).toBeInTheDocument()
    })
    fireEvent.click(within(tree).getByTestId("individual-row-10"))
    const sampleRow = await within(tree).findByTestId("sample-row-1")
    fireEvent.click(sampleRow)

    await waitFor(() => {
      // After selection the dropdown closes and the trigger label updates.
      expect(
        screen.queryByRole("tree", { name: /individuals and samples/i }),
      ).not.toBeInTheDocument()
    })

    // Label includes the individual segment now that the URL carries
    // the active sample id and the active sample is linked to Alice.
    const trigger = screen.getByRole("button", { name: /switch sample/i })
    expect(trigger).toHaveTextContent(/Alice \/ alice_23andme\.txt/)
  })

  it("selects an unassigned sample with a bare-name label and marks it active", async () => {
    installFetchScenario(
      [
        {
          id: 5,
          name: "lonely.txt",
          file_format: "ancestrydna_v2.0",
          created_at: "2026-05-04T00:00:00",
        },
      ],
      [],
    )

    render(<IndividualSelector />)
    fireEvent.click(await screen.findByRole("button", { name: /switch sample/i }))

    const tree = await screen.findByRole("tree", {
      name: /individuals and samples/i,
    })
    fireEvent.click(within(tree).getByTestId("sample-row-5"))

    await waitFor(() => {
      expect(
        screen.queryByRole("tree", { name: /individuals and samples/i }),
      ).not.toBeInTheDocument()
    })

    const trigger = screen.getByRole("button", { name: /switch sample/i })
    expect(trigger).toHaveTextContent("lonely.txt")
    // Bare-name path: no "/" infix because the sample has no individual.
    expect(trigger.textContent).not.toMatch(/ \/ /)

    // Re-open and assert the row is marked active.
    fireEvent.click(trigger)
    const reopenedTree = await screen.findByRole("tree", {
      name: /individuals and samples/i,
    })
    const activeRow = within(reopenedTree).getByTestId("sample-row-5")
    expect(activeRow).toHaveAttribute("aria-selected", "true")
  })

  it("auto-expands the active individual on open", async () => {
    installFetchScenario(
      [
        {
          id: 1,
          name: "alice_23andme.txt",
          file_format: "23andme_v5",
          created_at: "2026-05-01T00:00:00",
        },
      ],
      [{ id: 10, display_name: "Alice", sample_ids: [1] }],
    )

    render(<IndividualSelector />)
    fireEvent.click(await screen.findByRole("button", { name: /switch sample/i }))
    const tree = await screen.findByRole("tree", {
      name: /individuals and samples/i,
    })
    fireEvent.click(within(tree).getByTestId("individual-row-10"))
    const sampleRow = await within(tree).findByTestId("sample-row-1")
    fireEvent.click(sampleRow)

    // Re-open and confirm Alice is already expanded (sample row visible
    // without a second toggle click).
    fireEvent.click(screen.getByRole("button", { name: /switch sample/i }))
    const reopenedTree = await screen.findByRole("tree", {
      name: /individuals and samples/i,
    })
    expect(
      within(reopenedTree).getByTestId("sample-row-1"),
    ).toBeInTheDocument()
    const indRow = within(reopenedTree).getByTestId("individual-row-10")
    expect(indRow).toHaveAttribute("aria-expanded", "true")
  })

  it("renders an empty-state hint when an individual has no linked samples", async () => {
    installFetchScenario(
      [
        {
          id: 1,
          name: "orphan.txt",
          file_format: "23andme_v5",
          created_at: "2026-05-01T00:00:00",
        },
      ],
      [{ id: 10, display_name: "Alice", sample_ids: [] }],
    )

    render(<IndividualSelector />)
    fireEvent.click(await screen.findByRole("button", { name: /switch sample/i }))
    const tree = await screen.findByRole("tree", {
      name: /individuals and samples/i,
    })
    fireEvent.click(within(tree).getByTestId("individual-row-10"))
    await waitFor(() => {
      expect(within(tree).getByText("No linked samples")).toBeInTheDocument()
    })
  })
})

describe("IndividualSelector keyboard (#2066)", () => {
  const SAMPLES: MockSample[] = [
    { id: 1, name: "alice_23andme.txt", file_format: "23andme_v5", created_at: null },
    { id: 2, name: "alice_ancestry.txt", file_format: "ancestrydna_v2.0", created_at: null },
    { id: 5, name: "lonely.txt", file_format: "ancestrydna_v2.0", created_at: null },
  ]
  const INDIVIDUALS: MockIndividual[] = [{ id: 10, display_name: "Alice", sample_ids: [1, 2] }]

  async function openTree() {
    installFetchScenario(SAMPLES, INDIVIDUALS)
    const user = userEvent.setup()
    render(<IndividualSelector />)
    const trigger = await screen.findByRole("button", { name: /switch sample/i })
    await user.click(trigger)
    const tree = await screen.findByRole("tree", { name: /individuals and samples/i })
    // Wait for Alice's linked samples (count chip "2") so the tree is complete.
    await waitFor(() => expect(within(tree).getByText("2")).toBeInTheDocument())
    return { user, trigger, tree }
  }

  const focused = () => document.activeElement as HTMLElement

  it("moves focus into the tree when it opens", async () => {
    const { tree } = await openTree()
    await waitFor(() => expect(focused()).toBe(within(tree).getByTestId("individual-row-10")))
  })

  it("returns focus to the trigger on Escape", async () => {
    const { user, trigger, tree } = await openTree()
    await waitFor(() => expect(tree.contains(focused())).toBe(true))

    await user.keyboard("{Escape}")

    expect(screen.queryByRole("tree", { name: /individuals and samples/i })).not.toBeInTheDocument()
    expect(focused()).toBe(trigger)
  })

  it("navigates the tree with the arrow, Home and End keys", async () => {
    const { user, tree } = await openTree()
    const ind = within(tree).getByTestId("individual-row-10")
    await waitFor(() => expect(focused()).toBe(ind))

    await user.keyboard("{ArrowRight}") // closed parent: expand, focus stays
    expect(ind).toHaveAttribute("aria-expanded", "true")
    expect(focused()).toBe(ind)
    await user.keyboard("{ArrowRight}") // open parent: first child
    expect(focused()).toBe(within(tree).getByTestId("sample-row-1"))
    await user.keyboard("{ArrowDown}")
    expect(focused()).toBe(within(tree).getByTestId("sample-row-2"))
    await user.keyboard("{ArrowDown}") // into the Unassigned samples
    expect(focused()).toBe(within(tree).getByTestId("sample-row-5"))
    await user.keyboard("{ArrowDown}") // last item: stays put
    expect(focused()).toBe(within(tree).getByTestId("sample-row-5"))
    await user.keyboard("{ArrowUp}")
    expect(focused()).toBe(within(tree).getByTestId("sample-row-2"))
    await user.keyboard("{Home}")
    expect(focused()).toBe(ind)
    await user.keyboard("{End}")
    expect(focused()).toBe(within(tree).getByTestId("sample-row-5"))

    await user.keyboard("{Home}{ArrowRight}") // to Alice's first sample
    await user.keyboard("{ArrowLeft}") // child: back to its parent
    expect(focused()).toBe(ind)
    await user.keyboard("{ArrowLeft}") // open parent: collapse
    expect(ind).toHaveAttribute("aria-expanded", "false")
    expect(within(tree).queryByTestId("sample-row-1")).not.toBeInTheDocument()
  })

  it("keeps exactly one treeitem in the tab order (roving tabindex)", async () => {
    const { user, tree } = await openTree()
    await waitFor(() => expect(tree.contains(focused())).toBe(true))
    const tabbable = () =>
      within(tree)
        .getAllByRole("treeitem")
        .filter((el) => el.tabIndex === 0)

    expect(tabbable()).toEqual([within(tree).getByTestId("individual-row-10")])
    await user.keyboard("{End}")
    expect(tabbable()).toEqual([within(tree).getByTestId("sample-row-5")])
  })

  it("returns focus to the trigger when an outside click on page content closes the tree", async () => {
    const { tree, trigger } = await openTree()
    await waitFor(() => expect(tree.contains(focused())).toBe(true))

    fireEvent.mouseDown(document.body)

    await waitFor(() =>
      expect(screen.queryByRole("tree", { name: /individuals and samples/i })).not.toBeInTheDocument(),
    )
    await waitFor(() => expect(focused()).toBe(trigger))
  })

  it("leaves focus on a focusable element the outside click landed on", async () => {
    installFetchScenario(SAMPLES, INDIVIDUALS)
    const user = userEvent.setup()
    render(
      <>
        <IndividualSelector />
        <input aria-label="Elsewhere" />
      </>,
    )
    await user.click(await screen.findByRole("button", { name: /switch sample/i }))
    await screen.findByRole("tree", { name: /individuals and samples/i })

    const elsewhere = screen.getByRole("textbox", { name: "Elsewhere" })
    await user.click(elsewhere)

    await waitFor(() =>
      expect(screen.queryByRole("tree", { name: /individuals and samples/i })).not.toBeInTheDocument(),
    )
    expect(focused()).toBe(elsewhere)
  })

  it("keeps focus in the tree when a late individual detail regroups the selected sample", async () => {
    // The active sample (1) belongs to Alice, but her detail arrives after the
    // menu opens: until then it renders under Unassigned and gets focus. The
    // regroup unmounts that node, which used to drop focus to <body>.
    installFetchScenario(SAMPLES, INDIVIDUALS)
    const scenario = mockFetch.getMockImplementation()!
    let releaseDetail: () => void = () => {}
    const detailGate = new Promise<void>((resolve) => {
      releaseDetail = resolve
    })
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString()
      if (url === "/api/individuals/10") return detailGate.then(() => scenario(input))
      return scenario(input)
    })
    const user = userEvent.setup()
    render(<IndividualSelector />, { route: "/?sample_id=1" })
    await user.click(await screen.findByRole("button", { name: /switch sample/i }))
    const tree = await screen.findByRole("tree", { name: /individuals and samples/i })
    await waitFor(() => expect(focused().dataset.treeKey).toBe("unassigned-1"))

    releaseDetail()

    await waitFor(() => expect(within(tree).getByTestId("sample-row-1").dataset.treeKey).toBe("sample-1"))
    // Focus lands on the selected sample's own row, or the individual holding it.
    await waitFor(() =>
      expect(["sample-1", "ind-10"]).toContain(focused().dataset.treeKey),
    )
    expect(
      within(tree)
        .getAllByRole("treeitem")
        .filter((el) => el.tabIndex === 0),
    ).toHaveLength(1)
  })

  it("returns focus to the trigger after selecting a sample with Enter", async () => {
    const { user, trigger, tree } = await openTree()
    await waitFor(() => expect(tree.contains(focused())).toBe(true))

    await user.keyboard("{End}{Enter}")

    await waitFor(() =>
      expect(screen.queryByRole("tree", { name: /individuals and samples/i })).not.toBeInTheDocument(),
    )
    expect(trigger).toHaveTextContent("lonely.txt")
    expect(focused()).toBe(trigger)
  })
})
