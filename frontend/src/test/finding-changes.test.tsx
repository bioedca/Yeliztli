/** Tests for the finding-level "what changed" panel (SW-A4b). */

import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { FindingChangesPanel } from '@/components/settings/UpdateManager'

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

const DIFF_WITH_CHANGES = {
  available: true,
  generated_at: '2026-06-09T00:00:00Z',
  release_deltas: [{ db_name: 'clinvar', before: '2024-01', after: '2024-06' }],
  changed: [
    {
      module: 'cancer',
      category: 'monogenic_variant',
      gene_symbol: 'BRCA1',
      rsid: 'synthetic-reclassification-variant',
      drug: null,
      diplotype: null,
      finding_text: 'BRCA1 Pathogenic',
      changes: [
        {
          field: 'clinvar_significance',
          before: 'Uncertain_significance',
          after: 'Pathogenic',
        },
      ],
    },
  ],
  added: [
    {
      module: 'carrier_status',
      category: 'monogenic_variant',
      gene_symbol: 'CFTR',
      rsid: 'rs113993960',
      drug: null,
      diplotype: null,
      finding_text: 'CFTR carrier',
      clinvar_significance: 'Pathogenic',
      evidence_level: 3,
      metabolizer_status: null,
      pathway_level: null,
    },
  ],
  removed: [],
  counts: { changed: 1, added: 1, removed: 0 },
}

function mockFindingChanges(payload: unknown) {
  mockFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (typeof url === 'string' && url.includes('/api/updates/finding-changes')) {
      if (init?.method === 'POST') {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'dismissed' }) })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => payload })
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
  })
}

describe('FindingChangesPanel', () => {
  it('renders nothing when the diff is unavailable', async () => {
    mockFindingChanges({ available: false })
    const { container } = render(<FindingChangesPanel sampleId={1} />, {
      wrapper: createWrapper(),
    })
    // Give the query a tick to resolve, then assert nothing rendered.
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    expect(container.querySelector('[data-testid="finding-changes-1"]')).toBeNull()
  })

  it('renders the reclassification, additions, and release attribution', async () => {
    mockFindingChanges(DIFF_WITH_CHANGES)
    render(<FindingChangesPanel sampleId={1} />, { wrapper: createWrapper() })

    expect(
      await screen.findByText('What changed since your last analysis'),
    ).toBeInTheDocument()
    // Honest framing: source-database change, not a DNA change.
    expect(screen.getByText(/changes in the data\s+sources, not in your DNA/)).toBeInTheDocument()
    // Release-delta attribution.
    expect(screen.getByText(/clinvar 2024-01 → 2024-06/)).toBeInTheDocument()
    // The reclassified finding with its before/after.
    expect(screen.getByText('Reclassified (1)')).toBeInTheDocument()
    expect(screen.getByText('BRCA1')).toBeInTheDocument()
    expect(screen.getByText(/ClinVar significance:/)).toBeInTheDocument()
    expect(screen.getByText('Pathogenic')).toBeInTheDocument()
    // The added finding.
    expect(screen.getByText('New (1)')).toBeInTheDocument()
    expect(screen.getByText('CFTR')).toBeInTheDocument()
  })

  it('renders nothing when the fetch rejects (network error)', async () => {
    mockFetch.mockRejectedValue(new Error('Network error'))
    const { container } = render(<FindingChangesPanel sampleId={1} />, {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    expect(container.querySelector('[data-testid="finding-changes-1"]')).toBeNull()
  })

  it('renders nothing on a non-OK HTTP response', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 404,
      text: async () => 'Not found',
      json: async () => ({ detail: 'Not found' }),
    })
    const { container } = render(<FindingChangesPanel sampleId={999} />, {
      wrapper: createWrapper(),
    })
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    expect(container.querySelector('[data-testid="finding-changes-999"]')).toBeNull()
  })

  it('calls the dismiss endpoint when Dismiss is clicked', async () => {
    mockFindingChanges(DIFF_WITH_CHANGES)
    render(<FindingChangesPanel sampleId={7} />, { wrapper: createWrapper() })

    const dismissBtn = await screen.findByText('Dismiss')
    fireEvent.click(dismissBtn)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/updates/finding-changes/dismiss?sample_id=7',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })
})
