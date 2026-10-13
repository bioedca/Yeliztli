import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "./test-utils"
import SampleMetadataEditor from "@/components/settings/SampleMetadataEditor"

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

const SAMPLE_LIST = [
  {
    id: 1,
    name: "genome_a.txt",
    db_path: "samples/sample_1.db",
    file_format: "23andme_v5",
    file_hash: "abc",
    notes: null,
    date_collected: null,
    source: null,
    extra: null,
    created_at: "2025-06-01T00:00:00",
    updated_at: null,
  },
]

const SAMPLE_DETAIL = {
  ...SAMPLE_LIST[0],
  notes: "Test note",
  source: "23andMe",
  date_collected: "2025-01-15",
  extra: { ethnicity: "European" },
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

/** Returns a fetch implementation that routes /api/samples + /api/individuals
 * to the canned responses defined per-test, while keeping default no-op
 * responses for the individuals routes the assign-to-individual dropdown
 * (Step 51) added to SampleMetadataEditor. */
function makeRouter(handlers: {
  samples?: (init?: RequestInit) => Response | Promise<Response>
  sampleDetail?: (id: number, init?: RequestInit) => Response | Promise<Response>
  mergedChildren?: (id: number) => Response | Promise<Response>
  deleteSample?: (id: number) => Response | Promise<Response>
}) {
  return (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString()
    const method = (init?.method ?? "GET").toUpperCase()
    if (url === "/api/samples" && method === "GET" && handlers.samples) {
      return Promise.resolve(handlers.samples(init))
    }
    const mergedMatch = /^\/api\/samples\/(\d+)\/merged-children$/.exec(url)
    if (mergedMatch && method === "GET") {
      const id = Number(mergedMatch[1])
      if (handlers.mergedChildren) {
        return Promise.resolve(handlers.mergedChildren(id))
      }
      return Promise.resolve(jsonResponse([]))
    }
    const detailMatch = /^\/api\/samples\/(\d+)$/.exec(url)
    if (detailMatch) {
      const id = Number(detailMatch[1])
      if (method === "DELETE" && handlers.deleteSample) {
        return Promise.resolve(handlers.deleteSample(id))
      }
      if (handlers.sampleDetail) {
        return Promise.resolve(handlers.sampleDetail(id, init))
      }
    }
    if (url === "/api/individuals" && method === "GET") {
      return Promise.resolve(jsonResponse([]))
    }
    if (/^\/api\/individuals\/\d+$/.test(url) && method === "GET") {
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404))
    }
    return Promise.resolve(jsonResponse({ detail: "unhandled" }, 500))
  }
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe("SampleMetadataEditor", () => {
  it("shows empty state when no samples", async () => {
    mockFetch.mockImplementation(
      makeRouter({ samples: () => jsonResponse([]) }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("no-samples")).toBeInTheDocument()
    })
  })

  it("renders sample list with names", async () => {
    mockFetch.mockImplementation(
      makeRouter({ samples: () => jsonResponse(SAMPLE_LIST) }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByText("genome_a.txt")).toBeInTheDocument()
    })
  })

  it("expands edit form on edit button click", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        sampleDetail: () => jsonResponse(SAMPLE_DETAIL),
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-edit-1")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("sample-edit-1"))

    await waitFor(() => {
      expect(screen.getByTestId("sample-edit-form")).toBeInTheDocument()
    })
  })

  it("shows delete confirmation dialog on delete click", async () => {
    mockFetch.mockImplementation(
      makeRouter({ samples: () => jsonResponse(SAMPLE_LIST) }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-delete-1")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("sample-delete-1"))

    await waitFor(() => {
      expect(screen.getByTestId("delete-confirm-dialog")).toBeInTheDocument()
      expect(screen.getByTestId("delete-confirm-btn")).toBeInTheDocument()
    })
  })

  it("cancels delete when cancel button is clicked", async () => {
    mockFetch.mockImplementation(
      makeRouter({ samples: () => jsonResponse(SAMPLE_LIST) }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-delete-1")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("sample-delete-1"))

    await waitFor(() => {
      expect(screen.getByTestId("delete-cancel-btn")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("delete-cancel-btn"))

    await waitFor(() => {
      expect(screen.queryByTestId("delete-confirm-dialog")).not.toBeInTheDocument()
    })
  })

  it("displays metadata fields in edit form", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        sampleDetail: () => jsonResponse(SAMPLE_DETAIL),
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-edit-1")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("sample-edit-1"))

    await waitFor(() => {
      expect(screen.getByTestId("sample-name-input")).toHaveValue("genome_a.txt")
      expect(screen.getByTestId("sample-source-input")).toHaveValue("23andMe")
      expect(screen.getByTestId("sample-notes-input")).toHaveValue("Test note")
      expect(screen.getByTestId("sample-date-input")).toHaveValue("2025-01-15")
    })
  })

  it("save button is disabled when no changes", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        sampleDetail: () => jsonResponse(SAMPLE_DETAIL),
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-edit-1")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("sample-edit-1"))

    await waitFor(() => {
      expect(screen.getByTestId("sample-save-btn")).toBeDisabled()
    })
  })

  it("calls update mutation with changed fields on save", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        sampleDetail: (_id, init) => {
          if ((init?.method ?? "GET").toUpperCase() === "PATCH") {
            return jsonResponse({ ...SAMPLE_DETAIL, notes: "Updated note" })
          }
          return jsonResponse(SAMPLE_DETAIL)
        },
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-edit-1")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("sample-edit-1"))

    await waitFor(() => {
      expect(screen.getByTestId("sample-notes-input")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByTestId("sample-notes-input"), {
      target: { value: "Updated note" },
    })

    fireEvent.click(screen.getByTestId("sample-save-btn"))

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/samples/1",
        expect.objectContaining({
          method: "PATCH",
          body: expect.stringContaining("Updated note"),
        })
      )
    })
  })

  it("save button is enabled after making changes", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        sampleDetail: () => jsonResponse(SAMPLE_DETAIL),
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-edit-1")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId("sample-edit-1"))

    await waitFor(() => {
      expect(screen.getByTestId("sample-notes-input")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByTestId("sample-notes-input"), {
      target: { value: "Updated note" },
    })

    expect(screen.getByTestId("sample-save-btn")).not.toBeDisabled()
  })
})

// ── A sample cannot be saved without a name (#2099) ──────────────────

describe("SampleMetadataEditor — name is required", () => {
  async function openEditor() {
    const patches: string[] = []
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        sampleDetail: (_id, init) => {
          if ((init?.method ?? "GET").toUpperCase() === "PATCH") {
            patches.push(String(init?.body))
            return jsonResponse({ ...SAMPLE_DETAIL, ...JSON.parse(String(init?.body)) })
          }
          return jsonResponse(SAMPLE_DETAIL)
        },
      }),
    )
    render(<SampleMetadataEditor />)
    await waitFor(() => expect(screen.getByTestId("sample-edit-1")).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("sample-edit-1"))
    await waitFor(() => expect(screen.getByTestId("sample-name-input")).toBeInTheDocument())
    return patches
  }

  it.each([
    ["empty", ""],
    ["whitespace only", "   "],
  ])("keeps Save disabled, and says why, when the name is %s", async (_label, value) => {
    const patches = await openEditor()
    fireEvent.change(screen.getByTestId("sample-name-input"), { target: { value } })

    const save = screen.getByTestId("sample-save-btn")
    expect(save).toBeDisabled()
    const reason = screen.getByText("Enter a name for this sample before saving.")
    expect(save).toHaveAttribute("aria-describedby", reason.id)
    expect(screen.getByTestId("sample-name-input")).toHaveAttribute("aria-invalid", "true")

    fireEvent.click(save)
    expect(patches).toHaveLength(0)
  })

  it("keeps Save disabled for a blank name even when another field changed", async () => {
    const patches = await openEditor()
    fireEvent.change(screen.getByTestId("sample-notes-input"), { target: { value: "new note" } })
    expect(screen.getByTestId("sample-save-btn")).toBeEnabled()

    fireEvent.change(screen.getByTestId("sample-name-input"), { target: { value: "" } })
    expect(screen.getByTestId("sample-save-btn")).toBeDisabled()
    fireEvent.click(screen.getByTestId("sample-save-btn"))
    expect(patches).toHaveLength(0)
  })

  it("sends the name trimmed", async () => {
    const patches = await openEditor()
    fireEvent.change(screen.getByTestId("sample-name-input"), {
      target: { value: "  Mother's kit  " },
    })
    const save = screen.getByTestId("sample-save-btn")
    expect(save).toBeEnabled()
    expect(save).not.toHaveAttribute("aria-describedby")
    expect(screen.queryByText("Enter a name for this sample before saving.")).not.toBeInTheDocument()

    fireEvent.click(save)
    await waitFor(() => expect(patches).toHaveLength(1))
    expect(JSON.parse(patches[0]).name).toBe("Mother's kit")
  })

  it("does not count whitespace around the current name as a change", async () => {
    await openEditor()
    fireEvent.change(screen.getByTestId("sample-name-input"), {
      target: { value: `  ${SAMPLE_DETAIL.name}  ` },
    })
    expect(screen.getByTestId("sample-save-btn")).toBeDisabled()
  })
})

// ── Source-deletion cascade UI (Step 66 / Plan §10.8) ────────────────

describe("SampleMetadataEditor — delete cascade", () => {
  it("shows no cascade block when sample has never been merged", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        mergedChildren: () => jsonResponse([]),
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-delete-1")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId("sample-delete-1"))

    const confirmBtn = await screen.findByTestId("delete-confirm-btn")
    await waitFor(() => {
      expect(confirmBtn.textContent).toMatch(/^Delete Sample$/)
    })
    expect(screen.queryByTestId("delete-cascade-1")).not.toBeInTheDocument()
  })

  it("surfaces merged children count + names + warns the action cascades", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        mergedChildren: () =>
          jsonResponse([
            { id: 42, name: "alice (merged)" },
            { id: 43, name: "alice (re-merged)" },
          ]),
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-delete-1")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId("sample-delete-1"))

    const cascade = await screen.findByTestId("delete-cascade-1")
    expect(cascade.textContent).toMatch(/2 merged samples/i)
    expect(screen.getByTestId("delete-cascade-child-42").textContent).toBe(
      "alice (merged)",
    )
    expect(screen.getByTestId("delete-cascade-child-43").textContent).toBe(
      "alice (re-merged)",
    )
    expect(screen.getByTestId("delete-confirm-btn").textContent).toMatch(
      /Delete Sample \+ 2 Merged/,
    )
  })

  it("singular form when exactly one merged child references the source", async () => {
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        mergedChildren: () =>
          jsonResponse([{ id: 7, name: "alice (merged)" }]),
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-delete-1")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId("sample-delete-1"))

    const cascade = await screen.findByTestId("delete-cascade-1")
    expect(cascade.textContent).toMatch(/1 merged sample(?!s)/i)
    expect(screen.getByTestId("delete-confirm-btn").textContent).toMatch(
      /Delete Sample \+ 1 Merged/,
    )
  })

  it("issues a single DELETE on confirm regardless of cascade size", async () => {
    let deleteCalls = 0
    mockFetch.mockImplementation(
      makeRouter({
        samples: () => jsonResponse(SAMPLE_LIST),
        mergedChildren: () =>
          jsonResponse([{ id: 42, name: "alice (merged)" }]),
        deleteSample: () => {
          deleteCalls += 1
          return jsonResponse(null, 204)
        },
      }),
    )

    render(<SampleMetadataEditor />)
    await waitFor(() => {
      expect(screen.getByTestId("sample-delete-1")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId("sample-delete-1"))
    await screen.findByTestId("delete-cascade-1")

    fireEvent.click(screen.getByTestId("delete-confirm-btn"))
    await waitFor(() => {
      expect(deleteCalls).toBe(1)
    })
  })
})
