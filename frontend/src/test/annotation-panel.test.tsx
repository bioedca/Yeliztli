import { act } from "react"
import { QueryClient } from "@tanstack/react-query"
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "./test-utils"
import AnnotationPanel from "@/components/dashboard/AnnotationPanel"

// ── Mock fetch ────────────────────────────────────────────────────────

const mockFetch = vi.fn()

// ── Mock EventSource ──────────────────────────────────────────────────

type EventSourceListener = (event: MessageEvent) => void

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  listeners: Record<string, EventSourceListener[]> = {}
  readyState = 0 // CONNECTING

  constructor(url: string) {
    this.url = url
    this.readyState = 1 // OPEN
    MockEventSource.instances.push(this)
  }

  addEventListener(event: string, listener: EventSourceListener) {
    if (!this.listeners[event]) this.listeners[event] = []
    this.listeners[event].push(listener)
  }

  close() {
    this.readyState = 2 // CLOSED
  }

  // Test helper: simulate a server event
  _emit(event: string, data: unknown) {
    const listeners = this.listeners[event] ?? []
    for (const fn of listeners) {
      fn(new MessageEvent(event, { data: JSON.stringify(data) }))
    }
  }
}

/** Mock the active-annotation-job endpoint to return 404 (no active job). */
function mockNoActiveJob() {
  mockFetch.mockResolvedValueOnce({
    ok: false,
    status: 404,
    json: async () => ({ detail: "No active job" }),
  })
}

beforeEach(() => {
  mockFetch.mockReset()
  MockEventSource.instances = []
  vi.stubGlobal("fetch", mockFetch)
  vi.stubGlobal("EventSource", MockEventSource)
  // useActiveAnnotationJob fires on mount — always mock it first
  mockNoActiveJob()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

// ── Helpers ───────────────────────────────────────────────────────────

function mockStartAnnotation(jobId = "test-job-123") {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ job_id: jobId, sample_id: 1, status: "pending" }),
  })
}

function mockCancelAnnotation(jobId = "test-job-123") {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ job_id: jobId, status: "cancelled" }),
  })
}

function mockStartAnnotationError(detail = "Already in progress") {
  mockFetch.mockResolvedValueOnce({
    ok: false,
    status: 409,
    json: async () => ({ detail }),
  })
}

// ═══════════════════════════════════════════════════════════════════════
// Initial state
// ═══════════════════════════════════════════════════════════════════════

describe("AnnotationPanel", () => {
  describe("idle state", () => {
    it("shows Run Annotation button", () => {
      render(<AnnotationPanel sampleId={1} variantCount={623841} />)
      expect(screen.getByText("Run Annotation")).toBeInTheDocument()
    })

    it("shows variant count in description", () => {
      render(<AnnotationPanel sampleId={1} variantCount={623841} />)
      expect(screen.getByText(/623,841 variants/)).toBeInTheDocument()
    })

    it("has accessible region label", () => {
      render(<AnnotationPanel sampleId={1} variantCount={null} />)
      expect(screen.getByRole("region", { name: /Annotation/i })).toBeInTheDocument()
    })

    it("shows generic description when variant count is null", () => {
      render(<AnnotationPanel sampleId={1} variantCount={null} />)
      expect(screen.getByText(/Run the annotation pipeline/)).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════════
  // Starting annotation
  // ═══════════════════════════════════════════════════════════════════════

  describe("starting annotation", () => {
    it("calls POST /api/annotation/{sample_id}", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={42} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith("/api/annotation/42", {
          method: "POST",
        })
      })
    })

    it("connects to SSE endpoint after starting", async () => {
      mockStartAnnotation("my-job")
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => {
        expect(MockEventSource.instances.length).toBe(1)
        expect(MockEventSource.instances[0].url).toBe(
          "/api/annotation/status/my-job"
        )
      })
    })

    it("shows error when start fails", async () => {
      mockStartAnnotationError("Annotation already in progress for sample 1")
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => {
        expect(
          screen.getByText(/Annotation already in progress/)
        ).toBeInTheDocument()
      })
    })
  })

  // ═══════════════════════════════════════════════════════════════════════
  // Progress tracking
  // ═══════════════════════════════════════════════════════════════════════

  describe("progress tracking", () => {
    it("shows progress bar when running", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      const es = MockEventSource.instances[0]
      act(() => {
        es._emit("progress", {
          job_id: "test-job-123",
          status: "running",
          progress_pct: 50.0,
          message: "Annotated 500/1,000 variants",
          error: null,
        })
      })

      await waitFor(() => {
        expect(screen.getByRole("progressbar")).toBeInTheDocument()
        expect(screen.getByText("50.0%")).toBeInTheDocument()
        expect(screen.getByText(/Annotated 500/)).toBeInTheDocument()
      })
    })

    it("shows Annotating... label when running", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "running",
          progress_pct: 25.0,
          message: "Annotated 250/1,000 variants",
          error: null,
        })
      })

      await waitFor(() => {
        expect(screen.getByText("Annotating...")).toBeInTheDocument()
      })
    })

    it("shows Annotation Complete on success", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "complete",
          progress_pct: 100.0,
          message: "Annotated 950 variants",
          error: null,
        })
      })

      await waitFor(() => {
        expect(screen.getByText("Annotation Complete")).toBeInTheDocument()
        expect(screen.getByText("100.0%")).toBeInTheDocument()
      })
    })

    it("shows Annotation Failed with error message", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "failed",
          progress_pct: 30.0,
          message: "Annotation failed",
          error: "Database connection lost",
        })
      })

      await waitFor(() => {
        expect(screen.getByText("Annotation Failed")).toBeInTheDocument()
        expect(screen.getByText("Database connection lost")).toBeInTheDocument()
      })
    })

    it("closes EventSource on terminal state", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      const es = MockEventSource.instances[0]
      act(() => {
        es._emit("progress", {
          job_id: "test-job-123",
          status: "complete",
          progress_pct: 100.0,
          message: "Done",
          error: null,
        })
      })

      await waitFor(() => {
        expect(es.readyState).toBe(2) // CLOSED
      })
    })

    it("invalidates sample QC metrics on terminal state", async () => {
      const invalidateSpy = vi.spyOn(QueryClient.prototype, "invalidateQueries")

      mockStartAnnotation()
      render(<AnnotationPanel sampleId={42} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      const callsBeforeTerminalState = invalidateSpy.mock.calls.length

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "complete",
          progress_pct: 100.0,
          message: "Done",
          error: null,
        })
      })

      await waitFor(() => {
        expect(
          invalidateSpy.mock.calls.slice(callsBeforeTerminalState),
        ).toContainEqual([{ queryKey: ["analysis-qc-metrics", 42] }])
      })
    })
  })

  // ═══════════════════════════════════════════════════════════════════════
  // Cancel
  // ═══════════════════════════════════════════════════════════════════════

  describe("cancel", () => {
    it("shows cancel button when running", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "running",
          progress_pct: 10,
          message: "Working...",
          error: null,
        })
      })

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Cancel annotation/i })).toBeInTheDocument()
      })
    })

    it("calls POST /api/annotation/cancel/{job_id}", async () => {
      mockStartAnnotation("cancel-test-job")
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "cancel-test-job",
          status: "running",
          progress_pct: 10,
          message: "Working...",
          error: null,
        })
      })

      mockCancelAnnotation("cancel-test-job")

      await waitFor(() => {
        const cancelBtn = screen.getByRole("button", { name: /Cancel annotation/i })
        fireEvent.click(cancelBtn)
      })

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          "/api/annotation/cancel/cancel-test-job",
          { method: "POST" }
        )
      })
    })

    it("shows Annotation Cancelled after cancel", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "cancelled",
          progress_pct: 15.0,
          message: "Cancelled by user",
          error: null,
        })
      })

      await waitFor(() => {
        expect(screen.getByText("Annotation Cancelled")).toBeInTheDocument()
      })
    })
  })

  // ═══════════════════════════════════════════════════════════════════════
  // Dismiss
  // ═══════════════════════════════════════════════════════════════════════

  describe("dismiss", () => {
    it("shows dismiss button after completion", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "complete",
          progress_pct: 100.0,
          message: "Done",
          error: null,
        })
      })

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Dismiss/i })).toBeInTheDocument()
      })
    })

    it("returns to idle state after dismiss", async () => {
      mockStartAnnotation()
      render(<AnnotationPanel sampleId={1} variantCount={1000} />)

      fireEvent.click(screen.getByText("Run Annotation"))

      await waitFor(() => expect(MockEventSource.instances.length).toBe(1))

      act(() => {
        MockEventSource.instances[0]._emit("progress", {
          job_id: "test-job-123",
          status: "complete",
          progress_pct: 100.0,
          message: "Done",
          error: null,
        })
      })

      await waitFor(() => {
        fireEvent.click(screen.getByRole("button", { name: /Dismiss/i }))
      })

      await waitFor(() => {
        expect(screen.getByText("Run Annotation")).toBeInTheDocument()
      })
    })
  })
})
