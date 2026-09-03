import type { ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render as baseRender } from '@testing-library/react'
import { render, screen, fireEvent, within } from './test-utils'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from '@/pages/Dashboard'
import StatusBar from '@/components/dashboard/StatusBar'
import ModuleCard from '@/components/dashboard/ModuleCard'
import ModuleCardsGrid from '@/components/dashboard/ModuleCardsGrid'
import FindingsPreview from '@/components/dashboard/FindingsPreview'
import QualityControl from '@/components/dashboard/QualityControl'
import { Pill } from 'lucide-react'

// Mock react-plotly.js to avoid canvas dependency in test env
vi.mock('react-plotly.js', () => ({
  default: ({ layout }: { layout: { title?: { text?: string } } }) => (
    <div data-testid="plotly-chart" data-title={layout?.title?.text} />
  ),
}))

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
})

// ─── Helpers ────────────────────────────────────────────────

function mockSamplesResponse(samples: unknown[] = []) {
  return {
    ok: true,
    status: 200,
    json: async () => samples,
  }
}

function mockVariantCountResponse(total = 623841) {
  return {
    ok: true,
    status: 200,
    json: async () => ({ total }),
  }
}

function mockDatabaseListResponse(downloaded = 3, total = 4) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      databases: [],
      total_size_bytes: 0,
      downloaded_count: downloaded,
      total_count: total,
    }),
  }
}

function mockQCStatsResponse() {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      total_variants: 623841,
      called_variants: 610000,
      nocall_variants: 13841,
      het_count: 210000,
      hom_count: 400000,
      call_rate: 0.977817,
      heterozygosity_rate: 0.344262,
      per_chromosome: [],
    }),
  }
}

function mockQCMetricsResponse() {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      computed: true,
      call_rate: 0.977817,
      call_rate_pass: true,
      heterozygosity_rate: 0.344262,
      ti_tv_ratio: 2.08,
      total_variants: 623841,
      called_variants: 610000,
      nocall_variants: 13841,
      genetic_sex: 'XX',
      recorded_sex: 'XX',
      sex_check: 'concordant',
      het_outlier_z: null,
      het_outlier_status: 'within_range',
    }),
  }
}

function mockUpdateStatusResponse(statuses?: unknown[]) {
  return {
    ok: true,
    status: 200,
    json: async () => statuses ?? [
      { db_name: 'clinvar', display_name: 'ClinVar', current_version: '20260315', version_display: 'Mar 2026', downloaded_at: '2026-03-15T00:00:00', auto_update: true, update_available: false },
      { db_name: 'gnomad', display_name: 'gnomAD', current_version: '2.1.1', version_display: '2.1.1', downloaded_at: '2026-03-01T00:00:00', auto_update: false, update_available: false },
      { db_name: 'dbnsfp', display_name: 'dbNSFP', current_version: null, version_display: null, downloaded_at: null, auto_update: false, update_available: false },
      { db_name: 'vep_bundle', display_name: 'VEP Bundle', current_version: null, version_display: null, downloaded_at: null, auto_update: false, update_available: false },
    ],
  }
}

function mockUpdateCheckResponse(available?: unknown[]) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      available: available ?? [],
      up_to_date: ['clinvar', 'gnomad'],
      errors: [],
      checked_at: new Date().toISOString(),
    }),
  }
}

function setupFetchMocks(options: {
  samples?: unknown[]
  variantCount?: number
  dbDownloaded?: number
  dbTotal?: number
  updateStatuses?: unknown[]
  updatesAvailable?: unknown[]
} = {}) {
  mockFetch.mockImplementation((url: string) => {
    if (url.includes('/api/samples')) {
      return Promise.resolve(mockSamplesResponse(options.samples ?? []))
    }
    if (url.includes('/api/individuals')) {
      // Dashboard's two-level context chip (Step 50) calls
      // useIndividuals() to discover the owning individual of the active
      // sample. Tests don't exercise that surface, so return an empty
      // list to keep the chip suppressed without breaking renders.
      return Promise.resolve({ ok: true, status: 200, json: async () => [] })
    }
    if (url.includes('/api/variants/qc-stats')) {
      return Promise.resolve(mockQCStatsResponse())
    }
    if (url.includes('/api/analysis/qc/metrics')) {
      return Promise.resolve(mockQCMetricsResponse())
    }
    if (url.includes('/api/variants/count')) {
      return Promise.resolve(mockVariantCountResponse(options.variantCount ?? 623841))
    }
    if (url.includes('/api/updates/status')) {
      return Promise.resolve(mockUpdateStatusResponse(options.updateStatuses))
    }
    if (url.includes('/api/updates/check')) {
      return Promise.resolve(mockUpdateCheckResponse(options.updatesAvailable))
    }
    if (url.includes('/api/databases')) {
      return Promise.resolve(mockDatabaseListResponse(
        options.dbDownloaded ?? 3,
        options.dbTotal ?? 4,
      ))
    }
    return Promise.resolve({ ok: true, json: async () => ({}) })
  })
}

function createWrapper(initialEntries: string[] = ['/']) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        {children}
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const SAMPLE = {
  id: 1,
  name: 'Eduardo',
  db_path: '/tmp/sample_1.db',
  file_format: '23andme_v5',
  file_hash: 'abc123',
  notes: null,
  date_collected: null,
  source: null,
  extra: null,
  created_at: new Date().toISOString(),
  updated_at: null,
}

// ─── Dashboard page ─────────────────────────────────────────

describe('Dashboard', () => {
  it('shows upload prompt when no sample is active', async () => {
    setupFetchMocks()
    baseRender(<Dashboard />, { wrapper: createWrapper() })
    expect(await screen.findByText('Get Started')).toBeInTheDocument()
    expect(
      screen.getByText(/Upload a 23andMe or AncestryDNA raw data file/),
    ).toBeInTheDocument()
  })

  it('renders dashboard layout when sample is active', async () => {
    setupFetchMocks({ samples: [SAMPLE], variantCount: 500000 })
    baseRender(<Dashboard />, { wrapper: createWrapper(['/?sample_id=1']) })
    expect(await screen.findByText('Eduardo')).toBeInTheDocument()
  })

  it('renders the Viewing context chip when the active sample is linked to an individual (Step 50)', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.includes('/api/samples')) {
        return Promise.resolve(mockSamplesResponse([SAMPLE]))
      }
      if (url === '/api/individuals') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            {
              id: 99,
              display_name: 'Alice',
              notes: null,
              biological_sex: null,
              created_at: '2026-05-01T00:00:00',
              updated_at: null,
              sample_count: 1,
              vendors: ['23andme'],
              last_activity: null,
            },
          ],
        })
      }
      if (/^\/api\/individuals\/99/.test(url)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            id: 99,
            display_name: 'Alice',
            notes: null,
            biological_sex: null,
            created_at: '2026-05-01T00:00:00',
            updated_at: null,
            linked_samples: [
              {
                id: 1,
                name: 'Eduardo',
                file_format: '23andme_v5',
                vendor: '23andme',
                created_at: '2026-05-01T00:00:00',
                updated_at: null,
              },
            ],
            aggregated_findings_count: 0,
          }),
        })
      }
      if (url.includes('/api/variants/qc-stats')) {
        return Promise.resolve(mockQCStatsResponse())
      }
      if (url.includes('/api/analysis/qc/metrics')) {
        return Promise.resolve(mockQCMetricsResponse())
      }
      if (url.includes('/api/variants/count')) {
        return Promise.resolve(mockVariantCountResponse(500000))
      }
      if (url.includes('/api/updates/status')) {
        return Promise.resolve(mockUpdateStatusResponse())
      }
      if (url.includes('/api/updates/check')) {
        return Promise.resolve(mockUpdateCheckResponse())
      }
      if (url.includes('/api/databases')) {
        return Promise.resolve(mockDatabaseListResponse())
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })

    baseRender(<Dashboard />, { wrapper: createWrapper(['/?sample_id=1']) })

    const chip = await screen.findByTestId('dashboard-context-chip')
    expect(chip).toHaveTextContent(/Viewing:/)
    expect(chip).toHaveTextContent('Alice')
    expect(chip).toHaveTextContent('Eduardo')
    const link = screen.getByRole('link', { name: 'Alice' })
    expect(link).toHaveAttribute('href', '/individuals/99')
  })
})

// ─── StatusBar ──────────────────────────────────────────────

describe('StatusBar', () => {
  it('displays sample name and variant count', async () => {
    setupFetchMocks()
    render(<StatusBar sample={SAMPLE} variantCount={623841} />)
    expect(screen.getByText('Eduardo')).toBeInTheDocument()
    expect(screen.getByText(/623,841 SNPs/)).toBeInTheDocument()
  })

  it('shows null variant count as no SNP text', () => {
    setupFetchMocks()
    render(<StatusBar sample={SAMPLE} variantCount={null} />)
    expect(screen.getByText('Eduardo')).toBeInTheDocument()
    expect(screen.queryByText(/SNPs/)).not.toBeInTheDocument()
  })

  it('has accessible database status button', async () => {
    setupFetchMocks()
    render(<StatusBar sample={SAMPLE} variantCount={100} />)
    const dbButton = await screen.findByRole('button', { name: /Databases/i })
    expect(dbButton).toBeInTheDocument()
  })

  it('shows database version dots based on update status', async () => {
    setupFetchMocks({
      updateStatuses: [
        { db_name: 'clinvar', display_name: 'ClinVar', current_version: '20260315', version_display: 'Mar 2026', downloaded_at: '2026-03-15T00:00:00', auto_update: true, update_available: false },
        { db_name: 'gnomad', display_name: 'gnomAD', current_version: '2.1.1', version_display: '2.1.1', downloaded_at: '2026-03-01T00:00:00', auto_update: false, update_available: false },
      ],
    })
    render(<StatusBar sample={SAMPLE} variantCount={100} />)
    // Wait for update status to load — aria-label reflects current/update counts
    const dbButton = await screen.findByRole('button', { name: /2 current/i })
    expect(dbButton).toBeInTheDocument()
  })
})

// ─── ModuleCard ─────────────────────────────────────────────

describe('ModuleCard', () => {
  it('renders with label and description', () => {
    render(
      <ModuleCard
        to="/pharmacogenomics"
        label="Pharmacogenomics"
        icon={Pill}
        description="Drug-gene interactions"
      />,
    )
    expect(screen.getByText('Pharmacogenomics')).toBeInTheDocument()
    expect(screen.getByText('Drug-gene interactions')).toBeInTheDocument()
    expect(screen.getByText('View details →')).toBeInTheDocument()
  })

  it('links to the correct route', () => {
    render(
      <ModuleCard
        to="/pharmacogenomics"
        label="Pharmacogenomics"
        icon={Pill}
        description="Test"
      />,
    )
    const link = screen.getByRole('link', { name: /Pharmacogenomics module/i })
    expect(link).toHaveAttribute('href', '/pharmacogenomics')
  })

  it('shows gate text when gated', () => {
    render(
      <ModuleCard
        to="/apoe"
        label="APOE"
        icon={Pill}
        description="Should not show"
        gated
        gateText="Tap to learn more"
      />,
    )
    expect(screen.getByText('Tap to learn more')).toBeInTheDocument()
    expect(screen.queryByText('Should not show')).not.toBeInTheDocument()
  })
})

// ─── ModuleCardsGrid ────────────────────────────────────────

describe('ModuleCardsGrid', () => {
  it('renders all 12 module cards', () => {
    render(<ModuleCardsGrid sampleId={null} />)
    expect(screen.getByText('Pharmacogenomics')).toBeInTheDocument()
    expect(screen.getByText('Nutrigenomics')).toBeInTheDocument()
    expect(screen.getByText('Cancer')).toBeInTheDocument()
    expect(screen.getByText('Cardiovascular')).toBeInTheDocument()
    expect(screen.getByText('APOE')).toBeInTheDocument()
    expect(screen.getByText('Carrier Status')).toBeInTheDocument()
    expect(screen.getByText('Ancestry')).toBeInTheDocument()
    expect(screen.getByText('Fitness')).toBeInTheDocument()
    expect(screen.getByText('Sleep')).toBeInTheDocument()
    expect(screen.getByText('Allergy')).toBeInTheDocument()
    expect(screen.getByText('Traits & Personality')).toBeInTheDocument()
    expect(screen.getByText('Gene Health')).toBeInTheDocument()
  })

  it('carries the active sample to every module card', () => {
    setupFetchMocks()
    render(<ModuleCardsGrid sampleId={7} />)

    const links = within(
      screen.getByRole('region', { name: /Analysis modules/i }),
    ).getAllByRole('link')

    expect(links).toHaveLength(12)
    for (const link of links) {
      const url = new URL(link.getAttribute('href')!, 'http://localhost')
      expect(url.searchParams.get('sample_id')).toBe('7')
    }
  })

  it('has an accessible section label', () => {
    render(<ModuleCardsGrid sampleId={null} />)
    expect(screen.getByRole('region', { name: /Analysis modules/i })).toBeInTheDocument()
  })

  it('shows APOE as gated', () => {
    render(<ModuleCardsGrid sampleId={null} />)
    expect(screen.getByText('Tap to learn more')).toBeInTheDocument()
  })
})

// ─── FindingsPreview ────────────────────────────────────────

describe('FindingsPreview', () => {
  it('shows empty state placeholder', () => {
    render(<FindingsPreview sampleId={null} />)
    expect(screen.getByText('High-Confidence Findings')).toBeInTheDocument()
    expect(screen.getByText('No findings yet')).toBeInTheDocument()
    expect(screen.getByText(/Run annotation/)).toBeInTheDocument()
  })

  it('has an accessible section label', () => {
    render(<FindingsPreview sampleId={null} />)
    expect(screen.getByRole('region', { name: /High-confidence findings/i })).toBeInTheDocument()
  })

  // ── #2047: the count under this heading must describe THIS heading ────────
  //
  // The link printed `summary.total_findings` -- the unfiltered all-module
  // count -- directly beneath "High-Confidence Findings". On a real sample that
  // read as 311,472 high-confidence findings when there were 55, because
  // 311,359 of them are one-star rare variants.

  const summaryWith = (levels: Array<[number, number]>, total: number) => ({
    total_findings: total,
    modules: [],
    evidence_level_counts: levels.map(([evidence_level, count]) => ({
      evidence_level,
      count,
    })),
    high_confidence_findings: [
      {
        id: 1,
        module: 'gene_health',
        finding_text: 'Example high-confidence finding',
        evidence_level: 4,
      },
    ],
  })

  const renderPreview = (summary: unknown) => {
    mockFetch.mockImplementation((url: string) => {
      if (String(url).includes('/api/analysis/findings/summary')) {
        return Promise.resolve({ ok: true, json: async () => summary })
      }
      return Promise.resolve({ ok: true, json: async () => [] })
    })
    return render(<FindingsPreview sampleId={2} />, { route: '/?sample_id=2' })
  }

  it('counts only >=3-star findings, not the unfiltered total (#2047)', async () => {
    renderPreview(
      summaryWith(
        [
          [1, 311359],
          [2, 58],
          [3, 27],
          [4, 28],
        ],
        311472,
      ),
    )

    const link = await screen.findByRole('link', { name: /Show all/ })
    // 27 + 28 = 55, not 311,472.
    expect(link).toHaveTextContent('Show all 55')
    expect(link).not.toHaveTextContent('311472')
    expect(link).not.toHaveTextContent('311,472')
  })

  it('links to the set the count describes (#2047)', async () => {
    renderPreview(
      summaryWith(
        [
          [1, 100],
          [4, 7],
        ],
        107,
      ),
    )

    const link = await screen.findByRole('link', { name: /Show all/ })
    expect(link).toHaveTextContent('Show all 7')
    // Without minStars the destination shows all 107 -- the label would name one
    // set and the page would show another.
    expect(link).toHaveAttribute('href', expect.stringContaining('minStars=3'))
    expect(link).toHaveAttribute('href', expect.stringContaining('sample_id=2'))
  })

  it('omits the number when the API gives no per-level counts (#2047)', async () => {
    renderPreview({
      total_findings: 311472,
      modules: [],
      high_confidence_findings: [
        { id: 1, module: 'gene_health', finding_text: 'x', evidence_level: 4 },
      ],
    })

    const link = await screen.findByRole('link', { name: /Show all/ })
    // Say nothing rather than fall back to the misleading total.
    expect(link).toHaveTextContent('Show all findings')
    expect(link).not.toHaveTextContent('311472')
  })
})

// ─── QualityControl ─────────────────────────────────────────

describe('QualityControl', () => {
  it('renders collapsed by default', () => {
    render(<QualityControl variantCount={623841} />)
    expect(screen.getByText('Sample QC')).toBeInTheDocument()
    expect(screen.queryByText('Total Variants')).not.toBeInTheDocument()
  })

  it('expands to show variant count', () => {
    render(<QualityControl variantCount={623841} />)
    fireEvent.click(screen.getByText('Sample QC'))
    expect(screen.getByText('Total Variants')).toBeInTheDocument()
    expect(screen.getByText('623,841')).toBeInTheDocument()
  })

  it('shows dash when variant count is null', () => {
    render(<QualityControl variantCount={null} />)
    fireEvent.click(screen.getByText('Sample QC'))
    // variant count + call rate + het rate all show "—"
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBe(3)
  })

  it('shows labels for Call Rate and Het Rate', () => {
    render(<QualityControl variantCount={100} />)
    fireEvent.click(screen.getByText('Sample QC'))
    expect(screen.getByText('Call Rate')).toBeInTheDocument()
    expect(screen.getByText('Het Rate')).toBeInTheDocument()
  })

  it('has accessible expand/collapse button', () => {
    render(<QualityControl variantCount={100} />)
    const button = screen.getByRole('button', { name: /Sample QC/i })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
  })

  it('collapses when clicked again', () => {
    render(<QualityControl variantCount={100} />)
    const button = screen.getByText('Sample QC')
    fireEvent.click(button)
    expect(screen.getByText('Total Variants')).toBeInTheDocument()
    fireEvent.click(button)
    expect(screen.queryByText('Total Variants')).not.toBeInTheDocument()
  })
})
