import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { act, render, screen, waitFor, within } from "./test-utils"
import userEvent from "@testing-library/user-event"
import VariantTable from "@/components/variant-table/VariantTable"
import type { VariantPage, VariantCount, ChromosomeSummary, ColumnPreset, Tag } from "@/types/variants"

// Mock fetch globally
const mockFetch = vi.fn()

const defaultPresets: ColumnPreset[] = [
  {
    name: "Clinical",
    columns: ["genotype", "gene_symbol", "consequence", "clinvar_significance", "clinvar_review_stars"],
    predefined: true,
  },
  {
    name: "Research",
    columns: [
      "genotype", "gene_symbol", "consequence", "clinvar_significance", "clinvar_review_stars",
      "cadd_phred", "sift_score", "sift_pred", "polyphen2_hsvar_score", "polyphen2_hsvar_pred",
      "revel", "ensemble_pathogenic",
    ],
    predefined: true,
  },
  {
    name: "Frequency",
    columns: ["genotype", "gene_symbol", "gnomad_af_global", "rare_flag"],
    predefined: true,
  },
  {
    name: "Scores",
    columns: [
      "gene_symbol", "consequence", "cadd_phred", "sift_score", "sift_pred",
      "polyphen2_hsvar_score", "polyphen2_hsvar_pred", "revel",
    ],
    predefined: true,
  },
]

function makeVariantPage(
  count: number,
  hasMore = false,
  startPos = 1000,
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
      gene_symbol: i % 2 === 0 ? "BRCA1" : "TP53",
      consequence: "missense_variant",
      clinvar_significance: i === 0 ? "Pathogenic" : null,
      clinvar_review_stars: i === 0 ? 2 : null,
      gnomad_af_global: 0.001,
      rare_flag: true,
      cadd_phred: 25.5,
      sift_score: 0.01,
      sift_pred: "D",
      polyphen2_hsvar_score: 0.99,
      polyphen2_hsvar_pred: "D",
      revel: 0.85,
      annotation_coverage: 0b111111,
      evidence_conflict: i === 0,
      ensemble_pathogenic: i === 0,
      chrom_grch38: "1",
      pos_grch38: startPos + i * 100 + 50000,
    })),
    next_cursor_chrom: hasMore ? "1" : null,
    next_cursor_pos: hasMore ? startPos + count * 100 : null,
    has_more: hasMore,
    limit: 100,
  }
}

function makeCountResponse(total: number): VariantCount {
  return { total, filtered: false }
}

const defaultChromCounts: ChromosomeSummary[] = [
  { chrom: "1", count: 50000 },
  { chrom: "2", count: 45000 },
  { chrom: "3", count: 35000 },
  { chrom: "X", count: 10000 },
]

const defaultTags: Tag[] = []

function setupFetchMock(
  page: VariantPage,
  count: VariantCount,
  chromCounts: ChromosomeSummary[] = defaultChromCounts,
  presets: ColumnPreset[] = defaultPresets,
  tags: Tag[] = defaultTags,
) {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/api/column-presets")) {
      return { ok: true, json: async () => ({ presets }) }
    }
    if (url.includes("/api/tags")) {
      return { ok: true, json: async () => tags }
    }
    if (url.includes("/api/variants/chromosomes")) {
      return { ok: true, json: async () => chromCounts }
    }
    if (url.includes("/api/variants/count")) {
      return { ok: true, json: async () => count }
    }
    // The GRCh38 toggle triggers the liftover batch (#2029); without this branch
    // every toggle-on test would exercise a failing POST.
    if (url.includes("/api/liftover/")) {
      return {
        ok: true,
        json: async () => ({ total: 2, converted: 2, failed: 0, already_lifted: 0 }),
      }
    }
    if (url.includes("/api/variants")) {
      return { ok: true, json: async () => page }
    }
    return { ok: false, status: 404 }
  })
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch)
  mockFetch.mockReset()
  // Reset URL params
  window.history.replaceState({}, "", window.location.pathname)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("VariantTable", () => {
  it("shows upload prompt when no sample selected (P1-15e: pre-upload)", () => {
    render(<VariantTable sampleId={null} />)
    expect(screen.getByText("Upload a file to get started")).toBeInTheDocument()
    expect(screen.getByText(/go to the dashboard/i)).toBeInTheDocument()
  })

  it("announces the loading state to screen readers via role=status (#601)", () => {
    setupFetchMock(makeVariantPage(1), makeCountResponse(1))
    // The first synchronous render is the pending state, before the query resolves.
    render(<VariantTable sampleId={1} />)
    const loadingText = screen.getByText("Loading variants...")
    // The indicator sits inside a status live region so SR users are notified...
    const status = loadingText.closest('[role="status"]')
    expect(status).not.toBeNull()
    expect(status).toHaveTextContent("Loading variants...")
    // ...and the decorative spinner is hidden from assistive tech.
    expect(status?.querySelector("svg")).toHaveAttribute("aria-hidden", "true")
  })

  it("renders variant rows from API", async () => {
    const page = makeVariantPage(3)
    setupFetchMock(page, makeCountResponse(3))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    expect(screen.getByText("rs101")).toBeInTheDocument()
    expect(screen.getByText("rs102")).toBeInTheDocument()
    // The genotype column carries a value, not just a header — all three rows
    // are genotype "AG" in this fixture.
    expect(screen.getAllByText("AG")).toHaveLength(3)
  })

  it("renders table tag pills with the configured tag colors and gray fallback (#710)", async () => {
    const page = makeVariantPage(1)
    page.items[0].tags = ["Pathogenic", "Reviewed", "Unknown"]
    setupFetchMock(
      page,
      makeCountResponse(1),
      defaultChromCounts,
      defaultPresets,
      [
        {
          id: 1,
          name: "Pathogenic",
          color: "#dc2626",
          is_predefined: false,
          created_at: null,
          variant_count: 1,
        },
        {
          id: 2,
          name: "Reviewed",
          color: "#16a34a",
          is_predefined: false,
          created_at: null,
          variant_count: 1,
        },
      ],
    )

    render(<VariantTable sampleId={1} />)

    const pathogenic = await screen.findByTitle("Pathogenic")
    const reviewed = screen.getByTitle("Reviewed")
    const unknown = screen.getByTitle("Unknown")

    expect(pathogenic).toHaveStyle({ backgroundColor: "#dc2626" })
    expect(reviewed).toHaveStyle({ backgroundColor: "#16a34a" })
    expect(unknown).toHaveStyle({ backgroundColor: "#6b7280" })
  })

  it("renders readable SIFT and PolyPhen prediction labels instead of raw codes (#1753)", async () => {
    const page = makeVariantPage(5)
    page.items[0].sift_pred = "D"
    page.items[0].polyphen2_hsvar_pred = "D"
    page.items[1].sift_pred = "T"
    page.items[1].polyphen2_hsvar_pred = "P"
    page.items[2].sift_pred = null
    page.items[2].polyphen2_hsvar_pred = "B"
    page.items[3].sift_pred = "   "
    page.items[3].polyphen2_hsvar_pred = "   "
    page.items[4].sift_pred = "UNKNOWN_SIFT"
    page.items[4].polyphen2_hsvar_pred = "UNKNOWN_PP2"
    setupFetchMock(page, makeCountResponse(5))

    render(<VariantTable sampleId={1} />)

    await screen.findByText("rs104")

    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent)
    const predictionCell = (rsid: string, column: "SIFT Pred" | "PP2 Pred") => {
      const row = screen.getByText(rsid, { exact: true }).closest("tr")
      expect(row).not.toBeNull()
      const columnIndex = headers.indexOf(column)
      expect(columnIndex).toBeGreaterThanOrEqual(0)
      return within(row as HTMLTableRowElement).getAllByRole("cell")[columnIndex]
    }

    const expected = [
      ["rs100", "SIFT Pred", "Damaging", "text-red-700"],
      ["rs100", "PP2 Pred", "Probably Damaging", "text-red-700"],
      ["rs101", "SIFT Pred", "Tolerated", "text-green-700"],
      ["rs101", "PP2 Pred", "Possibly Damaging", "text-amber-700"],
      ["rs102", "PP2 Pred", "Benign", "text-green-700"],
    ] as const

    for (const [rsid, column, label, colorClass] of expected) {
      const cell = predictionCell(rsid, column)
      expect(cell).toHaveTextContent(label)
      expect(cell.firstElementChild).toHaveClass(colorClass)
      expect(cell).not.toHaveTextContent(/^(D|T|P|B)$/)
    }

    expect(predictionCell("rs102", "SIFT Pred")).toBeEmptyDOMElement()
    expect(predictionCell("rs103", "SIFT Pred")).toBeEmptyDOMElement()
    expect(predictionCell("rs103", "PP2 Pred")).toBeEmptyDOMElement()

    const unknownSift = predictionCell("rs104", "SIFT Pred")
    expect(unknownSift).toHaveTextContent("UNKNOWN SIFT")
    expect(unknownSift.firstElementChild).toHaveClass("text-muted-foreground")
    const unknownPolyphen = predictionCell("rs104", "PP2 Pred")
    expect(unknownPolyphen).toHaveTextContent("UNKNOWN PP2")
    expect(unknownPolyphen.firstElementChild).toHaveClass("text-muted-foreground")
  })

  it("renders genotype and zygosity for het and hom_alt rows", async () => {
    // A preset that surfaces the zygosity column (the default presets omit it).
    const carriagePreset: ColumnPreset[] = [
      { name: "Carriage", columns: ["genotype", "zygosity", "gene_symbol"], predefined: true },
    ]
    const page = makeVariantPage(2)
    page.items[0] = { ...page.items[0], rsid: "rs_het", genotype: "AG", zygosity: "het" }
    page.items[1] = {
      ...page.items[1],
      rsid: "rs_homalt",
      genotype: "GG",
      ref: "A",
      alt: "G",
      zygosity: "hom_alt",
    }
    setupFetchMock(page, makeCountResponse(2), defaultChromCounts, carriagePreset)

    render(<VariantTable sampleId={1} />)
    await waitFor(() => expect(screen.getByText("rs_het")).toBeInTheDocument())

    // Both carriage branches must render their genotype + zygosity — a het↔hom
    // label inversion (a clinically wrong call) would otherwise be invisible.
    expect(screen.getByText("AG")).toBeInTheDocument()
    expect(screen.getByText("GG")).toBeInTheDocument()
    expect(screen.getByText("het")).toBeInTheDocument()
    expect(screen.getByText("hom_alt")).toBeInTheDocument()
  })

  it("shows async total count", async () => {
    const page = makeVariantPage(5)
    setupFetchMock(page, makeCountResponse(12345))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("12,345 variants")).toBeInTheDocument()
    })
  })

  it("fires count query with annotation_coverage:notnull filter by default (P1-15d)", async () => {
    const page = makeVariantPage(3)
    setupFetchMock(page, makeCountResponse(3))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("3 variants")).toBeInTheDocument()
    })

    // Verify the count endpoint was called with annotation_coverage:notnull
    const countCalls = mockFetch.mock.calls
      .map((c) => c[0] as string)
      .filter((url) => url.includes("/api/variants/count"))
    expect(countCalls.length).toBeGreaterThan(0)
    expect(countCalls[0]).toContain("annotation_coverage%3Anotnull")
  })

  it("fires count query without annotation_coverage filter when showing unannotated (P1-15d)", async () => {
    const page = makeVariantPage(3)
    setupFetchMock(page, makeCountResponse(3))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Toggle unannotated on
    const toggle = screen.getByRole("button", { name: /show unannotated/i })
    await user.click(toggle)

    // After toggling, a new count call should fire without annotation_coverage filter
    await waitFor(() => {
      const countCalls = mockFetch.mock.calls
        .map((c) => c[0] as string)
        .filter((url) => url.includes("/api/variants/count"))
      const callsWithoutFilter = countCalls.filter(
        (url) => !url.includes("annotation_coverage"),
      )
      expect(callsWithoutFilter.length).toBeGreaterThan(0)
    })
  })

  it("displays conflict flag for conflicting variants", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("\u26A0")).toBeInTheDocument()
    })
  })

  it("shows error state on fetch failure", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: false,
      status: 500,
      text: async () => "Internal Server Error",
    }))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("Error loading variants")).toBeInTheDocument()
    })
  })

  it("filters variants by search query (client-side)", async () => {
    const page = makeVariantPage(4)
    setupFetchMock(page, makeCountResponse(4))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    const searchInput = screen.getByPlaceholderText("Search rsid or gene...")
    await user.type(searchInput, "BRCA1")

    // BRCA1 genes are on even indices (rs100, rs102)
    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
      expect(screen.getByText("rs102")).toBeInTheDocument()
    })
    expect(screen.queryByText("rs101")).not.toBeInTheDocument()
    expect(screen.queryByText("rs103")).not.toBeInTheDocument()
  })

  it("has unannotated toggle button", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /show unannotated/i })).toBeInTheDocument()
    })
  })

  it("toggles unannotated visibility on button click", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    const toggle = await waitFor(() =>
      screen.getByRole("button", { name: /show unannotated/i }),
    )

    await user.click(toggle)
    expect(toggle).toHaveAttribute("aria-pressed", "true")

    await user.click(toggle)
    expect(toggle).toHaveAttribute("aria-pressed", "false")
  })

  it("renders table headers", async () => {
    const page = makeVariantPage(1)
    setupFetchMock(page, makeCountResponse(1))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rsID")).toBeInTheDocument()
    })
    expect(screen.getByText("Chr (GRCh37)")).toHaveAttribute(
      "title",
      "Native GRCh37/hg19 coordinate stored for this sample; use these columns with GRCh37/hg19 tools.",
    )
    expect(screen.getByText("Position (GRCh37)")).toHaveAttribute(
      "title",
      "Native GRCh37/hg19 coordinate stored for this sample; use these columns with GRCh37/hg19 tools.",
    )
    expect(screen.getByText("Genotype")).toBeInTheDocument()
    expect(screen.getByText("Gene")).toBeInTheDocument()
    expect(screen.getByText("Consequence")).toBeInTheDocument()
    expect(screen.getByText("ClinVar")).toBeInTheDocument()
  })

  it("shows pre-annotation empty state when sample has only raw variants (P1-15e)", async () => {
    // All variants have annotation_coverage = null → pre-annotation state
    const page: VariantPage = {
      items: [
        {
          rsid: "rs999",
          chrom: "1",
          pos: 1000,
          genotype: "AG",
          ref: null,
          alt: null,
          zygosity: null,
          gene_symbol: null,
          consequence: null,
          clinvar_significance: null,
          clinvar_review_stars: null,
          gnomad_af_global: null,
          rare_flag: null,
          cadd_phred: null,
          sift_score: null,
          sift_pred: null,
          polyphen2_hsvar_score: null,
          polyphen2_hsvar_pred: null,
          revel: null,
          annotation_coverage: null,
          evidence_conflict: null,
          ensemble_pathogenic: null,
          chrom_grch38: null,
          pos_grch38: null,
        },
      ],
      next_cursor_chrom: null,
      next_cursor_pos: null,
      has_more: false,
      limit: 100,
    }

    // Annotated count = 0, but total variants = 1 (raw variants exist)
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets")) {
        return { ok: true, json: async () => ({ presets: defaultPresets }) }
      }
      if (url.includes("/api/variants/chromosomes")) {
        return { ok: true, json: async () => [{ chrom: "1", count: 1 }] }
      }
      if (url.includes("/api/variants/count")) {
        // When filter includes annotation_coverage:notnull → 0 annotated
        if (url.includes("annotation_coverage")) {
          return { ok: true, json: async () => ({ total: 0, filtered: true }) }
        }
        // Unfiltered total → 1
        return { ok: true, json: async () => ({ total: 1, filtered: false }) }
      }
      if (url.includes("/api/variants")) {
        return { ok: true, json: async () => page }
      }
      return { ok: false, status: 404 }
    })

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("Run annotation to see results here")).toBeInTheDocument()
    })
    expect(screen.getByText(/1 variant.* uploaded/i)).toBeInTheDocument()
    expect(screen.getByText("Show raw variants")).toBeInTheDocument()
  })

  it("shows no-match empty state with suggestion buttons (P1-15e)", async () => {
    // Return annotated variants but they'll be filtered out by search
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Type a search that matches nothing
    const searchInput = screen.getByPlaceholderText("Search rsid or gene...")
    await user.type(searchInput, "NONEXISTENT_GENE_XYZ")

    await waitFor(() => {
      expect(screen.getByText("No variants match your filters")).toBeInTheDocument()
    })

    // Should show clear search button and quick-apply suggestions
    expect(screen.getByText("Clear search")).toBeInTheDocument()
    expect(screen.getByText("Pathogenic only")).toBeInTheDocument()
    expect(screen.getByText("Rare variants")).toBeInTheDocument()
  })

  it("shows ClinVar review stars correctly", async () => {
    const page = makeVariantPage(1)
    setupFetchMock(page, makeCountResponse(1))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      // 2 stars = ★★☆☆
      expect(screen.getByText("\u2605\u2605\u2606\u2606")).toBeInTheDocument()
    })
  })

  it("displays gnomAD AF in scientific notation for very small values", async () => {
    const page = makeVariantPage(1)
    // Override the gnomad_af_global to be very small
    page.items[0].gnomad_af_global = 0.00001
    setupFetchMock(page, makeCountResponse(1))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("1.00e-5")).toBeInTheDocument()
    })
  })

  it("labels source-uncovered gnomAD rows as not assessed", async () => {
    const page = makeVariantPage(1)
    page.items[0].gnomad_af_global = null
    page.items[0].gnomad_source_status = "source_uncovered"
    setupFetchMock(page, makeCountResponse(1))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("Not assessed")).toBeInTheDocument()
    })
  })

  // #2214 review: the helper-level specs pass even if a consumer stops calling
  // the helper, which would silently restore the blank cell. These assert
  // through the RENDERED table so a dropped call is caught where it matters.
  it.each([
    ["allele_ambiguous", "Allele unresolved"],
    ["locus_unresolved", "Position unmatched"],
    ["alias_unresolved", "Shared rsID"],
    ["allele_mismatch", "Other allele"],
  ])("renders %s as %s instead of a blank AF cell", async (status, label) => {
    const page = makeVariantPage(1)
    page.items[0].gnomad_af_global = null
    page.items[0].gnomad_source_status = status
    setupFetchMock(page, makeCountResponse(1))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText(label)).toBeInTheDocument()
    })
  })
})

describe("VariantToolbar", () => {
  it("has search input with correct aria-label", async () => {
    const page = makeVariantPage(1)
    setupFetchMock(page, makeCountResponse(1))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: "Search variants by rsid or gene" }),
      ).toBeInTheDocument()
    })
  })

  it("has Conflicts only toggle button (P2-22)", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /show conflicts only/i })).toBeInTheDocument()
    })
  })

  it("toggles Conflicts only on/off (P2-22)", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    const toggle = await waitFor(() =>
      screen.getByRole("button", { name: /show conflicts only/i }),
    )

    expect(toggle).toHaveAttribute("aria-pressed", "false")

    await user.click(toggle)
    expect(toggle).toHaveAttribute("aria-pressed", "true")

    await user.click(toggle)
    expect(toggle).toHaveAttribute("aria-pressed", "false")
  })

  it("sends evidence_conflict:1 filter when Conflicts only is active (P2-22)", async () => {
    const page = makeVariantPage(3)
    setupFetchMock(page, makeCountResponse(3))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Activate conflicts only toggle
    const toggle = screen.getByRole("button", { name: /show conflicts only/i })
    await user.click(toggle)

    // Verify evidence_conflict:1 is in the fetch filter
    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c) => c[0] as string)
      const variantCalls = calls.filter(
        (url) => url.includes("/api/variants?") && !url.includes("count") && !url.includes("chromosomes"),
      )
      const conflictCall = variantCalls.find(
        (url) => url.includes("evidence_conflict%3A1") || url.includes("evidence_conflict:1"),
      )
      expect(conflictCall).toBeDefined()
    })
  })
})

describe("Evidence conflict indicator (P2-22)", () => {
  it("renders amber conflict indicator with title", async () => {
    const page = makeVariantPage(2) // index 0 has evidence_conflict: true
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      const indicator = screen.getByLabelText("Evidence conflict")
      expect(indicator).toBeInTheDocument()
      expect(indicator).toHaveAttribute("title", "Evidence conflict: ClinVar disagrees with in-silico predictions")
      expect(indicator).toHaveClass("text-amber-500")
    })
  })

  it("annotation columns visible per selected preset (P2-22)", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Switch to Research preset — should show CADD, SIFT, etc.
    await user.click(screen.getByRole("button", { name: "Column presets" }))
    await waitFor(() => expect(screen.getByRole("menu")).toBeInTheDocument())
    await user.click(screen.getByRole("menuitem", { name: /Research/ }))

    await waitFor(() => {
      expect(screen.getByText("CADD")).toBeInTheDocument()
      expect(screen.getByText("SIFT")).toBeInTheDocument()
      expect(screen.getByText("REVEL")).toBeInTheDocument()
      expect(screen.getByText("Ensemble")).toBeInTheDocument()
    })
    // gnomAD AF should NOT be visible in Research preset
    expect(screen.queryByText("gnomAD AF")).not.toBeInTheDocument()
  })
})

describe("ChromosomeNav (P1-15b)", () => {
  it("renders chromosome navigation bar", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("toolbar", { name: "Chromosome navigation" })).toBeInTheDocument()
    })
  })

  it("shows chromosome buttons with variant counts in title", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      const chr1Button = screen.getByRole("button", { name: /^jump to chromosome 1,/i })
      expect(chr1Button).toBeInTheDocument()
      expect(chr1Button).toHaveAttribute("title", "Chromosome 1: 50,000 variants")
    })
  })

  it("disables chromosomes with no data", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      // Chromosome 4 is not in our mock data, should be disabled
      const chr4Button = screen.getByRole("button", { name: /^jump to chromosome 4$/i })
      expect(chr4Button).toBeDisabled()
    })
  })

  it("highlights the active chromosome", async () => {
    const page = makeVariantPage(2) // all items on chrom "1"
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      const chr1Button = screen.getByRole("button", { name: /^jump to chromosome 1,/i })
      expect(chr1Button).toHaveAttribute("aria-current", "location")
    })
  })

  it("triggers chromosome jump on click", async () => {
    const page = makeVariantPage(3)
    setupFetchMock(page, makeCountResponse(3))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Click chromosome X to jump
    const chrXButton = screen.getByRole("button", { name: /^jump to chromosome x,/i })
    await user.click(chrXButton)

    // After clicking, the fetch should have been called with cursor params for chr X
    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c) => c[0] as string)
      const variantCalls = calls.filter(
        (url) => url.includes("/api/variants?") && !url.includes("count") && !url.includes("chromosomes"),
      )
      // Should have a call with cursor_chrom=X&cursor_pos=0
      const jumpCall = variantCalls.find(
        (url) => url.includes("cursor_chrom=X") && url.includes("cursor_pos=0"),
      )
      expect(jumpCall).toBeDefined()
    })
  })

  it("does not render chromosome nav when no sample selected", () => {
    render(<VariantTable sampleId={null} />)
    expect(screen.queryByRole("toolbar", { name: "Chromosome navigation" })).not.toBeInTheDocument()
  })
})

describe("ColumnPresets (P1-15c)", () => {
  it("renders preset selector in toolbar", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Column presets" })).toBeInTheDocument()
    })
    // Default label
    expect(screen.getByText("All Columns")).toBeInTheDocument()
  })

  it("opens dropdown with predefined presets", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Column presets" })).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: "Column presets" }))

    await waitFor(() => {
      expect(screen.getByRole("menu")).toBeInTheDocument()
    })
    expect(screen.getByRole("menuitem", { name: /Clinical/ })).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: /Research/ })).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: /Frequency/ })).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: /Scores/ })).toBeInTheDocument()
  })

  it("switching preset hides non-preset columns", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    // Wait for data to load
    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // CADD header should be visible initially (All Columns)
    expect(screen.getByText("CADD")).toBeInTheDocument()

    // Click preset button and select Clinical
    await user.click(screen.getByRole("button", { name: "Column presets" }))
    await waitFor(() => {
      expect(screen.getByRole("menu")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("menuitem", { name: /Clinical/ }))

    // CADD should be hidden (not in Clinical preset)
    await waitFor(() => {
      expect(screen.queryByText("CADD")).not.toBeInTheDocument()
    })
    // Gene should still be visible (in Clinical preset)
    expect(screen.getByText("Gene")).toBeInTheDocument()
    // rsID always visible
    expect(screen.getByText("rsID")).toBeInTheDocument()
  })

  it("All Columns shows all columns after switching away and back", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Switch to Clinical (hides CADD)
    await user.click(screen.getByRole("button", { name: "Column presets" }))
    await waitFor(() => expect(screen.getByRole("menu")).toBeInTheDocument())
    await user.click(screen.getByRole("menuitem", { name: /Clinical/ }))

    await waitFor(() => {
      expect(screen.queryByText("CADD")).not.toBeInTheDocument()
    })

    // Switch back to All Columns
    await user.click(screen.getByRole("button", { name: "Column presets" }))
    await waitFor(() => expect(screen.getByRole("menu")).toBeInTheDocument())
    await user.click(screen.getByRole("menuitem", { name: /All Columns/ }))

    await waitFor(() => {
      expect(screen.getByText("CADD")).toBeInTheDocument()
    })
  })

  it("updates URL param when preset is selected", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: "Column presets" }))
    await waitFor(() => expect(screen.getByRole("menu")).toBeInTheDocument())
    await user.click(screen.getByRole("menuitem", { name: /Frequency/ }))

    await waitFor(() => {
      const params = new URLSearchParams(window.location.search)
      expect(params.get("profile")).toBe("frequency")
    })
  })
})

describe("GRCh38 liftover toggle (P4-20)", () => {
  it("renders GRCh38 toggle button in toolbar", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      const toggle = screen.getByRole("button", { name: /show grch38 coordinates/i })
      expect(toggle).toBeInTheDocument()
      expect(toggle).toHaveAttribute(
        "title",
        "Show computational GRCh38/hg38 liftover columns. Default coordinate columns are native GRCh37/hg19. GRCh38 coordinates are computed the first time you enable this, which takes a few seconds; afterwards a blank cell means that position could not be lifted over, as MT/mitochondrial variants never are.",
      )
      expect(toggle).toHaveAttribute("aria-describedby", "variant-table-grch38-toggle-help")
      expect(
        screen.getByText(
          "Show computational GRCh38/hg38 liftover columns. Default coordinate columns are native GRCh37/hg19. GRCh38 coordinates are computed the first time you enable this, which takes a few seconds; afterwards a blank cell means that position could not be lifted over, as MT/mitochondrial variants never are.",
        ),
      ).toHaveClass("sr-only")
    })
  })

  // #2029: the columns these tests assert were blank for 100% of variants of
  // every sample, because POST /api/liftover/{sample_id} — the only thing that
  // fills them — had no caller anywhere in the app. Every test above passed
  // throughout: they check that the columns and their labels *render*, which is
  // true of an empty column. What was missing was any assertion that something
  // asks for the data.
  const liftoverCalls = () =>
    mockFetch.mock.calls.filter(
      ([url, init]) =>
        typeof url === "string" &&
        url.includes("/api/liftover/") &&
        (init as RequestInit | undefined)?.method === "POST",
    )

  it("requests the liftover batch when the toggle is switched on", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    // Nothing should be computed until the user asks to see the columns.
    expect(liftoverCalls()).toHaveLength(0)

    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))

    await waitFor(() => {
      expect(liftoverCalls()).toHaveLength(1)
    })
    expect(liftoverCalls()[0][0]).toBe("/api/liftover/1")
  })

  it("does not re-request the batch when the toggle is cycled", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    const toggle = screen.getByRole("button", { name: /show grch38 coordinates/i })
    await user.click(toggle)
    await waitFor(() => expect(liftoverCalls()).toHaveLength(1))

    await user.click(toggle) // off — must not trigger anything
    await user.click(toggle) // on again — already computed for this sample

    await waitFor(() => {
      expect(screen.getByText("Chr (GRCh38)")).toBeInTheDocument()
    })
    expect(liftoverCalls()).toHaveLength(1)
  })

  it("requests the batch for the new sample when the sample changes with columns on", async () => {
    // VariantExplorer renders <VariantTable sampleId={…}/> without a key, so a
    // sample change re-renders the same mounted component with showGRCh38 still
    // true. A trigger living in the click handler never fires here and sample
    // B's columns stay blank — the exact defect #2029 reports, one sample over.
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    const { rerender } = render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))
    await waitFor(() => expect(liftoverCalls()).toHaveLength(1))
    expect(liftoverCalls()[0][0]).toBe("/api/liftover/1")

    rerender(<VariantTable sampleId={2} />)

    await waitFor(() => {
      expect(liftoverCalls()).toHaveLength(2)
    })
    expect(liftoverCalls()[1][0]).toBe("/api/liftover/2")
  })

  it("does not re-request a sample already lifted this mount when returning to it", async () => {
    // A → B → A. A set rather than a single latched id, so coming back to A
    // does not spend another round trip on work already done.
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    const { rerender } = render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))
    await waitFor(() => expect(liftoverCalls()).toHaveLength(1))

    rerender(<VariantTable sampleId={2} />)
    await waitFor(() => expect(liftoverCalls()).toHaveLength(2))

    rerender(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByText("Chr (GRCh38)")).toBeInTheDocument()
    })
    expect(liftoverCalls()).toHaveLength(2)
  })

  it("re-requests a sample whose batch failed after the user switched away", async () => {
    // TanStack Query gives a component one mutation observer: a second mutate()
    // detaches it from the first request, and callbacks passed to that first
    // mutate() then never run. Cleanup therefore lives on the mutation itself.
    // Without that, sample 1's failure never clears its "already requested"
    // latch, and coming back to sample 1 shows permanently blank GRCh38 columns
    // with no failure notice and no Retry.
    const page = makeVariantPage(2)
    let failSampleOne!: () => void
    const sampleOneResponse = new Promise<unknown>((resolve) => {
      failSampleOne = () =>
        resolve({ ok: false, status: 503, json: async () => ({ detail: "unavailable" }) })
    })
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets"))
        return { ok: true, json: async () => ({ presets: defaultPresets }) }
      if (url.includes("/api/tags")) return { ok: true, json: async () => defaultTags }
      if (url.includes("/api/variants/chromosomes"))
        return { ok: true, json: async () => defaultChromCounts }
      if (url.includes("/api/variants/count"))
        return { ok: true, json: async () => makeCountResponse(2) }
      // Sample 1's batch stays in flight until the test fails it; sample 2's
      // succeeds immediately, so the observer moves on while 1 is unresolved.
      if (url === "/api/liftover/1") return await sampleOneResponse
      if (url.includes("/api/liftover/"))
        return {
          ok: true,
          json: async () => ({ total: 2, converted: 2, failed: 0, already_lifted: 0 }),
        }
      if (url.includes("/api/variants")) return { ok: true, json: async () => page }
      return { ok: false, status: 404 }
    })

    const user = userEvent.setup()
    const { rerender } = render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))
    await waitFor(() => expect(liftoverCalls()).toHaveLength(1))
    expect(liftoverCalls()[0][0]).toBe("/api/liftover/1")

    // Switch to sample 2 while sample 1's request is still open.
    rerender(<VariantTable sampleId={2} />)
    await waitFor(() => expect(liftoverCalls()).toHaveLength(2))
    expect(liftoverCalls()[1][0]).toBe("/api/liftover/2")

    // Now let sample 1's request fail, with the observer attached to sample 2.
    await act(async () => {
      failSampleOne()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    rerender(<VariantTable sampleId={1} />)

    await waitFor(() => expect(liftoverCalls()).toHaveLength(3))
    expect(liftoverCalls()[2][0]).toBe("/api/liftover/1")
    // …and that retry's failure is reported against the sample on screen.
    await waitFor(() => {
      expect(screen.getByText(/could not be computed/i)).toBeInTheDocument()
    })
  })

  it("does not report one sample's failure against another sample", async () => {
    // 2 → 1 → 2. The mutation observer holds a single isError, and returning to
    // an already-computed sample starts no new request to clear it. Read
    // straight off the observer, sample 1's failure therefore keeps claiming
    // sample 2's coordinates are broken — the inverse misreport, and just as
    // wrong: sample 2's GRCh38 columns were computed and are correct.
    const page = makeVariantPage(2)
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets"))
        return { ok: true, json: async () => ({ presets: defaultPresets }) }
      if (url.includes("/api/tags")) return { ok: true, json: async () => defaultTags }
      if (url.includes("/api/variants/chromosomes"))
        return { ok: true, json: async () => defaultChromCounts }
      if (url.includes("/api/variants/count"))
        return { ok: true, json: async () => makeCountResponse(2) }
      if (url === "/api/liftover/1")
        return { ok: false, status: 503, json: async () => ({ detail: "unavailable" }) }
      if (url.includes("/api/liftover/"))
        return {
          ok: true,
          json: async () => ({ total: 2, converted: 2, failed: 0, already_lifted: 0 }),
        }
      if (url.includes("/api/variants")) return { ok: true, json: async () => page }
      return { ok: false, status: 404 }
    })

    const user = userEvent.setup()
    const { rerender } = render(<VariantTable sampleId={2} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))
    await waitFor(() => expect(liftoverCalls()).toHaveLength(1))
    expect(liftoverCalls()[0][0]).toBe("/api/liftover/2")

    rerender(<VariantTable sampleId={1} />)
    await waitFor(() => {
      expect(screen.getByText(/could not be computed/i)).toBeInTheDocument()
    })

    // Back to the sample that succeeded. It is already computed, so no new
    // request runs and nothing resets a shared error flag.
    rerender(<VariantTable sampleId={2} />)

    await waitFor(() => {
      expect(screen.queryByText(/could not be computed/i)).not.toBeInTheDocument()
    })
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
    expect(liftoverCalls()).toHaveLength(2)
  })

  it("stays in the computing state until the refreshed coordinates arrive", async () => {
    // The POST finishing is not the end of the operation: the table is still
    // rendering the variant pages fetched *before* the batch wrote the columns,
    // so its GRCh38 cells are the pre-liftover NULLs. Clearing "Computing" when
    // the POST resolves therefore presents those blanks as final — which the
    // tooltip defines as "this position could not be lifted over" — for as long
    // as the refetch takes.
    const stalePage = makeVariantPage(1)
    stalePage.items[0].chrom_grch38 = null
    stalePage.items[0].pos_grch38 = null
    const refreshedPage = makeVariantPage(1) // pos_grch38 = 51,000

    let variantPageCalls = 0
    let releaseRefetch!: () => void
    const refetched = new Promise<unknown>((resolve) => {
      releaseRefetch = () => resolve({ ok: true, json: async () => refreshedPage })
    })
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets"))
        return { ok: true, json: async () => ({ presets: defaultPresets }) }
      if (url.includes("/api/tags")) return { ok: true, json: async () => defaultTags }
      if (url.includes("/api/variants/chromosomes"))
        return { ok: true, json: async () => defaultChromCounts }
      if (url.includes("/api/variants/count"))
        return { ok: true, json: async () => makeCountResponse(1) }
      if (url.includes("/api/liftover/"))
        return {
          ok: true,
          json: async () => ({ total: 1, converted: 1, failed: 0, already_lifted: 0 }),
        }
      if (url.includes("/api/variants")) {
        variantPageCalls += 1
        // The first fetch is the pre-liftover page; the second is the refetch
        // the batch's invalidation triggers, held open by this test.
        return variantPageCalls > 1 ? await refetched : { ok: true, json: async () => stalePage }
      }
      return { ok: false, status: 404 }
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => expect(screen.getByText("rs100")).toBeInTheDocument())
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))

    // The POST has resolved and the refetch is in flight but unresolved.
    await waitFor(() => expect(liftoverCalls()).toHaveLength(1))
    await waitFor(() => expect(variantPageCalls).toBeGreaterThan(1))

    // The stale blanks are still on screen, so the operation must still read as
    // in progress.
    expect(screen.getByText(/Computing GRCh38 coordinates/i)).toBeInTheDocument()
    expect(screen.queryByText("51,000")).not.toBeInTheDocument()

    await act(async () => {
      releaseRefetch()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    // Only once the refreshed rows land does the operation report as finished.
    await waitFor(() => expect(screen.getByText("51,000")).toBeInTheDocument())
    expect(screen.queryByText(/Computing GRCh38 coordinates/i)).not.toBeInTheDocument()
  })

  it("re-requests the batch for a sample whose post-liftover refresh failed", async () => {
    // A refetch that never delivers the coordinates must not be recorded as a
    // completed liftover. It is not a liftover failure either — the batch
    // stored the coordinates — so it is not labelled one; what it must not do
    // is leave the sample latched, which would make the blank columns
    // permanent for this mount.
    const page = makeVariantPage(2)
    let sampleOnePageCalls = 0
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets"))
        return { ok: true, json: async () => ({ presets: defaultPresets }) }
      if (url.includes("/api/tags")) return { ok: true, json: async () => defaultTags }
      if (url.includes("/api/variants/chromosomes"))
        return { ok: true, json: async () => defaultChromCounts }
      if (url.includes("/api/variants/count"))
        return { ok: true, json: async () => makeCountResponse(2) }
      if (url.includes("/api/liftover/"))
        return {
          ok: true,
          json: async () => ({ total: 2, converted: 2, failed: 0, already_lifted: 0 }),
        }
      if (url.includes("/api/variants")) {
        if (url.includes("sample_id=1")) {
          sampleOnePageCalls += 1
          // Second call = the refetch the batch's invalidation triggers.
          if (sampleOnePageCalls === 2) {
            return { ok: false, status: 500, text: async () => "Internal Server Error" }
          }
        }
        return { ok: true, json: async () => page }
      }
      return { ok: false, status: 404 }
    })

    const user = userEvent.setup()
    const { rerender } = render(<VariantTable sampleId={1} />)

    await waitFor(() => expect(screen.getByText("rs100")).toBeInTheDocument())
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))
    await waitFor(() => expect(liftoverCalls()).toHaveLength(1))

    // The failed refresh surfaces as the variant query's own error state — the
    // user is not left looking at stale blanks dressed up as final values.
    await waitFor(() => {
      expect(screen.getByText("Error loading variants")).toBeInTheDocument()
    })

    rerender(<VariantTable sampleId={2} />)
    await waitFor(() => expect(liftoverCalls()).toHaveLength(2))

    // Returning re-runs the batch, because the first attempt never confirmed a
    // refreshed table. (The batch is idempotent: the retry is an
    // already_lifted no-op that re-triggers the refetch.)
    rerender(<VariantTable sampleId={1} />)
    await waitFor(() => expect(liftoverCalls()).toHaveLength(3))
    expect(liftoverCalls()[2][0]).toBe("/api/liftover/1")
  })

  it("surfaces a liftover failure instead of presenting blank cells as final", async () => {
    // The tooltip now tells users a blank GRCh38 cell means the position could
    // not be lifted over. That is only true once the batch has run — so if it
    // fails, silently showing empty columns misreports a failed computation as
    // an unmappable genome position.
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets"))
        return { ok: true, json: async () => ({ presets: defaultPresets }) }
      if (url.includes("/api/tags")) return { ok: true, json: async () => defaultTags }
      if (url.includes("/api/variants/chromosomes"))
        return { ok: true, json: async () => defaultChromCounts }
      if (url.includes("/api/variants/count"))
        return { ok: true, json: async () => makeCountResponse(2) }
      if (url.includes("/api/liftover/"))
        return { ok: false, status: 503, json: async () => ({ detail: "unavailable" }) }
      if (url.includes("/api/variants")) return { ok: true, json: async () => page }
      return { ok: false, status: 404 }
    })

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))

    await waitFor(() => {
      expect(screen.getByText(/could not be computed/i)).toBeInTheDocument()
    })
    // …and the user can retry without knowing to cycle the toggle.
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
  })

  it("GRCh38 columns are hidden by default", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    expect(screen.queryByText("Chr (GRCh38)")).not.toBeInTheDocument()
    expect(screen.queryByText("Pos (GRCh38)")).not.toBeInTheDocument()
  })

  it("shows GRCh38 columns when toggle is activated", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    const toggle = screen.getByRole("button", { name: /show grch38 coordinates/i })
    await user.click(toggle)

    await waitFor(() => {
      expect(screen.getByText("Chr (GRCh38)")).toHaveAttribute(
        "title",
        "Computational GRCh38/hg38 liftover from the native GRCh37 coordinate, computed when the GRCh38 toggle is first enabled. Once computed, blank means the position could not be lifted over, including MT/mitochondrial variants, which are never lifted.",
      )
      expect(screen.getByText("Pos (GRCh38)")).toHaveAttribute(
        "title",
        "Computational GRCh38/hg38 liftover from the native GRCh37 coordinate, computed when the GRCh38 toggle is first enabled. Once computed, blank means the position could not be lifted over, including MT/mitochondrial variants, which are never lifted.",
      )
    })
    expect(toggle).toHaveAttribute("aria-pressed", "true")
  })

  it("hides GRCh38 columns when toggle is deactivated", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    const toggle = screen.getByRole("button", { name: /show grch38 coordinates/i })
    await user.click(toggle)

    await waitFor(() => {
      expect(screen.getByText("Chr (GRCh38)")).toBeInTheDocument()
    })

    await user.click(toggle)

    await waitFor(() => {
      expect(screen.queryByText("Chr (GRCh38)")).not.toBeInTheDocument()
      expect(screen.queryByText("Pos (GRCh38)")).not.toBeInTheDocument()
    })
    expect(toggle).toHaveAttribute("aria-pressed", "false")
  })

  it("GRCh38 columns remain visible after switching presets", async () => {
    const page = makeVariantPage(2)
    setupFetchMock(page, makeCountResponse(2))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Enable GRCh38
    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))
    await waitFor(() => {
      expect(screen.getByText("Chr (GRCh38)")).toBeInTheDocument()
    })

    // Switch to Clinical preset
    await user.click(screen.getByRole("button", { name: "Column presets" }))
    await waitFor(() => expect(screen.getByRole("menu")).toBeInTheDocument())
    await user.click(screen.getByRole("menuitem", { name: /Clinical/ }))

    // GRCh38 columns should still be visible
    await waitFor(() => {
      expect(screen.getByText("Chr (GRCh38)")).toBeInTheDocument()
      expect(screen.getByText("Pos (GRCh38)")).toBeInTheDocument()
    })
  })

  it("displays lifted coordinate values in GRCh38 columns", async () => {
    const page = makeVariantPage(1)
    setupFetchMock(page, makeCountResponse(1))

    const user = userEvent.setup()
    render(<VariantTable sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: /show grch38 coordinates/i }))

    await waitFor(() => {
      // pos_grch38 = 1000 + 0 * 100 + 50000 = 51000
      expect(screen.getByText("51,000")).toBeInTheDocument()
    })
  })
})

describe("Unannotated count badge (#1978)", () => {
  // The "/api/variants/count" endpoint is filter-aware: the badge's own hook
  // asks for `annotation_coverage:null` (unannotated), the visible-rows count
  // asks for `annotation_coverage:notnull` (annotated), and the unfiltered call
  // is the sample total. Return a distinct number for each so a badge wired to
  // the wrong one is caught.
  function setupCountMock({
    total,
    annotated,
    unannotated,
  }: {
    total: number
    annotated: number
    unannotated: number
  }) {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/column-presets")) {
        return { ok: true, json: async () => ({ presets: defaultPresets }) }
      }
      if (url.includes("/api/tags")) {
        return { ok: true, json: async () => defaultTags }
      }
      if (url.includes("/api/variants/chromosomes")) {
        return { ok: true, json: async () => defaultChromCounts }
      }
      if (url.includes("/api/variants/count")) {
        if (url.includes("annotation_coverage%3Anotnull")) {
          return { ok: true, json: async () => ({ total: annotated, filtered: true }) }
        }
        if (url.includes("annotation_coverage%3Anull")) {
          return { ok: true, json: async () => ({ total: unannotated, filtered: true }) }
        }
        return { ok: true, json: async () => ({ total, filtered: false }) }
      }
      if (url.includes("/api/variants")) {
        return { ok: true, json: async () => makeVariantPage(2) }
      }
      return { ok: false, status: 404 }
    })
  }

  it("shows the unannotated count, not the sample total", async () => {
    // total 100, of which 7 are unannotated. The badge must read 7 — the bug
    // was that it read the total (100), so every fully-annotated sample claimed
    // its entire variant set was unannotated.
    setupCountMock({ total: 100, annotated: 93, unannotated: 7 })

    render(<VariantTable sampleId={1} />)

    const toggle = await waitFor(() =>
      screen.getByRole("button", { name: /show unannotated/i }),
    )
    await waitFor(() => expect(toggle).toHaveTextContent("Show unannotated (7)"))
    expect(toggle).not.toHaveTextContent("(100)")

    // The badge count must come from an IS-NULL count query (annotation_coverage:null),
    // not the unfiltered total — so it stays correct under an active view filter.
    const nullCountCall = mockFetch.mock.calls
      .map((c) => c[0] as string)
      .find((url) => url.includes("/api/variants/count") && url.includes("annotation_coverage%3Anull"))
    expect(nullCountCall).toBeDefined()
  })

  it("reads (0) and disables the toggle for a fully-annotated sample", async () => {
    // The #1978 scenario: 677,436 variants, all annotated. The badge must say 0
    // and the toggle must be disabled — toggling would reveal nothing.
    setupCountMock({ total: 677436, annotated: 677436, unannotated: 0 })

    render(<VariantTable sampleId={1} />)

    const toggle = await waitFor(() =>
      screen.getByRole("button", { name: /show unannotated/i }),
    )
    await waitFor(() => expect(toggle).toHaveTextContent("Show unannotated (0)"))
    expect(toggle).not.toHaveTextContent("(677,436)")
    expect(toggle).toBeDisabled()
  })

  it("keeps the toggle enabled when there are unannotated variants", async () => {
    setupCountMock({ total: 100, annotated: 93, unannotated: 7 })

    render(<VariantTable sampleId={1} />)

    const toggle = await waitFor(() =>
      screen.getByRole("button", { name: /show unannotated/i }),
    )
    await waitFor(() => expect(toggle).toHaveTextContent("(7)"))
    expect(toggle).toBeEnabled()
  })
})
