/** Tests for WatchingSidebar component (P4-21k).
 *
 * T4-22o (partial): Watching sidebar section shows watched variant after watch action.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import WatchingSidebar from "@/components/variant-table/WatchingSidebar"

// Mock the watches API hooks
const mockWatchedVariants = vi.fn()
const mockUnwatchMutate = vi.fn()

vi.mock("@/api/watches", () => ({
  useWatchedVariants: (...args: unknown[]) => mockWatchedVariants(...args),
  useUnwatchVariant: () => ({
    mutate: mockUnwatchMutate,
    isPending: false,
  }),
}))

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe("WatchingSidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders nothing when sampleId is null", () => {
    mockWatchedVariants.mockReturnValue({ data: [], isLoading: false })
    const { container } = renderWithProviders(
      <WatchingSidebar sampleId={null} />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("shows empty state naming the real watch control, not a phantom eye icon", () => {
    mockWatchedVariants.mockReturnValue({ data: [], isLoading: false })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    // The instruction must name the affordance that actually exists — the
    // "Watch this variant" button in a variant's detail panel — and must not
    // point at an eye icon on a row, which the table never renders (#1995).
    expect(screen.getByText(/no watched variants/i)).toBeInTheDocument()
    expect(screen.getByText(/Watch this variant/)).toBeInTheDocument()
    expect(screen.queryByText(/eye icon/i)).not.toBeInTheDocument()
  })

  it("shows loading state", () => {
    mockWatchedVariants.mockReturnValue({ data: undefined, isLoading: true })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    expect(screen.getByText(/loading watched variants/i)).toBeInTheDocument()
  })

  it("lists watched variants with rsid, significance, and date", () => {
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs12345",
          watched_at: "2026-03-01T12:00:00",
          clinvar_significance_at_watch: "Uncertain_significance",
          clinvar_significance_current: "Uncertain_significance",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    expect(screen.getByText("rs12345")).toBeInTheDocument()
    expect(screen.getByText("Uncertain significance")).toBeInTheDocument()
    expect(screen.getByText(/mar 1, 2026/i)).toBeInTheDocument()
  })

  it("highlights reclassified variants with amber ring and arrow notation", () => {
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs99999",
          watched_at: "2026-02-15T10:00:00",
          clinvar_significance_at_watch: "Uncertain_significance",
          clinvar_significance_current: "Pathogenic",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    // Should show old → new significance
    expect(screen.getByText("Uncertain significance")).toBeInTheDocument()
    expect(screen.getByText("Pathogenic")).toBeInTheDocument()
    expect(screen.getByText("→")).toBeInTheDocument()
    // Reclassified icon
    expect(screen.getByLabelText("Reclassified since watched")).toBeInTheDocument()
  })

  it("shows reclassified count badge in header", () => {
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs111",
          watched_at: "2026-03-01T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "Pathogenic",
          notes: "",
        },
        {
          rsid: "rs222",
          watched_at: "2026-03-02T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "VUS",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    // Header should show total count (2) and reclassified count (1)
    const sidebar = screen.getByTestId("watching-sidebar")
    expect(within(sidebar).getByText("2")).toBeInTheDocument()
    expect(within(sidebar).getByText("1")).toBeInTheDocument()
  })

  it("toggles sort mode between watched_at and reclassified", async () => {
    const user = userEvent.setup()
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs111",
          watched_at: "2026-03-02T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "VUS",
          notes: "",
        },
        {
          rsid: "rs222",
          watched_at: "2026-03-01T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "Pathogenic",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    // Default sort: by watched_at desc — rs111 first
    const items = screen.getAllByRole("button", { name: /^rs/ })
    expect(items[0]).toHaveAttribute("aria-label", "rs111")

    // Click sort toggle to switch to reclassified first
    await user.click(screen.getByText(/sort: date watched/i))

    // Now reclassified variant rs222 should be first
    const reorderedItems = screen.getAllByRole("button", { name: /^rs/ })
    expect(reorderedItems[0]).toHaveAttribute("aria-label", expect.stringContaining("rs222"))
  })

  it("keeps the sort toggle's accessible name containing its visible text in both states", async () => {
    // WCAG 2.5.3 Label in Name (#2027). The accessible name must CONTAIN the
    // visible text, or voice control ("click Sort: Date watched") cannot reach
    // the button. This regressed in BOTH states at once, because the label and
    // the visible text branched on the same condition but returned opposite
    // sort modes, so the two were never the same words.
    const user = userEvent.setup()
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs111",
          watched_at: "2026-03-02T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "VUS",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    const expectLabelInName = () => {
      const toggle = screen.getByText(/^sort:/i).closest("button")
      expect(toggle).not.toBeNull()
      const visibleText = toggle!.textContent?.trim() ?? ""
      expect(visibleText).not.toBe("")
      // Resolve through the real accessible-name computation rather than
      // reading aria-label back, so this still holds if the name later comes
      // from another source. getAllByRole throws when nothing matches.
      const reachableByVisibleText = screen.getAllByRole("button", {
        name: (accessibleName) => accessibleName.includes(visibleText),
      })
      expect(reachableByVisibleText).toContain(toggle)
      return visibleText
    }

    expect(expectLabelInName()).toBe("Sort: Date watched")

    await user.click(screen.getByText(/^sort:/i))

    expect(expectLabelInName()).toBe("Sort: Reclassified first")
  })

  it("calls onSelectVariant when a watched variant is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs12345",
          watched_at: "2026-03-01T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "VUS",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} onSelectVariant={onSelect} />)

    await user.click(screen.getByText("rs12345"))
    expect(onSelect).toHaveBeenCalledWith("rs12345")
  })

  it("calls unwatch mutation when unwatch button is clicked", async () => {
    const user = userEvent.setup()
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs12345",
          watched_at: "2026-03-01T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "VUS",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    await user.click(screen.getByLabelText("Unwatch rs12345"))
    expect(mockUnwatchMutate).toHaveBeenCalledWith("rs12345", expect.objectContaining({ onSettled: expect.any(Function) }))
  })

  it("collapses and expands when header is clicked", async () => {
    const user = userEvent.setup()
    mockWatchedVariants.mockReturnValue({
      data: [
        {
          rsid: "rs12345",
          watched_at: "2026-03-01T12:00:00",
          clinvar_significance_at_watch: "VUS",
          clinvar_significance_current: "VUS",
          notes: "",
        },
      ],
      isLoading: false,
    })
    renderWithProviders(<WatchingSidebar sampleId={1} />)

    // Initially expanded
    expect(screen.getByText("rs12345")).toBeInTheDocument()

    // Collapse
    await user.click(screen.getByRole("button", { name: /watching/i }))
    expect(screen.queryByText("rs12345")).not.toBeInTheDocument()

    // Expand
    await user.click(screen.getByRole("button", { name: /watching/i }))
    expect(screen.getByText("rs12345")).toBeInTheDocument()
  })
})
