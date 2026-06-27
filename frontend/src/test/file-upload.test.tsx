import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "./test-utils"
import FileUpload from "@/components/upload/FileUpload"

// Mock fetch globally
const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
})

describe("FileUpload", () => {
  it("renders the drop zone in idle state", () => {
    render(<FileUpload />)
    expect(
      screen.getByText(/drop your 23andMe or AncestryDNA file here/i),
    ).toBeInTheDocument()
    expect(
      screen.getByLabelText(/upload 23andMe or AncestryDNA file/i),
    ).toBeInTheDocument()
  })

  it("shows dragging state on dragover", () => {
    render(<FileUpload />)
    const dropZone = screen.getByRole("button")
    fireEvent.dragOver(dropZone)
    // The border color changes — component should still be visible
    expect(
      screen.getByText(/drop your 23andMe or AncestryDNA file here/i),
    ).toBeInTheDocument()
  })

  it("uploads file on drop and shows success", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          sample_id: 1,
          job_id: "abc-123",
          variant_count: 600000,
          nocall_count: 500,
          file_format: "23andme_v5",
        }),
    })

    render(<FileUpload />)
    const dropZone = screen.getByRole("button")
    const file = new File(["test content"], "genome.txt", {
      type: "text/plain",
    })
    fireEvent.drop(dropZone, {
      dataTransfer: { files: [file] },
    })

    // Should show success
    await waitFor(() => {
      expect(screen.getByText(/600,000 variants parsed/i)).toBeInTheDocument()
    })

    expect(screen.getByText(/view variants/i)).toBeInTheDocument()
    expect(screen.getByText(/upload another/i)).toBeInTheDocument()
  })

  it("shows error state on upload failure", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      text: () => Promise.resolve('{"detail":"Not a valid 23andMe file"}'),
    })

    render(<FileUpload />)
    const dropZone = screen.getByRole("button")
    const file = new File(["bad"], "bad.txt", { type: "text/plain" })
    fireEvent.drop(dropZone, {
      dataTransfer: { files: [file] },
    })

    await waitFor(() => {
      expect(screen.getByText(/upload failed/i)).toBeInTheDocument()
    })

    expect(screen.getByText(/try again/i)).toBeInTheDocument()
  })

  it("resets to idle after clicking try again", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: () => Promise.resolve("Server error"),
    })

    render(<FileUpload />)
    const dropZone = screen.getByRole("button")
    const file = new File(["bad"], "bad.txt", { type: "text/plain" })
    fireEvent.drop(dropZone, {
      dataTransfer: { files: [file] },
    })

    await waitFor(() => {
      expect(screen.getByText(/upload failed/i)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText(/try again/i))
    expect(
      screen.getByText(/drop your 23andMe or AncestryDNA file here/i),
    ).toBeInTheDocument()
  })

  it("rejects ZIP archives before calling ingest", async () => {
    render(<FileUpload />)
    const dropZone = screen.getByRole("button")
    const file = new File(["PK\u0003\u0004"], "genome.zip", {
      type: "application/zip",
    })
    fireEvent.drop(dropZone, {
      dataTransfer: { files: [file] },
    })

    expect(await screen.findByText(/upload failed/i)).toBeInTheDocument()
    expect(mockFetch).not.toHaveBeenCalled()
    expect(screen.getByText(/this looks like a zip archive/i)).toBeInTheDocument()
    expect(
      screen.getByText(/extract the raw 23andMe\/AncestryDNA \.txt file first/i),
    ).toBeInTheDocument()
  })

  it("rejects renamed ZIP archives by signature before calling ingest", async () => {
    render(<FileUpload />)
    const dropZone = screen.getByRole("button")
    const file = new File(["PK\u0003\u0004"], "renamed-genome.txt", {
      type: "application/octet-stream",
    })
    fireEvent.drop(dropZone, {
      dataTransfer: { files: [file] },
    })

    expect(await screen.findByText(/upload failed/i)).toBeInTheDocument()
    expect(mockFetch).not.toHaveBeenCalled()
    expect(screen.getByText(/this looks like a zip archive/i)).toBeInTheDocument()
  })

  it("renders the bundle-gate banner (not a raw object) on a 409 gate response", async () => {
    // Regression: the 409 detail is a structured object, not a string. The
    // old code set it directly as the error message and React threw
    // "Objects are not valid as a React child".
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () =>
        Promise.resolve({
          detail: {
            error: "bundle_version_too_old",
            installed_version: "v1.0.0",
            required_version: "v2.0.0",
            vendor: "ancestrydna",
            update_url: "https://example.invalid/bundle-v2",
            size_bytes: 500_000_000,
            checksum_sha256: null,
          },
        }),
    })

    render(<FileUpload />)
    const dropZone = screen.getByRole("button")
    const file = new File(["ancestry"], "AncestryDNA.txt", {
      type: "text/plain",
    })
    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByTestId("bundle-gate-banner")).toBeInTheDocument()
    })
    // Versions surface as strings; the update CTA is offered.
    expect(screen.getAllByText(/v2\.0\.0/).length).toBeGreaterThan(0)
    expect(screen.getByTestId("bundle-gate-update-cta")).toHaveTextContent(
      /update vep bundle to v2\.0\.0/i,
    )
  })

  it("uploads file via file input click", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          sample_id: 2,
          job_id: "def-456",
          variant_count: 100000,
          nocall_count: 0,
          file_format: "23andme_v4",
        }),
    })

    render(<FileUpload />)
    const fileInput = screen.getByLabelText(/upload 23andMe or AncestryDNA file/i)
    const file = new File(["content"], "my_data.txt", { type: "text/plain" })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByText(/100,000 variants parsed/i)).toBeInTheDocument()
    })
  })
})
