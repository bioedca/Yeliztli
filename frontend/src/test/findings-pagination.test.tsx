/** Held requests exercise the production findings hook and mounted rows (#2485). */
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Link, MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"
import FindingsExplorer from "@/pages/FindingsExplorer"
import { useFindings } from "@/api/findings"
import type { Finding } from "@/types/findings"
import type { ReactNode } from "react"

function findings(count: number): Finding[] {
  return Array.from({ length: count }, (_, index) => ({
    id: index + 1, module: "rare_variants", category: "synthetic",
    evidence_level: 1, finding_text: `Synthetic finding ${index + 1}`,
    gene_symbol: null, rsid: null, phenotype: null, conditions: null,
    zygosity: null, clinvar_significance: null, diplotype: null,
    metabolizer_status: null, drug: null, haplogroup: null, prs_score: null,
    prs_percentile: null, pathway: null, pathway_level: null, svg_path: null,
    pmid_citations: [], detail: null, created_at: "2026-01-01T00:00:00",
  }))
}

const summary = {
  total_findings: 450,
  modules: [{ module: "rare_variants", count: 450, max_evidence_level: 1,
    top_finding_text: "Synthetic finding 1" }],
  high_confidence_findings: [],
}

const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } })

let pending: Array<{ url: URL; resolve: (response: Response) => void }>

beforeEach(() => {
  pending = []
  vi.stubGlobal("fetch", vi.fn((input: string) => {
    const url = new URL(input, "http://localhost")
    if (url.pathname.endsWith("/summary")) return Promise.resolve(response(summary))
    if (url.searchParams.get("limit") === "200" &&
        url.searchParams.get("sample_id") === "1" &&
        !url.searchParams.has("module") && !url.searchParams.has("min_stars")) {
      return Promise.resolve(response(findings(200)))
    }
    return new Promise<Response>((resolve) => pending.push({ url, resolve }))
  }))
})

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}>
    <MemoryRouter initialEntries={["/findings?sample_id=1"]}>
      <Link to="/findings?sample_id=2">Switch sample</Link>
      <Link to="/findings">Clear sample</Link>
      <FindingsExplorer />
    </MemoryRouter>
  </QueryClientProvider>)
  return client
}

async function loadMore() {
  const first = await screen.findByText("Synthetic finding 1")
  fireEvent.click(screen.getByRole("button", { name: "Load more findings" }))
  await waitFor(() => expect(pending).toHaveLength(1))
  expect(pending[0].url.searchParams.get("limit")).toBe("400")
  return first
}

it("keeps mounted rows and the disabled loading control through successive windows (#2485)", async () => {
  mount()
  const first = await loadMore()
  expect(screen.queryByText("Loading findings...")).not.toBeInTheDocument()
  expect(screen.getByText("Synthetic finding 1")).toBe(first)
  expect(screen.getAllByText(/^Synthetic finding \d+$/)).toHaveLength(200)
  const loading = screen.getByRole("button", { name: "Loading…" })
  expect(loading).toBeDisabled()
  fireEvent.click(loading)
  expect(pending).toHaveLength(1)
  expect(screen.getByText(/Showing the top 200 findings/)).toBeInTheDocument()

  await act(async () => pending[0].resolve(response(findings(400))))
  await screen.findByText("Synthetic finding 400")
  expect(screen.getByText("Synthetic finding 1")).toBe(first)
  expect(screen.getAllByText(/^Synthetic finding \d+$/)).toHaveLength(400)
  fireEvent.click(screen.getByRole("button", { name: "Load more findings" }))
  await waitFor(() => expect(pending).toHaveLength(2))
  expect(pending[1].url.searchParams.get("limit")).toBe("600")
  await act(async () => pending[1].resolve(response(findings(450))))
  await screen.findByText("Synthetic finding 450")
  expect(screen.getByText("Synthetic finding 1")).toBe(first)
  expect(screen.getAllByText(/^Synthetic finding \d+$/)).toHaveLength(450)
  expect(screen.queryByRole("button", { name: "Load more findings" })).not.toBeInTheDocument()
}, 30_000)

it("retains mounted rows on a transient failure and retries the same bound (#2485)", async () => {
  mount()
  const first = await loadMore()
  await act(async () => pending[0].resolve(response({ detail: "private server details" }, 503)))
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load more findings")
  expect(screen.queryByText("private server details")).not.toBeInTheDocument()
  expect(screen.getByText("Synthetic finding 1")).toBe(first)
  fireEvent.click(screen.getByRole("button", { name: "Retry loading more findings" }))
  await waitFor(() => expect(pending).toHaveLength(2))
  expect(pending[1].url.searchParams.get("limit")).toBe("400")
  expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled()
  await act(async () => pending[1].resolve(response(findings(250))))
  await screen.findByText("Synthetic finding 250")
  expect(screen.getByText("Synthetic finding 1")).toBe(first)
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
}, 30_000)

it.each([
  ["sample", "Switch sample"],
  ["no sample", "Clear sample"],
  ["module", "Rare Variants"],
  ["minimum evidence", "3+"],
])("clears the old window on a %s change, including a late response (#2485)", async (_context, label) => {
  mount()
  await loadMore()
  const control = label.includes("sample")
    ? screen.getByRole("link", { name: label })
    : screen.getByRole("button", { name: label === "Rare Variants" ? /Rare Variants/ : label })
  fireEvent.click(control)
  await waitFor(() => expect(screen.queryByText("Synthetic finding 1")).not.toBeInTheDocument())
  await act(async () => pending[0].resolve(response(findings(400))))
  expect(screen.queryByText("Synthetic finding 1")).not.toBeInTheDocument()
  expect(screen.queryByText("Synthetic finding 400")).not.toBeInTheDocument()
}, 30_000)

it.each([{ category: "different" }, { offset: 200 }])("does not reuse rows across hook context %j (#2485)", async (next) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const { result, rerender } = renderHook(
    (options: { category?: string; offset?: number }) => useFindings(1, options),
    { initialProps: {}, wrapper: ({ children }: { children: ReactNode }) =>
      <QueryClientProvider client={client}>{children}</QueryClientProvider> },
  )
  await waitFor(() => expect(result.current.data).toHaveLength(200))
  // Hold the new context's request even when it starts again at limit=200.
  vi.mocked(fetch).mockImplementationOnce(() => new Promise(() => {}))
  rerender(next)
  expect(result.current.data).toBeUndefined()
  expect(result.current.isLoading).toBe(true)
})

it("refetches only the latest successful bound on annotation invalidation (#2485)", async () => {
  const client = mount()
  await loadMore()
  await act(async () => pending[0].resolve(response(findings(400))))
  await screen.findByText("Synthetic finding 400")
  act(() => { void client.invalidateQueries({ queryKey: ["findings", 1] }) })
  await waitFor(() => expect(pending).toHaveLength(2))
  expect(pending[1].url.searchParams.get("limit")).toBe("400")
  await act(async () => pending[1].resolve(response([])))
  await screen.findByText(/No findings yet/)
  expect(screen.queryByText("Synthetic finding 400")).not.toBeInTheDocument()
  expect(pending).toHaveLength(2)
}, 30_000)

it.each([401, 403, 404, 423])("clears retained findings on a %s response (#2485)", async (status) => {
  mount()
  await loadMore()
  await act(async () => pending[0].resolve(response({ detail: "withheld" }, status)))
  await screen.findByRole("alert")
  expect(screen.queryByText("Synthetic finding 1")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Load more findings" })).not.toBeInTheDocument()
}, 30_000)

it("clears a loaded window when its summary becomes withheld (#2485)", async () => {
  const client = mount()
  await loadMore()
  await act(async () => {
    client.setQueryData(["findings-summary", 1], { ...summary, modules: [], withheld: true })
  })
  await screen.findByText(/Findings are withheld for this sample/)
  expect(screen.queryByText("Synthetic finding 1")).not.toBeInTheDocument()
  await act(async () => pending[0].resolve(response(findings(400))))
  expect(screen.queryByText("Synthetic finding 400")).not.toBeInTheDocument()
}, 30_000)

it("replaces the previous window with a successful empty response (#2485)", async () => {
  mount()
  await loadMore()
  await act(async () => pending[0].resolve(response([])))
  await screen.findByText(/No findings yet/)
  expect(screen.queryByText("Synthetic finding 1")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Load more findings" })).not.toBeInTheDocument()
}, 30_000)
