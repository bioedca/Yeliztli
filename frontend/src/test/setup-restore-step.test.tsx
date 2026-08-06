/** Tests for RestoreStep + ImportBackupStep mismatch wiring (Plan §7.6).
 *
 * RestoreStep is the bundle-version-mismatch banner the setup wizard
 * renders when ``POST /api/setup/import-backup`` returns HTTP 409 with
 * the ``bundle_version_mismatch`` payload. ImportBackupStep routes the
 * mutation error through the banner so no extraction-stage state leaks
 * into the UI.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from './test-utils'
import RestoreStep from '@/components/setup/RestoreStep'
import ImportBackupStep from '@/components/setup/ImportBackupStep'
import type { BundleVersionMismatchPayload } from '@/types/setup'

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
})

const basePayload: BundleVersionMismatchPayload = {
  error: 'bundle_version_mismatch',
  installed_version: 'v2.0.0',
  backup_version: 'v1.0.0',
  direction: 'backup_below_installed',
  sample_member: 'samples/sample_1.db',
}

// ─── RestoreStep banner — direct render ─────────────────────────────

describe('RestoreStep banner', () => {
  it('renders both versions and the below-installed headline', () => {
    render(
      <RestoreStep
        payload={basePayload}
        onRetry={vi.fn()}
        onBack={vi.fn()}
      />,
    )
    expect(
      screen.getByText(/backup is older than the installed bundle/i),
    ).toBeInTheDocument()
    // Both versions appear in the headline paragraph AND the summary rows.
    expect(screen.getAllByText('v1.0.0').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('v2.0.0').length).toBeGreaterThanOrEqual(1)
  })

  it('flips headline + guidance for the opposite direction', () => {
    render(
      <RestoreStep
        payload={{
          ...basePayload,
          installed_version: 'v1.0.0',
          backup_version: 'v2.0.0',
          direction: 'backup_above_installed',
        }}
        onRetry={vi.fn()}
        onBack={vi.fn()}
      />,
    )
    expect(
      screen.getByText(/backup is newer than the installed bundle/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/upgrade the installed vep bundle/i),
    ).toBeInTheDocument()
  })

  it('exposes accessible alert role for screen readers', () => {
    render(
      <RestoreStep
        payload={basePayload}
        onRetry={vi.fn()}
        onBack={vi.fn()}
      />,
    )
    const banner = screen.getByRole('alert')
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveAttribute('aria-live', 'polite')
  })

  it('invokes onRetry / onBack from the action buttons', () => {
    const onRetry = vi.fn()
    const onBack = vi.fn()
    render(
      <RestoreStep payload={basePayload} onRetry={onRetry} onBack={onBack} />,
    )
    fireEvent.click(screen.getByText(/choose a different backup/i))
    expect(onRetry).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByText(/^back$/i))
    expect(onBack).toHaveBeenCalledOnce()
  })
})

// ─── ImportBackupStep wiring — 409 → banner ─────────────────────────

function mockDetectExisting(overrides: Record<string, unknown> = {}) {
  return {
    existing_found: false,
    has_config: false,
    has_samples: false,
    has_databases: false,
    data_dir: '/tmp/test',
    ...overrides,
  }
}

describe('ImportBackupStep — bundle-version mismatch wiring', () => {
  it('renders the RestoreStep banner when the API returns 409 mismatch', async () => {
    mockFetch
      // detect-existing call (returns no existing install)
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockDetectExisting()),
      })
      // import-backup call returns 409 with the §7.6 payload
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail: basePayload }),
      })

    render(<ImportBackupStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Import from Backup')).toBeInTheDocument()
    })

    // Simulate a user dropping a .tar.gz file into the drop zone.
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    const file = new File(['archive'], 'backup.tar.gz', {
      type: 'application/gzip',
    })
    Object.defineProperty(fileInput, 'files', { value: [file] })
    fireEvent.change(fileInput)

    // Trigger the import button.
    fireEvent.click(await screen.findByRole('button', { name: /import backup/i }))

    // RestoreStep banner replaces the upload UI on the 409 response.
    await waitFor(() => {
      expect(
        screen.getByTestId('restore-bundle-mismatch'),
      ).toBeInTheDocument()
    })
    expect(
      screen.getByText(/backup is older than the installed bundle/i),
    ).toBeInTheDocument()
    expect(screen.getAllByText('v1.0.0').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('v2.0.0').length).toBeGreaterThanOrEqual(1)
  })

  it('falls back to the generic error path on a non-mismatch 409', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockDetectExisting()),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () =>
          Promise.resolve({ detail: 'A backup export is already in progress.' }),
      })

    render(<ImportBackupStep onNext={vi.fn()} onBack={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('Import from Backup')).toBeInTheDocument()
    })

    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    const file = new File(['archive'], 'backup.tar.gz', {
      type: 'application/gzip',
    })
    Object.defineProperty(fileInput, 'files', { value: [file] })
    fireEvent.change(fileInput)

    fireEvent.click(await screen.findByRole('button', { name: /import backup/i }))

    // Generic error state — banner must NOT appear.
    await waitFor(() => {
      expect(
        screen.getByText('Unable to import the backup. Please try again.'),
      ).toBeInTheDocument()
    })
    expect(
      screen.queryByText(/a backup export is already in progress/i),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('restore-bundle-mismatch'),
    ).not.toBeInTheDocument()
  })
})
