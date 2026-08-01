/** Tests for SQL Console UI (P4-04). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "./test-utils"
import userEvent from "@testing-library/user-event"
import SqlConsole from "@/components/query-builder/SqlConsole"
import QueryBuilderView from "@/pages/QueryBuilderView"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render as rtlRender } from "@testing-library/react"
import type { ReactNode } from "react"
import type { SqlResult } from "@/types/query-builder"

// Mock Monaco editor since it requires a browser environment
vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onChange,
    "data-testid": testId,
  }: {
    value?: string
    onChange?: (v: string) => void
    "data-testid"?: string
  }) => (
    <textarea
      data-testid={testId ?? "monaco-editor-mock"}
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
      aria-label="SQL editor"
    />
  ),
}))

const mockFetch = vi.fn()

function renderWithRoute(ui: ReactNode, route = "/query-builder") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

const MOCK_SQL_RESULT: SqlResult = {
  columns: [
    { name: "rsid", type: null },
    { name: "chrom", type: null },
    { name: "pos", type: null },
    { name: "genotype", type: null },
  ],
  rows: [
    ["rs429358", "19", 44908684, "TC"],
    ["rs7412", "19", 44908822, "CC"],
  ],
  row_count: 2,
  truncated: false,
  execution_time_ms: 3.45,
}

const MOCK_SQL_TRUNCATED: SqlResult = {
  ...MOCK_SQL_RESULT,
  truncated: true,
  row_count: 500,
}

const MOCK_SCHEMA_TABLES_RESULT: SqlResult = {
  columns: [{ name: "name", type: null }],
  rows: [["annotated_variants"], ["raw_variants"]],
  row_count: 2,
  truncated: false,
  execution_time_ms: 0.5,
}

const MOCK_TABLE_INFO_RESULT: SqlResult = {
  columns: [
    { name: "cid", type: null },
    { name: "name", type: null },
    { name: "type", type: null },
    { name: "notnull", type: null },
    { name: "dflt_value", type: null },
    { name: "pk", type: null },
  ],
  rows: [
    [0, "rsid", "TEXT", 0, null, 0],
    [1, "chrom", "TEXT", 0, null, 0],
    [2, "pos", "INTEGER", 0, null, 0],
  ],
  row_count: 3,
  truncated: false,
  execution_time_ms: 0.2,
}

function setupDefaultMocks() {
  mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
    // Schema: table list
    if (url === "/api/query/sql" && opts?.body) {
      const body = JSON.parse(opts.body as string)
      if (body.sql.includes("sqlite_master")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(MOCK_SCHEMA_TABLES_RESULT),
        })
      }
      if (body.sql.includes("PRAGMA table_info")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(MOCK_TABLE_INFO_RESULT),
        })
      }
      // Regular SQL execution
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_SQL_RESULT),
      })
    }
    // Query fields (for visual builder tab)
    if (url === "/api/query/fields") {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            fields: [{ name: "rsid", type: "text", label: "Rsid" }],
            operators: ["="],
          }),
        text: () => Promise.resolve(""),
      })
    }
    // Saved queries
    if (url.includes("/api/saved-queries")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ queries: [] }),
        text: () => Promise.resolve(""),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal("fetch", mockFetch)
})

describe("QueryBuilderView tabs", () => {
  it("renders Visual Builder and SQL Console tabs", async () => {
    setupDefaultMocks()
    renderWithRoute(<QueryBuilderView />, "/query-builder?sample_id=1")

    expect(screen.getByTestId("tab-visual")).toBeInTheDocument()
    expect(screen.getByTestId("tab-sql")).toBeInTheDocument()
  })

  it("defaults to Visual Builder tab", async () => {
    setupDefaultMocks()
    renderWithRoute(<QueryBuilderView />, "/query-builder?sample_id=1")

    const visualTab = screen.getByTestId("tab-visual")
    expect(visualTab).toHaveAttribute("aria-selected", "true")
  })

  it("switches to SQL Console tab when clicked", async () => {
    setupDefaultMocks()
    renderWithRoute(<QueryBuilderView />, "/query-builder?sample_id=1")

    const user = userEvent.setup()
    await user.click(screen.getByTestId("tab-sql"))

    expect(screen.getByTestId("tab-sql")).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("tab-visual")).toHaveAttribute("aria-selected", "false")
  })

  it("shows SQL console content when SQL tab is active", async () => {
    setupDefaultMocks()
    renderWithRoute(<QueryBuilderView />, "/query-builder?sample_id=1")

    const user = userEvent.setup()
    await user.click(screen.getByTestId("tab-sql"))

    expect(screen.getByTestId("run-sql-btn")).toBeInTheDocument()
    expect(screen.getByTestId("schema-panel")).toBeInTheDocument()
  })
})

describe("SqlConsole", () => {
  it("renders editor and run button", () => {
    setupDefaultMocks()
    render(<SqlConsole sampleId={1} />)

    expect(screen.getByTestId("sql-editor-container")).toBeInTheDocument()
    expect(screen.getByTestId("run-sql-btn")).toBeInTheDocument()
  })

  it("shows keyboard shortcut hint", () => {
    setupDefaultMocks()
    render(<SqlConsole sampleId={1} />)

    expect(screen.getByText(/enter to run/i)).toBeInTheDocument()
  })

  it("renders schema panel", async () => {
    setupDefaultMocks()
    render(<SqlConsole sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByTestId("schema-panel")).toBeInTheDocument()
    })
    expect(screen.getByText("Schema")).toBeInTheDocument()
  })

  it("loads and displays schema tables", async () => {
    setupDefaultMocks()
    render(<SqlConsole sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("annotated_variants")).toBeInTheDocument()
    })
    expect(screen.getByText("raw_variants")).toBeInTheDocument()
  })

  it("expands table to show columns", async () => {
    setupDefaultMocks()
    render(<SqlConsole sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("annotated_variants")).toBeInTheDocument()
    })

    const user = userEvent.setup()
    const toggleButtons = screen.getAllByTestId("schema-table-toggle")
    await user.click(toggleButtons[0])

    await waitFor(() => {
      const columns = screen.getAllByTestId("schema-column")
      expect(columns.length).toBeGreaterThan(0)
    })
  })

  it("executes SQL and displays results", async () => {
    setupDefaultMocks()
    render(<SqlConsole sampleId={1} />)

    const user = userEvent.setup()
    await user.click(screen.getByTestId("run-sql-btn"))

    await waitFor(() => {
      expect(screen.getByTestId("sql-results")).toBeInTheDocument()
    })

    // Check results table
    expect(screen.getByTestId("sql-results-table")).toBeInTheDocument()
    const rows = screen.getAllByTestId("sql-result-row")
    expect(rows).toHaveLength(2)

    // Check execution time
    expect(screen.getByText(/3\.5 ms/)).toBeInTheDocument()
    // Check row count
    expect(screen.getByText(/2 rows/)).toBeInTheDocument()
  })

  it("displays error for rejected write operations", async () => {
    mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
      if (url === "/api/query/sql" && opts?.body) {
        const body = JSON.parse(opts.body as string)
        if (body.sql.includes("sqlite_master") || body.sql.includes("PRAGMA")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(MOCK_SCHEMA_TABLES_RESULT),
          })
        }
        return Promise.resolve({
          ok: false,
          status: 403,
          json: () =>
            Promise.resolve({
              detail:
                "Write operations are not allowed in the SQL console. Only SELECT and read-only statements are permitted.",
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })

    render(<SqlConsole sampleId={1} />)

    const user = userEvent.setup()
    await user.click(screen.getByTestId("run-sql-btn"))

    await waitFor(() => {
      expect(screen.getByTestId("sql-error")).toBeInTheDocument()
    })
    expect(
      screen.getByText("SQL execution failed. Please try again."),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/write operations are not allowed/i),
    ).not.toBeInTheDocument()
  })

  it("shows truncation warning when results are truncated", async () => {
    mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
      if (url === "/api/query/sql" && opts?.body) {
        const body = JSON.parse(opts.body as string)
        if (body.sql.includes("sqlite_master")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(MOCK_SCHEMA_TABLES_RESULT),
          })
        }
        if (body.sql.includes("PRAGMA")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(MOCK_TABLE_INFO_RESULT),
          })
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(MOCK_SQL_TRUNCATED),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })

    render(<SqlConsole sampleId={1} />)

    const user = userEvent.setup()
    await user.click(screen.getByTestId("run-sql-btn"))

    await waitFor(() => {
      expect(screen.getAllByText(/truncated/i).length).toBeGreaterThan(0)
    })
    expect(screen.getByText(/add a limit clause/i)).toBeInTheDocument()
  })

  it("shows NULL values in italics", async () => {
    const resultWithNulls: SqlResult = {
      columns: [
        { name: "rsid", type: null },
        { name: "gene", type: null },
      ],
      rows: [["rs123", null]],
      row_count: 1,
      truncated: false,
      execution_time_ms: 1.0,
    }

    mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
      if (url === "/api/query/sql" && opts?.body) {
        const body = JSON.parse(opts.body as string)
        if (body.sql.includes("sqlite_master")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(MOCK_SCHEMA_TABLES_RESULT),
          })
        }
        if (body.sql.includes("PRAGMA")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(MOCK_TABLE_INFO_RESULT),
          })
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(resultWithNulls),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })

    render(<SqlConsole sampleId={1} />)

    const user = userEvent.setup()
    await user.click(screen.getByTestId("run-sql-btn"))

    await waitFor(() => {
      expect(screen.getByText("NULL")).toBeInTheDocument()
    })
    expect(screen.getByText("NULL")).toHaveClass("italic")
  })

  it("displays column headers from query results", async () => {
    setupDefaultMocks()
    render(<SqlConsole sampleId={1} />)

    const user = userEvent.setup()
    await user.click(screen.getByTestId("run-sql-btn"))

    await waitFor(() => {
      expect(screen.getByText("rsid")).toBeInTheDocument()
    })
    expect(screen.getByText("chrom")).toBeInTheDocument()
    expect(screen.getByText("pos")).toBeInTheDocument()
    expect(screen.getByText("genotype")).toBeInTheDocument()
  })

  it("shows empty result message for queries with no rows", async () => {
    const emptyResult: SqlResult = {
      columns: [],
      rows: [],
      row_count: 0,
      truncated: false,
      execution_time_ms: 0.8,
    }

    mockFetch.mockImplementation((url: string, opts?: RequestInit) => {
      if (url === "/api/query/sql" && opts?.body) {
        const body = JSON.parse(opts.body as string)
        if (body.sql.includes("sqlite_master")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(MOCK_SCHEMA_TABLES_RESULT),
          })
        }
        if (body.sql.includes("PRAGMA")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(MOCK_TABLE_INFO_RESULT),
          })
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(emptyResult),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })

    render(<SqlConsole sampleId={1} />)

    const user = userEvent.setup()
    await user.click(screen.getByTestId("run-sql-btn"))

    await waitFor(() => {
      expect(screen.getByTestId("sql-empty-result")).toBeInTheDocument()
    })
    expect(screen.getByText(/no results/i)).toBeInTheDocument()
  })
})
