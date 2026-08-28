/**
 * @vitest-environment happy-dom
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@/test/test-utils"
import userEvent from "@testing-library/user-event"
import CommandPalette from "./CommandPalette"
import { isGenomicQuery } from "@/lib/genomic-query"

const mockNavigate = vi.fn()
const mockSetSearchParams = vi.fn()
const mockSearchParams = new URLSearchParams("sample_id=1")

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom")
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
  }
})

vi.mock("@/api/variants", () => ({
  useVariantSearch: (sampleId: number | null, query: string) => {
    if (sampleId && query.startsWith("rs42")) {
      return {
        data: [
          { rsid: "rs429358", chrom: "19", pos: 44908684, gene_symbol: "APOE", clinvar_significance: "risk_factor" },
          { rsid: "rs4244285", chrom: "10", pos: 96541616, gene_symbol: "CYP2C19", clinvar_significance: null },
        ],
      }
    }
    return { data: undefined }
  },
}))

vi.mock("@/api/samples", () => ({
  useSamples: () => ({
    data: [
      { id: 1, name: "Sample A", file_format: "23andme_v5", created_at: "2024-01-01" },
      { id: 2, name: "Sample B", file_format: "23andme_v5", created_at: "2024-02-01" },
    ],
  }),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe("isGenomicQuery", () => {
  it("matches rsids", () => {
    expect(isGenomicQuery("rs123")).toBe(true)
    expect(isGenomicQuery("rs429358")).toBe(true)
    expect(isGenomicQuery("RS123")).toBe(true)
  })

  it("matches genomic coordinates", () => {
    expect(isGenomicQuery("chr17:41196312-41277500")).toBe(true)
    expect(isGenomicQuery("chr1:12345")).toBe(true)
    expect(isGenomicQuery("17:41196312-41277500")).toBe(true)
    expect(isGenomicQuery("1:100000")).toBe(true)
    expect(isGenomicQuery("chrX:12345")).toBe(true)
    expect(isGenomicQuery("Y:6789-9999")).toBe(true)
    expect(isGenomicQuery("chrM:100")).toBe(true)
    expect(isGenomicQuery("chrMT:200-300")).toBe(true)
  })

  it("matches gene symbols", () => {
    expect(isGenomicQuery("BRCA1")).toBe(true)
    expect(isGenomicQuery("TP53")).toBe(true)
    expect(isGenomicQuery("MTHFR")).toBe(true)
    expect(isGenomicQuery("APOE")).toBe(true)
  })

  it("rejects empty and numeric-only inputs", () => {
    expect(isGenomicQuery("")).toBe(false)
    expect(isGenomicQuery("  ")).toBe(false)
    expect(isGenomicQuery("123")).toBe(false)
  })

  it("rejects page-like search terms (mixed case / lowercase)", () => {
    expect(isGenomicQuery("Dashboard")).toBe(false)
    expect(isGenomicQuery("Pharmacogenomics")).toBe(false)
    expect(isGenomicQuery("Settings")).toBe(false)
    expect(isGenomicQuery("settings")).toBe(false)
  })
})

describe("CommandPalette", () => {
  it("renders nothing when closed", () => {
    render(<CommandPalette open={false} onOpenChange={vi.fn()} />)
    expect(screen.queryByTestId("command-palette-input")).not.toBeInTheDocument()
  })

  it("renders input and pages when open", () => {
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)
    expect(screen.getByTestId("command-palette-input")).toBeInTheDocument()
    expect(screen.getByText("Dashboard")).toBeInTheDocument()
    expect(screen.getByText("Variant Explorer")).toBeInTheDocument()
    expect(screen.getByText("Genome Browser")).toBeInTheDocument()
  })

  it("shows all module pages including new ones", () => {
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)
    expect(screen.getByText("Fitness")).toBeInTheDocument()
    expect(screen.getByText("Sleep")).toBeInTheDocument()
    expect(screen.getByText("Methylation")).toBeInTheDocument()
    expect(screen.getByText("Skin")).toBeInTheDocument()
    expect(screen.getByText("Allergy")).toBeInTheDocument()
    expect(screen.getByText("Traits & Personality")).toBeInTheDocument()
    expect(screen.getByText("Gene Health")).toBeInTheDocument()
    expect(screen.getByText("Query Builder")).toBeInTheDocument()
  })

  it("navigates to page on selection", async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<CommandPalette open={true} onOpenChange={onOpenChange} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "Variant Explorer")

    // Find the item inside the cmdk list (role="option")
    const item = screen.getByRole("option", { name: /Variant Explorer/i })
    await user.click(item)

    const target = mockNavigate.mock.calls.at(-1)?.[0]
    const url = new URL(target, "http://localhost")
    expect(url.pathname).toBe("/variants")
    expect(url.searchParams.get("sample_id")).toBe("1")
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("shows IGV navigation item for gene queries", async () => {
    const user = userEvent.setup()
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "BRCA1")

    await waitFor(() => {
      expect(screen.getByTestId("command-palette-igv-item")).toBeInTheDocument()
    })
    expect(screen.getByText(/Jump to/)).toBeInTheDocument()
    expect(screen.getByText("BRCA1")).toBeInTheDocument()
  })

  it("shows IGV navigation item for rsid queries", async () => {
    const user = userEvent.setup()
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "rs429358")

    await waitFor(() => {
      expect(screen.getByTestId("command-palette-igv-item")).toBeInTheDocument()
    })
  })

  it("shows IGV navigation item for coordinate queries", async () => {
    const user = userEvent.setup()
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "chr17:41196312-41277500")

    await waitFor(() => {
      expect(screen.getByTestId("command-palette-igv-item")).toBeInTheDocument()
    })
  })

  it("navigates to genome browser on IGV item selection", async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<CommandPalette open={true} onOpenChange={onOpenChange} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "BRCA1")

    await waitFor(() => {
      expect(screen.getByTestId("command-palette-igv-item")).toBeInTheDocument()
    })

    await user.click(screen.getByTestId("command-palette-igv-item"))

    const target = mockNavigate.mock.calls.at(-1)?.[0]
    const url = new URL(target, "http://localhost")
    expect(url.pathname).toBe("/genome-browser")
    expect(url.searchParams.get("locus")).toBe("BRCA1")
    expect(url.searchParams.get("sample_id")).toBe("1")
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("does not show IGV item for non-genomic search", async () => {
    const user = userEvent.setup()
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "Settings")

    expect(screen.queryByTestId("command-palette-igv-item")).not.toBeInTheDocument()
  })

  it("filters pages by search text", async () => {
    const user = userEvent.setup()
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "Pharma")

    await waitFor(() => {
      expect(screen.getByText("Pharmacogenomics")).toBeInTheDocument()
    })
    // Other pages should be filtered out by cmdk
    expect(screen.queryByText("Nutrigenomics")).not.toBeInTheDocument()
  })

  it("calls onOpenChange(false) when overlay is clicked", async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<CommandPalette open={true} onOpenChange={onOpenChange} />)

    // The overlay is the div with data-cmdk-overlay
    const overlay = document.querySelector("[data-cmdk-overlay]")
    expect(overlay).toBeTruthy()
    await user.click(overlay!)

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("shows variant search results for rsid prefix", async () => {
    const user = userEvent.setup()
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "rs42")

    await waitFor(() => {
      const items = screen.getAllByTestId("command-palette-variant-item")
      expect(items.length).toBe(2)
    })
    expect(screen.getByText("rs429358")).toBeInTheDocument()
    expect(screen.getByText("APOE")).toBeInTheDocument()
    expect(screen.getByText("risk_factor")).toBeInTheDocument()
  })

  it("navigates to variant detail on variant selection", async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<CommandPalette open={true} onOpenChange={onOpenChange} />)

    const input = screen.getByTestId("command-palette-input")
    await user.type(input, "rs42")

    await waitFor(() => {
      expect(screen.getAllByTestId("command-palette-variant-item").length).toBeGreaterThan(0)
    })

    const items = screen.getAllByTestId("command-palette-variant-item")
    await user.click(items[0])

    const target = mockNavigate.mock.calls.at(-1)?.[0]
    const url = new URL(target, "http://localhost")
    expect(url.pathname).toBe("/variants/rs429358")
    expect(url.searchParams.get("sample_id")).toBe("1")
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("shows sample switcher options", () => {
    render(<CommandPalette open={true} onOpenChange={vi.fn()} />)
    // Active sample is 1 ("Sample A"), so "Sample B" should appear
    expect(screen.getByText("Sample B")).toBeInTheDocument()
    // Should show under "Switch Sample" group
    expect(screen.getByText("Switch Sample")).toBeInTheDocument()
  })

  it("switches sample on selection", async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<CommandPalette open={true} onOpenChange={onOpenChange} />)

    const sampleItem = screen.getByTestId("command-palette-sample-item")
    await user.click(sampleItem)

    expect(mockSetSearchParams).toHaveBeenCalled()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
