/** Variant table source / concordance columns + filter chips (Step 71 / Plan §10.7).
 *
 *  The chips and columns surface only when the sample's ``merge-provenance``
 *  response contains a non-null row. Unmerged samples return HTTP 200 with
 *  JSON null and see the chips and provenance columns suppressed. */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { act, render, screen, waitFor } from "./test-utils"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import VariantTable from "@/components/variant-table/VariantTable"
import type {
  ColumnPreset,
  ConcordanceTag,
  SourceTag,
  VariantCount,
  VariantPage,
} from "@/types/variants"

const mockFetch = vi.fn()

const PRESETS: ColumnPreset[] = [
  {
    name: "Clinical",
    columns: ["genotype", "gene_symbol", "consequence", "clinvar_significance"],
    predefined: true,
  },
]

function makePage(
  count: number,
  hasMore = false,
  startPos = 1000,
  source: SourceTag | "" = "S1",
  concordance: ConcordanceTag | "" = "match",
): VariantPage {
  return {
    items: Array.from({ length: count }, (_, i) => ({
      rsid: `rs${100 + i}`,
      chrom: "1",
      pos: startPos + i * 100,
      genotype: "AG",
      ref: "A",
      alt: "G",
      zygosity: "het",
      gene_symbol: "BRCA1",
      consequence: "missense_variant",
      clinvar_significance: null,
      clinvar_review_stars: null,
      gnomad_af_global: 0.001,
      rare_flag: true,
      cadd_phred: 25.5,
      sift_score: 0.01,
      sift_pred: "D",
      polyphen2_hsvar_score: 0.99,
      polyphen2_hsvar_pred: "D",
      revel: 0.85,
      annotation_coverage: 0b111111,
      evidence_conflict: false,
      ensemble_pathogenic: false,
      chrom_grch38: "1",
      pos_grch38: startPos + i * 100 + 50000,
      source,
      concordance,
      alt_rsid: "",
    })),
    next_cursor_chrom: hasMore ? "1" : null,
    next_cursor_pos: hasMore ? startPos + count * 100 : null,
    has_more: hasMore,
    limit: 100,
  }
}

const COUNT: VariantCount = { total: 4, filtered: false }

function pendingProvenanceResponse(retryAfter = "0") {
  const body = { detail: { error: "merge_provenance_pending" } }
  return {
    ok: false,
    status: 503,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "retry-after" ? retryAfter : null,
    },
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this
    },
  } as unknown as Response
}

const MERGED_PROVENANCE = {
  merged_at: "2026-05-01T00:00:00Z",
  strategy: "flag_only",
  source_sample_ids: [1, 2],
  source_file_hashes: ["abc", "def"],
  concordance_summary: { match: 3, discordant: 1 },
}

function setupMerged() {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/api/column-presets")) {
      return { ok: true, json: async () => ({ presets: PRESETS }) }
    }
    if (url.includes("/api/samples/") && url.includes("/merge-provenance")) {
      return {
        ok: true,
        json: async () => ({
          merged_at: "2026-05-01T00:00:00Z",
          strategy: "flag_only",
          source_sample_ids: [1, 2],
          source_file_hashes: ["abc", "def"],
          concordance_summary: { match: 3, discordant: 1 },
        }),
      }
    }
    if (url.includes("/api/variants/chromosomes")) {
      return { ok: true, json: async () => [{ chrom: "1", count: 4 }] }
    }
    if (url.includes("/api/variants/count")) {
      return { ok: true, json: async () => COUNT }
    }
    if (url.includes("/api/variants")) {
      return { ok: true, json: async () => makePage(4) }
    }
    return { ok: false, status: 404 }
  })
}

function setupUnmerged() {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/api/column-presets")) {
      return { ok: true, json: async () => ({ presets: PRESETS }) }
    }
    if (url.includes("/api/samples/") && url.includes("/merge-provenance")) {
      return {
        ok: true,
        status: 200,
        json: async () => null,
      }
    }
    if (url.includes("/api/variants/chromosomes")) {
      return { ok: true, json: async () => [{ chrom: "1", count: 4 }] }
    }
    if (url.includes("/api/variants/count")) {
      return { ok: true, json: async () => COUNT }
    }
    if (url.includes("/api/variants")) {
      return { ok: true, json: async () => makePage(4, false, 1000, "", "") }
    }
    return { ok: false, status: 404 }
  })
}

function variantsCalls(): string[] {
  return mockFetch.mock.calls
    .map((c) => c[0] as string)
    .filter(
      (url) =>
        url.includes("/api/variants?") &&
        !url.includes("count") &&
        !url.includes("chromosomes"),
    )
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch)
  mockFetch.mockReset()
  window.history.replaceState({}, "", window.location.pathname)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("VariantTable source/concordance columns (Step 71)", () => {
  it("retries typed pending provenance before issuing sample-derived queries", async () => {
    let provenanceCalls = 0
    let resolveProvenance: ((response: Response) => void) | undefined
    const materialised = new Promise<Response>((resolve) => {
      resolveProvenance = resolve
    })
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets")) {
        return { ok: true, json: async () => ({ presets: PRESETS }) }
      }
      if (url.includes("/merge-provenance")) {
        provenanceCalls += 1
        if (provenanceCalls === 1) return pendingProvenanceResponse()
        return materialised
      }
      if (url.includes("/api/variants/chromosomes")) {
        return { ok: true, json: async () => [{ chrom: "1", count: 4 }] }
      }
      if (url.includes("/api/variants/count")) {
        return { ok: true, json: async () => COUNT }
      }
      if (url.includes("/api/variants")) {
        return { ok: true, json: async () => makePage(4) }
      }
      return { ok: true, json: async () => [] }
    })

    render(<VariantTable sampleId={1} />)
    await waitFor(() => expect(provenanceCalls).toBe(2))
    expect(variantsCalls()).toHaveLength(0)
    expect(
      mockFetch.mock.calls.some(([url]) => String(url).includes("/api/tags")),
    ).toBe(false)
    expect(
      mockFetch.mock.calls.some(([url]) => String(url).includes("/api/watches")),
    ).toBe(false)

    resolveProvenance?.({
      ok: true,
      status: 200,
      json: async () => MERGED_PROVENANCE,
    } as Response)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /filter by source/i })).toBeInTheDocument()
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
  })

  it("fails closed with a retry control after bounded pending responses", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets")) {
        return { ok: true, json: async () => ({ presets: PRESETS }) }
      }
      if (url.includes("/merge-provenance")) return pendingProvenanceResponse()
      throw new Error(`sample-derived query escaped provenance gate: ${url}`)
    })

    render(<VariantTable sampleId={1} />)
    await waitFor(
      () => expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument(),
      { timeout: 5_000 },
    )
    expect(
      mockFetch.mock.calls.filter(([url]) =>
        String(url).includes("/merge-provenance"),
      ),
    ).toHaveLength(31)
    expect(variantsCalls()).toHaveLength(0)

    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    await waitFor(
      () =>
        expect(
          mockFetch.mock.calls.filter(([url]) =>
            String(url).includes("/merge-provenance"),
          ),
        ).toHaveLength(62),
      { timeout: 5_000 },
    )
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument()
    expect(variantsCalls()).toHaveLength(0)
  })

  it("does not retry an untyped 503 as merge provenance pending", async () => {
    let provenanceCalls = 0
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets")) {
        return { ok: true, json: async () => ({ presets: PRESETS }) }
      }
      if (url.includes("/merge-provenance")) {
        provenanceCalls += 1
        return {
          ...pendingProvenanceResponse(),
          json: async () => ({ detail: { error: "service_unavailable" } }),
        }
      }
      if (url.includes("/api/variants/chromosomes")) {
        return { ok: true, json: async () => [{ chrom: "1", count: 4 }] }
      }
      if (url.includes("/api/variants/count")) {
        return { ok: true, json: async () => COUNT }
      }
      if (url.includes("/api/variants")) {
        return { ok: true, json: async () => makePage(4) }
      }
      return { ok: true, json: async () => [] }
    })

    render(<VariantTable sampleId={1} />)
    await waitFor(() => expect(screen.getByText("rs100")).toBeInTheDocument())
    expect(provenanceCalls).toBe(1)
  })

  it("settles a native provenance fetch rejection without retrying", async () => {
    let provenanceCalls = 0
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets")) {
        return { ok: true, json: async () => ({ presets: PRESETS }) }
      }
      if (url.includes("/merge-provenance")) {
        provenanceCalls += 1
        throw new TypeError("network unavailable")
      }
      if (url.includes("/api/variants/chromosomes")) {
        return { ok: true, json: async () => [{ chrom: "1", count: 4 }] }
      }
      if (url.includes("/api/variants/count")) {
        return { ok: true, json: async () => COUNT }
      }
      if (url.includes("/api/variants")) {
        return { ok: true, json: async () => makePage(4) }
      }
      return { ok: true, json: async () => [] }
    })

    render(<VariantTable sampleId={1} />)
    await waitFor(() => expect(screen.getByText("rs100")).toBeInTheDocument())
    expect(provenanceCalls).toBe(1)
  })

  it("renders Source and Concordance headers when merge-provenance resolves", async () => {
    setupMerged()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(
        screen.getByRole("columnheader", { name: "Source" }),
      ).toBeInTheDocument()
      expect(
        screen.getByRole("columnheader", { name: "Concordance" }),
      ).toBeInTheDocument()
    })
  })

  it("does not render Source / Concordance for unmerged samples", async () => {
    setupUnmerged()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    expect(
      screen.queryByRole("columnheader", { name: "Source" }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("columnheader", { name: "Concordance" }),
    ).not.toBeInTheDocument()
  })

  it("hides merge-only controls when a cached provenance refetch fails", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 60_000 },
        mutations: { retry: false },
      },
    })
    setupMerged()
    render(
      <QueryClientProvider client={queryClient}>
        <VariantTable sampleId={1} />
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /filter by source/i }),
      ).toBeInTheDocument()
    })

    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/samples/1/merge-provenance")) {
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "Sample 1 not found." }),
        }
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    await act(async () => {
      await queryClient.invalidateQueries({
        queryKey: ["samples", 1, "merge-provenance"],
        exact: true,
      })
    })

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /filter by source/i }),
      ).not.toBeInTheDocument()
    })
    expect(
      screen.queryByRole("columnheader", { name: "Source" }),
    ).not.toBeInTheDocument()
  })

  it("renders human-readable labels in body cells", async () => {
    setupMerged()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      // S₁ label per SOURCE_LABELS map
      expect(screen.getAllByText("S₁").length).toBeGreaterThan(0)
      expect(screen.getAllByText("Match").length).toBeGreaterThan(0)
    })
  })
})

describe("VariantToolbar source/concordance filter chips (Step 71)", () => {
  it("shows Source and Concordance dropdowns only when sample is merged", async () => {
    setupMerged()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /filter by source/i }),
      ).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: /filter by concordance/i }),
      ).toBeInTheDocument()
    })
  })

  it("hides Source and Concordance dropdowns for unmerged samples", async () => {
    setupUnmerged()
    render(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    expect(
      screen.queryByRole("button", { name: /filter by source/i }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /filter by concordance/i }),
    ).not.toBeInTheDocument()
  })

  it("appends source:S2 to the API filter when Source S₂ is selected", async () => {
    setupMerged()
    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: /filter by source/i }))
    await waitFor(() =>
      expect(screen.getByRole("listbox", { name: "Source values" })).toBeInTheDocument(),
    )
    await user.click(screen.getByRole("option", { name: "S₂" }))

    await waitFor(() => {
      const matched = variantsCalls().find(
        (url) =>
          url.includes("source%3AS2") || url.includes("source:S2"),
      )
      expect(matched).toBeDefined()
    })
  })

  it("appends concordance:discordant to the API filter when selected", async () => {
    setupMerged()
    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    await user.click(
      screen.getByRole("button", { name: /filter by concordance/i }),
    )
    await waitFor(() =>
      expect(
        screen.getByRole("listbox", { name: "Concordance values" }),
      ).toBeInTheDocument(),
    )
    await user.click(screen.getByRole("option", { name: "Discordant" }))

    await waitFor(() => {
      const matched = variantsCalls().find(
        (url) =>
          url.includes("concordance%3Adiscordant") ||
          url.includes("concordance:discordant"),
      )
      expect(matched).toBeDefined()
    })
  })

  it("Source and Concordance filters can stack and clear independently", async () => {
    setupMerged()
    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Apply Source = both
    await user.click(screen.getByRole("button", { name: /filter by source/i }))
    await waitFor(() =>
      expect(screen.getByRole("listbox", { name: "Source values" })).toBeInTheDocument(),
    )
    await user.click(screen.getByRole("option", { name: "Both" }))

    // Apply Concordance = match
    await user.click(
      screen.getByRole("button", { name: /filter by concordance/i }),
    )
    await waitFor(() =>
      expect(
        screen.getByRole("listbox", { name: "Concordance values" }),
      ).toBeInTheDocument(),
    )
    await user.click(screen.getByRole("option", { name: "Match" }))

    await waitFor(() => {
      const matched = variantsCalls().find(
        (url) =>
          (url.includes("source%3Aboth") || url.includes("source:both")) &&
          (url.includes("concordance%3Amatch") ||
            url.includes("concordance:match")),
      )
      expect(matched).toBeDefined()
    })

    // The chip label now says "Both"; clicking the chevron-less label clears it
    const sourceChip = screen.getByRole("button", {
      name: /source filter: s₁|source filter: s₂|source filter: both/i,
    })
    expect(sourceChip).toBeInTheDocument()
  })
})
