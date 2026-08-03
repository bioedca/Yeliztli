import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from './test-utils'
import ExportBackup from '@/components/settings/ExportBackup'

const mockFetch = vi.fn()

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }
}

function renderFailedExport(error: string | null) {
  mockFetch.mockImplementation((url: string) => {
    if (url === '/api/backup/estimate') {
      return Promise.resolve(
        jsonResponse({
          sample_bytes: 0,
          config_bytes: 0,
          reference_bytes: 0,
          total_without_ref_bytes: 0,
          total_with_ref_bytes: 0,
          total_without_ref_mb: 0,
          total_with_ref_mb: 0,
          sample_count: 0,
          reference_db_count: 0,
        }),
      )
    }
    if (url === '/api/backup/export') {
      return Promise.resolve(jsonResponse({ job_id: 'backup-job', message: 'Queued' }, 202))
    }
    if (url === '/api/backup/status/backup-job') {
      return Promise.resolve(
        jsonResponse({
          job_id: 'backup-job',
          status: 'failed',
          progress_pct: 0,
          message: 'Export failed',
          error,
          download_filename: null,
        }),
      )
    }
    return Promise.resolve(jsonResponse({}))
  })
  render(<ExportBackup />)
}

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ExportBackup', () => {
  it.each([
    ['without a backend error', null],
    ['without exposing a backend error', 'sqlite:///private/data/reference.db: permission denied'],
  ])('shows recovery guidance for a failed backup %s', async (_label, error) => {
    renderFailedExport(error)

    fireEvent.click(await screen.findByTestId('export-backup-btn'))

    const recovery = await screen.findByRole('alert')
    expect(recovery).toHaveTextContent('Please try again.')
    expect(screen.getByText('Backup export failed')).toBeInTheDocument()
    expect(screen.getByTestId('export-backup-btn')).toBeEnabled()
    if (error) expect(document.body).not.toHaveTextContent(error)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/backup/status/backup-job')
    })
  })
})
