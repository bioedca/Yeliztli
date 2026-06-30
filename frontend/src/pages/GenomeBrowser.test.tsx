/**
 * @vitest-environment happy-dom
 */
import { act } from "react"
import { describe, it, expect, vi, beforeEach, afterAll } from "vitest"
import { render, screen, waitFor } from "@/test/test-utils"
import userEvent from "@testing-library/user-event"
import GenomeBrowser from "./GenomeBrowser"
import { __setIgvForTesting } from "@/components/igv-browser"
import { GENOME_BROWSER_REFERENCE_DISCLOSURE_KEY } from "@/components/igv-browser/IgvBrowser"

const mockSearch = vi.fn()
const mockOn = vi.fn()
const mockCreateBrowser = vi.fn()
const mockRemoveBrowser = vi.fn()

const mockBrowser = { search: mockSearch, on: mockOn }

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  // These tests exercise the browser's search/locus/variant-click behaviour, not
  // the one-time reference-fetch disclosure gate (#1286) — pre-acknowledge it so
  // IGV initializes. The gate itself is covered in IgvBrowser.test.tsx.
  localStorage.setItem(GENOME_BROWSER_REFERENCE_DISCLOSURE_KEY, "acknowledged")
  mockCreateBrowser.mockResolvedValue(mockBrowser)
  __setIgvForTesting({
    createBrowser: mockCreateBrowser,
    removeBrowser: mockRemoveBrowser,
  })
})

afterAll(() => {
  __setIgvForTesting(null)
})

// Mock useSearchParams to control URL params in tests
const mockSetSearchParams = vi.fn()
let mockParams = new URLSearchParams()

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom")
  return {
    ...actual,
    useSearchParams: () => [mockParams, mockSetSearchParams] as const,
  }
})

describe("GenomeBrowser", () => {
  beforeEach(() => {
    mockParams = new URLSearchParams()
  })

  it("renders page header and search bar", () => {
    mockCreateBrowser.mockReturnValue(new Promise(() => {}))
    render(<GenomeBrowser />)
    expect(screen.getByText("Genome Browser")).toBeInTheDocument()
    expect(screen.getByTestId("igv-search-input")).toBeInTheDocument()
    expect(screen.getByTestId("igv-search-button")).toBeInTheDocument()
  })

  it("renders the IGV browser component", () => {
    mockCreateBrowser.mockReturnValue(new Promise(() => {}))
    render(<GenomeBrowser />)
    expect(screen.getByTestId("igv-container")).toBeInTheDocument()
  })

  it("passes locus from URL params to IGV browser", async () => {
    mockParams = new URLSearchParams("locus=BRCA1")
    render(<GenomeBrowser />)
    await waitFor(() => {
      expect(mockCreateBrowser).toHaveBeenCalledTimes(1)
    })
    const [, options] = mockCreateBrowser.mock.calls[0]
    expect(options.locus).toBe("BRCA1")
  })

  it("defaults to 'all' locus when no URL param", async () => {
    render(<GenomeBrowser />)
    await waitFor(() => {
      expect(mockCreateBrowser).toHaveBeenCalledTimes(1)
    })
    const [, options] = mockCreateBrowser.mock.calls[0]
    expect(options.locus).toBe("all")
  })

  it("search bar calls browser.search() on form submit", async () => {
    const user = userEvent.setup()
    render(<GenomeBrowser />)

    await waitFor(() => {
      expect(mockCreateBrowser).toHaveBeenCalledTimes(1)
    })

    const input = screen.getByTestId("igv-search-input")
    await user.type(input, "MTHFR")
    await user.click(screen.getByTestId("igv-search-button"))

    expect(mockSearch).toHaveBeenCalledWith("MTHFR")
  })

  it("search bar updates URL search params", async () => {
    const user = userEvent.setup()
    render(<GenomeBrowser />)

    await waitFor(() => {
      expect(mockCreateBrowser).toHaveBeenCalledTimes(1)
    })

    const input = screen.getByTestId("igv-search-input")
    await user.type(input, "TP53")
    await user.click(screen.getByTestId("igv-search-button"))

    expect(mockSetSearchParams).toHaveBeenCalled()
    const updater = mockSetSearchParams.mock.calls[0][0]
    const result = updater(new URLSearchParams())
    expect(result.get("locus")).toBe("TP53")
  })

  it("search bar ignores empty input", async () => {
    const user = userEvent.setup()
    render(<GenomeBrowser />)

    await waitFor(() => {
      expect(mockCreateBrowser).toHaveBeenCalledTimes(1)
    })

    await user.click(screen.getByTestId("igv-search-button"))
    expect(mockSearch).not.toHaveBeenCalled()
  })

  it("search via Enter key submits the form", async () => {
    const user = userEvent.setup()
    render(<GenomeBrowser />)

    await waitFor(() => {
      expect(mockCreateBrowser).toHaveBeenCalledTimes(1)
    })

    const input = screen.getByTestId("igv-search-input")
    await user.type(input, "rs429358{enter}")

    expect(mockSearch).toHaveBeenCalledWith("rs429358")
  })

  it("shows variant click indicator when variant is clicked", async () => {
    render(<GenomeBrowser />)

    await waitFor(() => {
      expect(mockOn).toHaveBeenCalledWith("trackclick", expect.any(Function))
    })

    // Simulate variant click through the trackclick handler
    const trackClickHandler = mockOn.mock.calls.find(
      (args: unknown[]) => args[0] === "trackclick",
    )?.[1]

    act(() => {
      trackClickHandler(
        { config: { type: "variant" } },
        [
          { name: "Chr", value: "chr17" },
          { name: "Pos", value: "41196312" },
          { name: "ID", value: "rs80357906" },
          { name: "Ref", value: "A" },
          { name: "Alt", value: "G" },
        ],
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId("variant-click-indicator")).toBeInTheDocument()
    })
    expect(screen.getByText("chr17:41196312")).toBeInTheDocument()
    expect(screen.getByText("rs80357906")).toBeInTheDocument()
  })

  it("dismisses variant click indicator", async () => {
    const user = userEvent.setup()
    render(<GenomeBrowser />)

    await waitFor(() => {
      expect(mockOn).toHaveBeenCalledWith("trackclick", expect.any(Function))
    })

    const trackClickHandler = mockOn.mock.calls.find(
      (args: unknown[]) => args[0] === "trackclick",
    )?.[1]

    act(() => {
      trackClickHandler(
        { config: { type: "variant" } },
        [
          { name: "Chr", value: "chr1" },
          { name: "Pos", value: "12345" },
          { name: "ID", value: "rs123" },
          { name: "Ref", value: "C" },
          { name: "Alt", value: "T" },
        ],
      )
    })

    await waitFor(() => {
      expect(screen.getByTestId("variant-click-indicator")).toBeInTheDocument()
    })

    await user.click(screen.getByLabelText("Dismiss variant selection"))

    expect(screen.queryByTestId("variant-click-indicator")).not.toBeInTheDocument()
  })

  it("has accessible search form with proper labels", () => {
    mockCreateBrowser.mockReturnValue(new Promise(() => {}))
    render(<GenomeBrowser />)
    expect(screen.getByRole("search")).toHaveAttribute("aria-label", "Navigate to genomic locus")
    expect(screen.getByLabelText("Genomic locus search")).toBeInTheDocument()
  })
})
