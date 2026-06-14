/** Step 15 — Setup wizard disk-space pre-check (Plan §12.1, ADNA-00d).
 *
 * Covers:
 * - Per-DB size breakdown is rendered
 * - VEP bundle ~600 MB callout names AncestryDNA v2.0 union catalog
 * - Existing "approximately 4 GB" hint remains for the high-level summary
 * - Continue button drives `useSetStoragePath` and onNext (non-blocked path)
 * - Custom location radio surfaces the custom path input
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from './test-utils'
import StorageStep from '@/components/setup/StorageStep'

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function mockStorageInfo(overrides: Record<string, unknown> = {}) {
  return {
    data_dir: '/home/test/.yeliztli',
    free_space_bytes: 50 * 1024 * 1024 * 1024,
    free_space_gb: 50,
    total_space_bytes: 100 * 1024 * 1024 * 1024,
    total_space_gb: 100,
    status: 'ok' as const,
    message: '50.0 GB free — sufficient for Yeliztli.',
    path_exists: true,
    path_writable: true,
    volatile: false,
    volatile_message: null,
    ...overrides,
  }
}

describe('StorageStep — Step 15 disk-space pre-check', () => {
  it('keeps the 4 GB headline summary', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Storage Location')).toBeInTheDocument()
    })

    expect(
      screen.getByText(/approximately 4 GB of disk space/i),
    ).toBeInTheDocument()
  })

  it('warns when the storage path is on a volatile filesystem', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(
          mockStorageInfo({
            data_dir: '/tmp/yeliztli',
            volatile: true,
            volatile_message:
              'This location is on a volatile filesystem (e.g. /tmp) that is ' +
              'typically erased when the machine restarts.',
          }),
        ),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    expect(
      await screen.findByText('Volatile storage location'),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/erased when the machine restarts/i),
    ).toBeInTheDocument()
  })

  it('omits the volatile warning for a persistent path', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await screen.findByText(/Disk Space OK/i)
    expect(
      screen.queryByText('Volatile storage location'),
    ).not.toBeInTheDocument()
  })

  it('renders the per-DB size breakdown panel', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    const breakdown = await screen.findByTestId('storage-db-breakdown')
    expect(breakdown).toBeInTheDocument()
    expect(breakdown).toHaveTextContent(/reference database size breakdown/i)
    expect(breakdown).toHaveTextContent(/gnomAD/i)
    expect(breakdown).toHaveTextContent(/dbNSFP/i)
    expect(breakdown).toHaveTextContent(/LAI bundle/i)
  })

  it('calls out the ~600 MB VEP bundle for AncestryDNA v2.0', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    const breakdown = await screen.findByTestId('storage-db-breakdown')
    expect(breakdown).toHaveTextContent(/VEP bundle/i)
    expect(breakdown).toHaveTextContent(/600 MB/)
    expect(breakdown).toHaveTextContent(/AncestryDNA v2\.0/i)
    expect(breakdown).toHaveTextContent(/0\.2\.0\+/)
  })

  it('Continue button invokes set-storage-path and advances on a non-blocked result', async () => {
    const onNext = vi.fn()
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.endsWith('/api/setup/storage-info')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockStorageInfo()),
        })
      }
      if (typeof url === 'string' && url.endsWith('/api/setup/set-storage-path')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: 'ok',
              path: '/home/test/.yeliztli',
              free_space_gb: 50,
              message: 'OK',
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })

    render(<StorageStep onNext={onNext} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Storage Location')).toBeInTheDocument()
    })
    // Wait for storageInfo to render so the Continue button is enabled.
    await screen.findByText(/Disk Space OK/i)

    fireEvent.click(screen.getByText(/Continue/i))

    await waitFor(() => {
      expect(onNext).toHaveBeenCalledOnce()
    })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/setup/set-storage-path',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('toggling the Custom location radio surfaces the custom path input', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/disk space ok/i)).toBeInTheDocument()
    })

    const customRadio = document.getElementById(
      'storage-path-custom',
    ) as HTMLInputElement
    expect(customRadio).not.toBeNull()
    fireEvent.click(customRadio)

    const customInput = await screen.findByLabelText(/custom storage path/i)
    expect(customInput).toBeInTheDocument()

    fireEvent.change(customInput, { target: { value: '/data/yeliztli' } })
    expect((customInput as HTMLInputElement).value).toBe('/data/yeliztli')

    // Continue stays disabled while the custom path is empty — covers the
    // (useCustomPath && !customPath.trim()) branch.
    fireEvent.change(customInput, { target: { value: '   ' } })
    const continueBtn = screen.getByRole('button', { name: /continue/i })
    expect(continueBtn).toBeDisabled()
  })
})
