/** Tests for shared page state components (P4-26b). */

import type React from "react"
import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "./test-utils"
import { Heart } from "lucide-react"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import ErrorBoundary from "@/components/ui/ErrorBoundary"

// ── PageLoading ──────────────────────────────────────────────────────

describe("PageLoading", () => {
  it("renders default loading message", () => {
    render(<PageLoading />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("renders custom message", () => {
    render(<PageLoading message="Loading variants..." />)
    expect(screen.getByText("Loading variants...")).toBeInTheDocument()
  })

  it("has status role for accessibility", () => {
    render(<PageLoading message="Fetching data" />)
    expect(screen.getByRole("status")).toBeInTheDocument()
  })
})

// ── PageError ────────────────────────────────────────────────────────

describe("PageError", () => {
  it("renders default error message", () => {
    render(<PageError />)
    expect(screen.getByText("Failed to load data")).toBeInTheDocument()
    expect(screen.getByText("An unexpected error occurred.")).toBeInTheDocument()
  })

  it("renders custom message", () => {
    render(<PageError message="Network timeout" />)
    expect(screen.getByText("Network timeout")).toBeInTheDocument()
  })

  it("renders retry button when onRetry provided", () => {
    const onRetry = vi.fn()
    render(<PageError onRetry={onRetry} />)
    const button = screen.getByRole("button", { name: /retry/i })
    expect(button).toBeInTheDocument()
    fireEvent.click(button)
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it("does not render retry button when onRetry is not provided", () => {
    render(<PageError />)
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument()
  })

  it("has alert role for accessibility", () => {
    render(<PageError />)
    expect(screen.getByRole("alert")).toBeInTheDocument()
  })
})

// ── PageEmpty ────────────────────────────────────────────────────────

describe("PageEmpty", () => {
  it("renders title and icon", () => {
    render(<PageEmpty icon={Heart} title="No results found" />)
    expect(screen.getByText("No results found")).toBeInTheDocument()
  })

  it("renders description when provided", () => {
    render(
      <PageEmpty
        icon={Heart}
        title="No results"
        description="Run annotation first."
      />,
    )
    expect(screen.getByText("Run annotation first.")).toBeInTheDocument()
  })

  it("renders action button when provided", () => {
    const onClick = vi.fn()
    render(
      <PageEmpty
        icon={Heart}
        title="No results"
        action={{ label: "Run annotation", onClick }}
      />,
    )
    const button = screen.getByRole("button", { name: /run annotation/i })
    expect(button).toBeInTheDocument()
    fireEvent.click(button)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("does not render action when not provided", () => {
    render(<PageEmpty icon={Heart} title="No results" />)
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("has region role for accessibility", () => {
    render(<PageEmpty icon={Heart} title="No results" />)
    expect(screen.getByRole("region")).toBeInTheDocument()
  })
})

// ── ErrorBoundary ────────────────────────────────────────────────────

describe("ErrorBoundary", () => {
  // Suppress React error boundary console.error noise in tests
  const originalError = console.error
  beforeAll(() => {
    console.error = (...args: unknown[]) => {
      const msg = typeof args[0] === "string" ? args[0] : ""
      if (msg.includes("ErrorBoundary") || msg.includes("The above error")) return
      originalError.call(console, ...args)
    }
  })
  afterAll(() => {
    console.error = originalError
  })

  function ThrowingComponent({ message }: { message: string }): React.ReactNode {
    throw new Error(message)
  }

  it("renders children when no error", () => {
    render(
      <ErrorBoundary>
        <div>Hello world</div>
      </ErrorBoundary>,
    )
    expect(screen.getByText("Hello world")).toBeInTheDocument()
  })

  it("renders error UI when child throws", () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent message="Test explosion" />
      </ErrorBoundary>,
    )
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    expect(
      screen.getByText("An unexpected error occurred. Please try again."),
    ).toBeInTheDocument()
    expect(screen.queryByText("Test explosion")).not.toBeInTheDocument()
  })

  it("shows retry button that resets the boundary", () => {
    let shouldThrow = true
    function MaybeThrow() {
      if (shouldThrow) throw new Error("Boom")
      return <div>Recovered</div>
    }

    const { rerender } = render(
      <ErrorBoundary>
        <MaybeThrow />
      </ErrorBoundary>,
    )

    expect(screen.getByText("Something went wrong")).toBeInTheDocument()

    // Fix the error condition and click retry
    shouldThrow = false
    fireEvent.click(screen.getByRole("button", { name: /try again/i }))

    rerender(
      <ErrorBoundary>
        <MaybeThrow />
      </ErrorBoundary>,
    )

    expect(screen.getByText("Recovered")).toBeInTheDocument()
  })

  it("renders custom fallback when provided", () => {
    render(
      <ErrorBoundary fallback={<div>Custom fallback</div>}>
        <ThrowingComponent message="Oops" />
      </ErrorBoundary>,
    )
    expect(screen.getByText("Custom fallback")).toBeInTheDocument()
  })
})
