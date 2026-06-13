/** Step 15 — Setup wizard bundle-update affordance (Plan §5.4, §12.1, ADNA-00d).
 *
 * Covers:
 * - HTTP 409 bundle_version_too_old payload renders the in-wizard banner with
 *   the required + installed versions and the manifest's update URL size hint.
 * - The CTA fires `POST /api/updates/trigger` with `vep_bundle`.
 * - After the trigger completes, the banner clears and the upload can be
 *   retried.
 * - 23andMe-style 422 errors still surface in the existing error block
 *   (no false-positive banner).
 * - Success state renders parsed-variant summary and the dashboard CTA.
 * - File picker rejects unsupported extensions before any network call.
 * - Drop zone keyboard activation opens the file picker.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from './test-utils'
import UploadStep from '@/components/setup/UploadStep'

// Spy on navigation; keep the rest of react-router-dom real (MemoryRouter etc.).
const navigateMock = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})

const BUNDLE_GATE_PAYLOAD = {
  error: 'bundle_version_too_old',
  installed_version: 'v1.0.0',
  required_version: 'v2.0.0',
  vendor: 'ancestrydna',
  update_url: 'https://example.invalid/vep-bundle-v2.0.0.db',
  size_bytes: 612_345_678,
  checksum_sha256: 'a'.repeat(64),
}

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  navigateMock.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function selectFile(name = 'AncestryDNA.txt', content = 'fake content') {
  const file = new File([content], name, { type: 'text/plain' })
  const input = document.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
}

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  })
}

describe('UploadStep — Step 15 bundle-gate banner', () => {
  it('renders the bundle-gate banner when the API returns 409', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url === '/api/ingest') {
        return jsonResponse(409, { detail: BUNDLE_GATE_PAYLOAD })
      }
      return jsonResponse(200, {})
    })

    render(<UploadStep onBack={vi.fn()} />)

    selectFile()
    fireEvent.click(screen.getByText('Upload & Parse'))

    const banner = await screen.findByTestId('bundle-gate-banner')
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveTextContent(/Update VEP bundle/i)
    expect(banner).toHaveTextContent(/612 MB/)
    expect(banner).toHaveTextContent(/v2\.0\.0/)
    expect(banner).toHaveTextContent(/v1\.0\.0/)
    // Generic upload-failure block must stay hidden — 409 lives in the banner.
    expect(screen.queryByText(/Upload failed: 409/)).not.toBeInTheDocument()
  })

  it('CTA fires the vep_bundle update trigger and clears the banner on success', async () => {
    let triggerCalled = false
    let jobStatusCalls = 0
    mockFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url === '/api/ingest') {
        return jsonResponse(409, { detail: BUNDLE_GATE_PAYLOAD })
      }
      if (
        typeof url === 'string' &&
        url === '/api/updates/trigger' &&
        init?.method === 'POST'
      ) {
        triggerCalled = true
        const body = init.body ? JSON.parse(init.body as string) : {}
        expect(body.db_name).toBe('vep_bundle')
        return jsonResponse(202, {
          job_id: 'job-bundle-update',
          db_name: 'vep_bundle',
          message: 'Update queued',
        })
      }
      if (
        typeof url === 'string' &&
        url.startsWith('/api/updates/job/')
      ) {
        jobStatusCalls += 1
        return jsonResponse(200, {
          job_id: 'job-bundle-update',
          status: 'complete',
          progress_pct: 100,
          message: 'Bundle updated',
          error: null,
        })
      }
      return jsonResponse(200, {})
    })

    render(<UploadStep onBack={vi.fn()} />)

    selectFile()
    fireEvent.click(screen.getByText('Upload & Parse'))

    const cta = await screen.findByTestId('bundle-gate-update-cta')
    fireEvent.click(cta)

    await waitFor(() => {
      expect(triggerCalled).toBe(true)
    })

    await waitFor(() => {
      expect(
        screen.queryByTestId('bundle-gate-banner'),
      ).not.toBeInTheDocument()
    })
    expect(jobStatusCalls).toBeGreaterThan(0)
  })

  it('keeps the existing error block for non-409 ingest failures', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url === '/api/ingest') {
        return jsonResponse(422, { detail: 'Not a valid 23andMe file' })
      }
      return jsonResponse(200, {})
    })

    render(<UploadStep onBack={vi.fn()} />)

    selectFile('genome_data.txt')
    fireEvent.click(screen.getByText('Upload & Parse'))

    expect(
      await screen.findByText('Not a valid 23andMe file'),
    ).toBeInTheDocument()
    expect(
      screen.queryByTestId('bundle-gate-banner'),
    ).not.toBeInTheDocument()
  })

  it('renders the parsed-summary success state on 200', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url === '/api/ingest') {
        return jsonResponse(200, {
          sample_id: 1,
          variant_count: 712345,
          nocall_count: 1234,
          file_format: 'ancestrydna_v2.0',
        })
      }
      return jsonResponse(200, {})
    })

    render(<UploadStep onBack={vi.fn()} />)

    selectFile('AncestryDNA.txt')
    fireEvent.click(screen.getByText('Upload & Parse'))

    expect(await screen.findByText('Sample Uploaded')).toBeInTheDocument()
    expect(screen.getByText('712,345')).toBeInTheDocument()
    expect(screen.getByText('1,234')).toBeInTheDocument()
    expect(screen.getByText('ancestrydna_v2.0')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /go to dashboard/i }),
    ).toBeInTheDocument()
  })

  it('rejects unsupported extensions without calling /api/ingest', async () => {
    mockFetch.mockImplementation(() => jsonResponse(500, {}))

    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['bin'], 'genome.zip', {
      type: 'application/zip',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    expect(
      await screen.findByText(
        /please select a 23andme or ancestrydna raw data file/i,
      ),
    ).toBeInTheDocument()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('drop zone Enter / Space invokes the file picker', () => {
    mockFetch.mockImplementation(() => jsonResponse(200, {}))
    render(<UploadStep onBack={vi.fn()} />)

    const dropZone = screen.getByRole('button', {
      name: /select 23andme or ancestrydna raw data file to upload/i,
    })
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    // Replace the real click() with a no-op spy so happy-dom's activation
    // behavior doesn't bounce back into our onClick handler and double-count.
    const clickSpy = vi.spyOn(fileInput, 'click').mockImplementation(() => {})

    fireEvent.keyDown(dropZone, { key: 'Enter' })
    expect(clickSpy).toHaveBeenCalled()

    clickSpy.mockClear()
    fireEvent.keyDown(dropZone, { key: ' ' })
    expect(clickSpy).toHaveBeenCalled()

    // Non-activation keys must not open the picker.
    clickSpy.mockClear()
    fireEvent.keyDown(dropZone, { key: 'a' })
    expect(clickSpy).not.toHaveBeenCalled()
  })
})

/** Step 45 — Phase 1 frontend closure (ADNA-12, Plan §13.1).
 *
 * Explicit AncestryDNA-accept coverage on `<UploadStep>` independent of the
 * Step 15 bundle-gate scenarios above. Locks the contract that:
 * - the file picker accepts AncestryDNA `.txt`/`.tsv`/`.csv` filenames,
 * - drag-and-drop accepts the same,
 * - the request body POSTs as multipart `FormData` to `/api/ingest`,
 * - the 200 response renders the `ancestrydna_v2.0` summary card.
 */
describe('UploadStep — Step 45 / ADNA-12 (AncestryDNA accept)', () => {
  it('accepts an AncestryDNA-named .txt file via the file picker', () => {
    mockFetch.mockImplementation(() => jsonResponse(200, {}))
    render(<UploadStep onBack={vi.fn()} />)

    selectFile('AncestryDNA.txt', '#AncestryDNA raw data')

    expect(screen.getByText('AncestryDNA.txt')).toBeInTheDocument()
    expect(screen.getByText('Upload & Parse')).toBeInTheDocument()
    expect(
      screen.queryByText(
        /please select a 23andme or ancestrydna raw data file/i,
      ),
    ).not.toBeInTheDocument()
  })

  it('accepts AncestryDNA exports with .tsv and .csv extensions', () => {
    mockFetch.mockImplementation(() => jsonResponse(200, {}))
    render(<UploadStep onBack={vi.fn()} />)

    selectFile('AncestryDNA.tsv', 'header\n')
    expect(screen.getByText('AncestryDNA.tsv')).toBeInTheDocument()

    selectFile('AncestryDNA.csv', 'header\n')
    expect(screen.getByText('AncestryDNA.csv')).toBeInTheDocument()

    expect(
      screen.queryByText(
        /please select a 23andme or ancestrydna raw data file/i,
      ),
    ).not.toBeInTheDocument()
  })

  it('accepts an AncestryDNA file via drag-and-drop', () => {
    mockFetch.mockImplementation(() => jsonResponse(200, {}))
    render(<UploadStep onBack={vi.fn()} />)

    const dropZone = screen.getByRole('button', {
      name: /select 23andme or ancestrydna raw data file to upload/i,
    })
    const file = new File(['#AncestryDNA raw data'], 'AncestryDNA.txt', {
      type: 'text/plain',
    })

    fireEvent.drop(dropZone, {
      dataTransfer: { files: [file] },
      preventDefault: () => {},
    })

    expect(screen.getByText('AncestryDNA.txt')).toBeInTheDocument()
    expect(screen.getByText('Upload & Parse')).toBeInTheDocument()
  })

  it('POSTs the AncestryDNA file as multipart FormData to /api/ingest', async () => {
    let capturedBody: unknown = null
    let capturedMethod: string | undefined
    let capturedUrl: string | undefined
    mockFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url === '/api/ingest') {
        capturedUrl = url
        capturedMethod = init?.method
        capturedBody = init?.body
        return jsonResponse(200, {
          sample_id: 7,
          variant_count: 712_345,
          nocall_count: 1234,
          file_format: 'ancestrydna_v2.0',
        })
      }
      return jsonResponse(200, {})
    })

    render(<UploadStep onBack={vi.fn()} />)

    selectFile('AncestryDNA.txt', '#AncestryDNA raw data\nrsid\tchrom\tpos')
    fireEvent.click(screen.getByText('Upload & Parse'))

    await waitFor(() => {
      expect(capturedUrl).toBe('/api/ingest')
    })

    expect(capturedMethod).toBe('POST')
    expect(capturedBody).toBeInstanceOf(FormData)
    const formData = capturedBody as FormData
    const uploadedFile = formData.get('file')
    expect(uploadedFile).toBeInstanceOf(File)
    expect((uploadedFile as File).name).toBe('AncestryDNA.txt')
  })

  it('renders the AncestryDNA success card with ancestrydna_v2.0 file_format', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url === '/api/ingest') {
        return jsonResponse(200, {
          sample_id: 42,
          variant_count: 681_234,
          nocall_count: 945,
          file_format: 'ancestrydna_v2.0',
        })
      }
      return jsonResponse(200, {})
    })

    render(<UploadStep onBack={vi.fn()} />)

    selectFile('AncestryDNA.txt', '#AncestryDNA raw data')
    fireEvent.click(screen.getByText('Upload & Parse'))

    expect(await screen.findByText('Sample Uploaded')).toBeInTheDocument()
    expect(screen.getByText('681,234')).toBeInTheDocument()
    expect(screen.getByText('945')).toBeInTheDocument()
    expect(screen.getByText('ancestrydna_v2.0')).toBeInTheDocument()
    expect(
      screen.queryByTestId('bundle-gate-banner'),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /go to dashboard/i }),
    ).toBeInTheDocument()
  })
})

describe('UploadStep — readiness-gated dashboard hand-off', () => {
  it('delegates "Skip — Go to Dashboard" to onComplete instead of navigating directly', () => {
    mockFetch.mockImplementation(() => jsonResponse(200, {}))
    const onComplete = vi.fn()
    render(<UploadStep onBack={vi.fn()} onComplete={onComplete} />)

    // The wizard owns the readiness decision (navigate to / only when required
    // DBs are ready, else route to the Databases recovery step), so UploadStep
    // must delegate rather than navigate to a possibly-broken dashboard itself.
    fireEvent.click(screen.getByRole('button', { name: /skip — go to dashboard/i }))
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('navigates directly to the dashboard when onComplete is not provided', () => {
    mockFetch.mockImplementation(() => jsonResponse(200, {}))
    render(<UploadStep onBack={vi.fn()} />)

    // Standalone fallback (no wizard-supplied readiness hand-off): navigate
    // straight to the dashboard.
    fireEvent.click(screen.getByRole('button', { name: /skip — go to dashboard/i }))
    expect(navigateMock).toHaveBeenCalledWith('/', { replace: true })
  })
})
