/** Settings → Samples per-row "Assign to individual" dropdown
 * (Step 51 / IND-07; Plan §9.5).
 *
 * Covers:
 *   - dropdown lists existing individuals + "Unassigned" + "Create new…"
 *   - selecting an existing individual fires POST /link-sample
 *   - selecting "Unassigned" on a linked sample fires POST /unlink-sample
 *   - switching from one individual to another fires unlink → link in order
 *   - "Create new…" branch creates the individual then links the sample
 *   - 409 link-conflict surfaces an inline error message
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, within } from "./test-utils"
import SampleMetadataEditor from "@/components/settings/SampleMetadataEditor"

const mockFetch = vi.fn()
globalThis.fetch = mockFetch as unknown as typeof fetch

interface MockSample {
  id: number
  name: string
  file_format: string
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

function buildSamplePayload(samples: MockSample[]) {
  return samples.map((s) => ({
    id: s.id,
    name: s.name,
    db_path: `samples/sample_${s.id}.db`,
    file_format: s.file_format,
    file_hash: `hash${s.id}`,
    notes: null,
    date_collected: null,
    source: null,
    extra: null,
    created_at: "2026-05-01T00:00:00",
    updated_at: null,
  }))
}

function buildIndividualDetail(
  ind: MockIndividual,
  samples: MockSample[],
) {
  const byId = new Map(samples.map((s) => [s.id, s]))
  return {
    id: ind.id,
    display_name: ind.display_name,
    notes: null,
    biological_sex: null,
    created_at: "2026-05-01T00:00:00",
    updated_at: null,
    linked_samples: ind.sample_ids.map((sid) => {
      const s = byId.get(sid)!
      return {
        id: s.id,
        name: s.name,
        file_format: s.file_format,
        vendor: s.file_format.split("_", 1)[0],
        created_at: "2026-05-01T00:00:00",
        updated_at: null,
      }
    }),
    aggregated_findings_count: 0,
  }
}

function installFetchScenario(
  samples: MockSample[],
  individuals: MockIndividual[],
  overrides: {
    onLink?: (
      individualId: number,
      sampleId: number,
    ) => Response | Promise<Response>
    onUnlink?: (
      individualId: number,
      sampleId: number,
    ) => Response | Promise<Response>
    onCreate?: (body: { display_name: string }) => Response | Promise<Response>
  } = {},
) {
  mockFetch.mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString()
      const method = (init?.method ?? "GET").toUpperCase()
      if (url === "/api/samples" && method === "GET") {
        return Promise.resolve(jsonResponse(buildSamplePayload(samples)))
      }
      if (url === "/api/individuals" && method === "GET") {
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
      const detailMatch = /^\/api\/individuals\/(\d+)$/.exec(url)
      if (detailMatch && method === "GET") {
        const id = Number(detailMatch[1])
        const ind = individuals.find((x) => x.id === id)
        if (!ind) {
          return Promise.resolve(jsonResponse({ detail: "not found" }, 404))
        }
        return Promise.resolve(jsonResponse(buildIndividualDetail(ind, samples)))
      }
      const linkMatch = /^\/api\/individuals\/(\d+)\/link-sample$/.exec(url)
      if (linkMatch && method === "POST") {
        const indId = Number(linkMatch[1])
        const body = init?.body ? JSON.parse(init.body as string) : {}
        if (overrides.onLink) {
          return Promise.resolve(overrides.onLink(indId, body.sample_id))
        }
        const ind = individuals.find((x) => x.id === indId)
        if (!ind) {
          return Promise.resolve(jsonResponse({ detail: "not found" }, 404))
        }
        if (!ind.sample_ids.includes(body.sample_id)) {
          ind.sample_ids = [...ind.sample_ids, body.sample_id]
        }
        return Promise.resolve(jsonResponse(buildIndividualDetail(ind, samples)))
      }
      const unlinkMatch = /^\/api\/individuals\/(\d+)\/unlink-sample$/.exec(url)
      if (unlinkMatch && method === "POST") {
        const indId = Number(unlinkMatch[1])
        const body = init?.body ? JSON.parse(init.body as string) : {}
        if (overrides.onUnlink) {
          return Promise.resolve(overrides.onUnlink(indId, body.sample_id))
        }
        const ind = individuals.find((x) => x.id === indId)
        if (!ind) {
          return Promise.resolve(jsonResponse({ detail: "not found" }, 404))
        }
        ind.sample_ids = ind.sample_ids.filter((sid) => sid !== body.sample_id)
        return Promise.resolve(jsonResponse(buildIndividualDetail(ind, samples)))
      }
      if (url === "/api/individuals" && method === "POST") {
        const body = init?.body ? JSON.parse(init.body as string) : {}
        if (overrides.onCreate) {
          return Promise.resolve(overrides.onCreate(body))
        }
        const nextId = (individuals.reduce((m, x) => Math.max(m, x.id), 0) || 0) + 1
        const created: MockIndividual = {
          id: nextId,
          display_name: body.display_name,
          sample_ids: [],
        }
        individuals.push(created)
        return Promise.resolve(jsonResponse(buildIndividualDetail(created, samples)))
      }
      return Promise.resolve(jsonResponse({ detail: "unhandled" }, 500))
    },
  )
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe("SampleMetadataEditor — assign to individual", () => {
  it("lists existing individuals + Unassigned + Create new… in the dropdown", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [
        { id: 10, display_name: "Alice", sample_ids: [] },
        { id: 11, display_name: "Bob", sample_ids: [] },
      ],
    )

    render(<SampleMetadataEditor />)
    const select = (await screen.findByTestId(
      "assign-individual-select-1",
    )) as HTMLSelectElement

    const optionLabels = within(select)
      .getAllByRole("option")
      .map((opt) => opt.textContent?.trim())
    expect(optionLabels).toEqual([
      "Unassigned",
      "Alice",
      "Bob",
      "+ Create new…",
    ])
    expect(select.value).toBe("unassigned")
  })

  it("shows the current individual when the sample is already linked", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [{ id: 10, display_name: "Alice", sample_ids: [1] }],
    )

    render(<SampleMetadataEditor />)
    const select = (await screen.findByTestId(
      "assign-individual-select-1",
    )) as HTMLSelectElement
    await waitFor(() => {
      expect(select.value).toBe("10")
    })
  })

  it("POSTs /link-sample when an unassigned sample picks an existing individual", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [{ id: 10, display_name: "Alice", sample_ids: [] }],
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    fireEvent.change(select, { target: { value: "10" } })

    await waitFor(() => {
      const linkCall = mockFetch.mock.calls.find(
        ([url, init]) =>
          url === "/api/individuals/10/link-sample" &&
          (init as RequestInit)?.method === "POST",
      )
      expect(linkCall).toBeTruthy()
      expect(JSON.parse((linkCall![1] as RequestInit).body as string)).toEqual({
        sample_id: 1,
      })
    })
  })

  it("POSTs /unlink-sample when a linked sample is set back to Unassigned", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [{ id: 10, display_name: "Alice", sample_ids: [1] }],
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    await waitFor(() => {
      expect((select as HTMLSelectElement).value).toBe("10")
    })

    fireEvent.change(select, { target: { value: "unassigned" } })

    await waitFor(() => {
      const unlinkCall = mockFetch.mock.calls.find(
        ([url, init]) =>
          url === "/api/individuals/10/unlink-sample" &&
          (init as RequestInit)?.method === "POST",
      )
      expect(unlinkCall).toBeTruthy()
      expect(JSON.parse((unlinkCall![1] as RequestInit).body as string)).toEqual({
        sample_id: 1,
      })
    })
  })

  it("unlinks from the prior individual before linking to a new one when reassigning", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [
        { id: 10, display_name: "Alice", sample_ids: [1] },
        { id: 11, display_name: "Bob", sample_ids: [] },
      ],
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    await waitFor(() => {
      expect((select as HTMLSelectElement).value).toBe("10")
    })

    fireEvent.change(select, { target: { value: "11" } })

    await waitFor(() => {
      const unlinkIdx = mockFetch.mock.calls.findIndex(
        ([url, init]) =>
          url === "/api/individuals/10/unlink-sample" &&
          (init as RequestInit)?.method === "POST",
      )
      const linkIdx = mockFetch.mock.calls.findIndex(
        ([url, init]) =>
          url === "/api/individuals/11/link-sample" &&
          (init as RequestInit)?.method === "POST",
      )
      expect(unlinkIdx).toBeGreaterThanOrEqual(0)
      expect(linkIdx).toBeGreaterThanOrEqual(0)
      expect(unlinkIdx).toBeLessThan(linkIdx)
    })
  })

  it("creates a new individual then links the sample when picking Create new…", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [],
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    fireEvent.change(select, { target: { value: "create" } })

    const nameInput = await screen.findByTestId("assign-new-name-1")
    fireEvent.change(nameInput, { target: { value: "Charlie" } })
    fireEvent.click(screen.getByTestId("assign-create-confirm-1"))

    await waitFor(() => {
      const createCall = mockFetch.mock.calls.find(
        ([url, init]) =>
          url === "/api/individuals" && (init as RequestInit)?.method === "POST",
      )
      expect(createCall).toBeTruthy()
      expect(JSON.parse((createCall![1] as RequestInit).body as string)).toEqual({
        display_name: "Charlie",
      })
    })

    // After create, the new individual receives a link-sample POST.
    await waitFor(() => {
      const linkCall = mockFetch.mock.calls.find(
        ([url, init]) =>
          /^\/api\/individuals\/\d+\/link-sample$/.test(url as string) &&
          (init as RequestInit)?.method === "POST",
      )
      expect(linkCall).toBeTruthy()
    })

    await waitFor(() => {
      expect(screen.queryByTestId("assign-new-name-1")).not.toBeInTheDocument()
    })
  })

  it("cancels Create new… without creating an individual", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [],
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    fireEvent.change(select, { target: { value: "create" } })

    await screen.findByTestId("assign-new-name-1")
    fireEvent.click(screen.getByTestId("assign-create-cancel-1"))

    await waitFor(() => {
      expect(screen.queryByTestId("assign-new-name-1")).not.toBeInTheDocument()
    })
    const createCall = mockFetch.mock.calls.find(
      ([url, init]) =>
        url === "/api/individuals" && (init as RequestInit)?.method === "POST",
    )
    expect(createCall).toBeFalsy()
  })

  it("submits Create new… on Enter and closes the input", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [],
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    fireEvent.change(select, { target: { value: "create" } })

    const nameInput = await screen.findByTestId("assign-new-name-1")
    fireEvent.change(nameInput, { target: { value: "Charlie" } })
    fireEvent.keyDown(nameInput, { key: "Enter" })

    await waitFor(() => {
      const createCall = mockFetch.mock.calls.find(
        ([url, init]) =>
          url === "/api/individuals" &&
          (init as RequestInit)?.method === "POST",
      )
      expect(createCall).toBeTruthy()
    })

    await waitFor(() => {
      expect(screen.queryByTestId("assign-new-name-1")).not.toBeInTheDocument()
    })
  })

  it("cancels Create new… on Escape without creating an individual", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [],
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    fireEvent.change(select, { target: { value: "create" } })

    const nameInput = await screen.findByTestId("assign-new-name-1")
    fireEvent.change(nameInput, { target: { value: "Charlie" } })
    fireEvent.keyDown(nameInput, { key: "Escape" })

    await waitFor(() => {
      expect(screen.queryByTestId("assign-new-name-1")).not.toBeInTheDocument()
    })
    const createCall = mockFetch.mock.calls.find(
      ([url, init]) =>
        url === "/api/individuals" && (init as RequestInit)?.method === "POST",
    )
    expect(createCall).toBeFalsy()
  })

  it("surfaces a generic non-409 error from link-sample on the inline alert", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [{ id: 10, display_name: "Alice", sample_ids: [] }],
      {
        onLink: () =>
          jsonResponse({ detail: "server exploded" }, 500),
      },
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    fireEvent.change(select, { target: { value: "10" } })

    const alert = await screen.findByTestId("assign-error-1")
    expect(alert.textContent).toMatch(/failed to link sample/i)
    expect(alert.textContent).not.toMatch(/server exploded/i)
  })

  it("surfaces a 409 link-conflict body as an inline error", async () => {
    installFetchScenario(
      [{ id: 1, name: "alice_23andme.txt", file_format: "23andme_v5" }],
      [{ id: 10, display_name: "Alice", sample_ids: [] }],
      {
        onLink: () =>
          jsonResponse(
            {
              detail: {
                sample_id: 1,
                individual_id: 99,
                individual_display_name: "Bob",
                message: "Sample is already linked to Bob",
              },
            },
            409,
          ),
      },
    )

    render(<SampleMetadataEditor />)
    const select = await screen.findByTestId("assign-individual-select-1")
    fireEvent.change(select, { target: { value: "10" } })

    const alert = await screen.findByTestId("assign-error-1")
    expect(alert.textContent).toMatch(/already linked/i)
  })
})
