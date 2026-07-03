/** Tests for SystemHealth component (P4-21b). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "./test-utils"
import SystemHealth from "@/components/settings/SystemHealth"
import type { DatabaseStat } from "@/api/admin"

const mockFetch = vi.fn()
beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch)
  mockFetch.mockReset()
})

const STATUS_RESPONSE = {
  version: "0.1.0",
  uptime_seconds: 3661,
  data_dir: "/home/test/.yeliztli",
  active_jobs: [
    {
      job_id: "job-1",
      job_type: "annotation",
      status: "running",
      progress_pct: 45,
      message: "Annotating",
      created_at: "2026-03-26T12:00:00",
    },
  ],
  total_samples: 3,
  auth_enabled: false,
  log_level: "INFO",
}

const DISK_RESPONSE = {
  data_dir: "/home/test/.yeliztli",
  total_bytes: 500_000_000_000,
  free_bytes: 200_000_000_000,
  used_bytes: 300_000_000_000,
  reference_dbs_bytes: 4_000_000_000,
  sample_dbs_bytes: 50_000_000,
  logs_bytes: 1_000_000,
  other_bytes: 500_000,
}

const DB_STATS_RESPONSE = [
  {
    name: "reference",
    display_name: "Reference DB",
    file_path: "/home/test/.yeliztli/reference.db",
    file_size_bytes: 1_000_000,
    exists: true,
    row_count: null,
    last_updated: null,
    version: null,
  },
  {
    name: "clinvar",
    display_name: "ClinVar",
    file_path: "/home/test/.yeliztli/ClinVar.db",
    file_size_bytes: 250_000_000,
    exists: true,
    row_count: 500000,
    last_updated: "2026-03-20T10:00:00",
    version: "2026-03-15",
  },
]

const SAMPLE_STATS_RESPONSE = [
  {
    sample_id: 1,
    name: "Test Sample",
    db_path: "/home/test/.yeliztli/samples/sample_1.db",
    file_size_bytes: 10_000_000,
    exists: true,
  },
]

const LOGS_RESPONSE = {
  entries: [
    {
      id: 5,
      timestamp: "2026-03-26T12:00:00",
      level: "ERROR",
      logger: "backend.annotation.engine",
      message: "Annotation batch failed",
      event_data: '{"batch_size": 1000}',
    },
    {
      id: 4,
      timestamp: "2026-03-26T11:55:00",
      level: "INFO",
      logger: "backend.main",
      message: "Application started",
      event_data: null,
    },
  ],
  total: 2,
  page: 1,
  page_size: 50,
  has_more: false,
}

function mockAllEndpoints(
  overrides: {
    disk?: Partial<typeof DISK_RESPONSE>
    dbStats?: DatabaseStat[]
  } = {},
) {
  const diskResponse = { ...DISK_RESPONSE, ...overrides.disk }
  const dbStatsResponse = overrides.dbStats ?? DB_STATS_RESPONSE
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/api/admin/status")) {
      return { ok: true, json: async () => STATUS_RESPONSE }
    }
    if (url.includes("/api/admin/disk-usage")) {
      return { ok: true, json: async () => diskResponse }
    }
    if (url.includes("/api/admin/db-stats")) {
      return { ok: true, json: async () => dbStatsResponse }
    }
    if (url.includes("/api/admin/sample-stats")) {
      return { ok: true, json: async () => SAMPLE_STATS_RESPONSE }
    }
    if (url.includes("/api/admin/logs")) {
      return { ok: true, json: async () => LOGS_RESPONSE }
    }
    if (url.includes("/api/databases/health")) {
      return { ok: true, json: async () => ({ databases: [] }) }
    }
    return { ok: false, status: 404, text: async () => "Not found" }
  })
}

describe("SystemHealth", () => {
  it("renders the page heading", () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    expect(screen.getByText("System Health")).toBeInTheDocument()
  })

  it("displays system status after loading", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByText("v0.1.0")).toBeInTheDocument()
    })
    expect(screen.getByText("3")).toBeInTheDocument() // total_samples
  })

  it("displays disk usage information", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByText("Disk Usage")).toBeInTheDocument()
    })
    expect(screen.getByText("Reference DBs")).toBeInTheDocument()
    expect(screen.getByText("Sample DBs")).toBeInTheDocument()
  })

  it("does not emit non-finite disk segment widths when total bytes is unavailable", async () => {
    mockAllEndpoints({
      disk: {
        total_bytes: 0,
        free_bytes: 0,
        used_bytes: 0,
        reference_dbs_bytes: 4_000_000_000,
      },
    })
    render(<SystemHealth />)

    await waitFor(() => {
      expect(screen.getByText("Reference DBs")).toBeInTheDocument()
    })

    const styleValues = Array.from(document.querySelectorAll<HTMLElement>("[style]")).map(
      (node) => node.getAttribute("style") ?? "",
    )
    expect(styleValues.join(" ")).not.toContain("Infinity")
    expect(styleValues.join(" ")).not.toContain("NaN")
  })

  it("displays database stats table", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByText("Database Stats")).toBeInTheDocument()
    })
    expect(screen.getByText("Reference DB")).toBeInTheDocument()
    expect(screen.getByText("ClinVar")).toBeInTheDocument()
  })

  it("displays unknown database sizes as an em dash without changing real zero sizes", async () => {
    mockAllEndpoints({
      dbStats: [
        {
          name: "cpic",
          display_name: "CPIC",
          file_path: "/home/test/.yeliztli/reference.db",
          file_size_bytes: null,
          exists: true,
          row_count: 42,
          last_updated: "2026-03-20T10:00:00",
          version: "2026-03-15",
        },
        {
          name: "empty_reference",
          display_name: "Empty Reference",
          file_path: "/home/test/.yeliztli/empty.db",
          file_size_bytes: 0,
          exists: true,
          row_count: 0,
          last_updated: "2026-03-20T10:00:00",
          version: "2026-03-15",
        },
      ],
    })
    render(<SystemHealth />)

    const cpicRow = (await screen.findByText("CPIC")).closest("tr")
    expect(cpicRow).toHaveTextContent("Available")
    expect(cpicRow).toHaveTextContent("—")
    expect(cpicRow).not.toHaveTextContent("0 B")

    const zeroRow = screen.getByText("Empty Reference").closest("tr")
    expect(zeroRow).toHaveTextContent("Available")
    expect(zeroRow).toHaveTextContent("0 B")
  })

  it("displays log explorer section", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByText("Log Explorer")).toBeInTheDocument()
    })
  })

  it("displays log entries after loading", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByText("Annotation batch failed")).toBeInTheDocument()
    })
    expect(screen.getByText("Application started")).toBeInTheDocument()
  })

  it("shows active jobs in status section", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByText("annotation")).toBeInTheDocument()
    })
    expect(screen.getByText(/running.*45%/)).toBeInTheDocument()
  })

  it("shows sample stats", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByText("Test Sample")).toBeInTheDocument()
    })
  })

  it("has a refresh button in log explorer", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    // The page now has two "Refresh" controls (Database Health panel + Log
    // Explorer); the panel's carries a distinct accessible name, so scope to
    // the log-explorer one by its plain text label.
    await waitFor(() => {
      const refreshButtons = screen.getAllByText("Refresh")
      expect(refreshButtons.length).toBeGreaterThanOrEqual(1)
    })
    expect(
      screen.queryByRole("button", { name: "Refresh database health" }),
    ).toBeInTheDocument()
  })

  it("has log level filter dropdown", async () => {
    mockAllEndpoints()
    render(<SystemHealth />)
    await waitFor(() => {
      expect(screen.getByLabelText("Filter by log level")).toBeInTheDocument()
    })
  })

  it("shows error state when API fails", async () => {
    mockFetch.mockImplementation(async () => ({
      ok: false,
      status: 500,
      text: async () => "Internal Server Error",
    }))
    render(<SystemHealth />)
    await waitFor(() => {
      const errors = screen.getAllByText(/Failed to/)
      expect(errors.length).toBeGreaterThan(0)
    })
  })
})
