/** Tests for QC charts (P1-21).
 *
 * Tests the QualityControl component with QC stats data,
 * and verifies chart components render correctly.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from './test-utils'
import QualityControl from '@/components/dashboard/QualityControl'
import ChromosomeBarChart from '@/components/charts/ChromosomeBarChart'
import HeterozygosityHistogram from '@/components/charts/HeterozygosityHistogram'
import type { QCStats, ChromosomeQCStats } from '@/types/variants'
import type { QCMetrics } from '@/types/qc'

// Mock react-plotly.js since it requires a browser canvas. Expose each trace's
// name + y-values so tests can assert the DATA reaching the chart (the
// het/hom/nocall counts and the per-chromosome het rates), not just the count
// of traces.
vi.mock('react-plotly.js', () => ({
  default: ({
    data,
    layout,
  }: {
    data: Array<{ name?: string; y?: number[] }>
    layout: { title?: { text?: string } }
  }) => (
    <div
      data-testid="plotly-chart"
      data-title={layout?.title?.text}
      data-traces={JSON.stringify(data.map((t) => ({ name: t.name, y: t.y })))}
    >
      <span data-testid="plotly-trace-count">{data.length}</span>
    </div>
  ),
}))

function traceMap(chart: HTMLElement): Record<string, number[]> {
  const traces = JSON.parse(chart.getAttribute('data-traces') ?? '[]') as Array<{
    name: string
    y: number[]
  }>
  return Object.fromEntries(traces.map((t) => [t.name, t.y]))
}

const MOCK_QC_STATS: QCStats = {
  total_variants: 623841,
  called_variants: 610000,
  nocall_variants: 13841,
  het_count: 210000,
  hom_count: 400000,
  call_rate: 0.977817,
  heterozygosity_rate: 0.344262,
  per_chromosome: [
    { chrom: '1', total: 50000, het_count: 17000, hom_count: 32000, nocall_count: 1000 },
    { chrom: '2', total: 45000, het_count: 15500, hom_count: 28500, nocall_count: 1000 },
    { chrom: '3', total: 38000, het_count: 13000, hom_count: 24000, nocall_count: 1000 },
    { chrom: 'X', total: 22000, het_count: 4000, hom_count: 17500, nocall_count: 500 },
    { chrom: 'MT', total: 800, het_count: 0, hom_count: 780, nocall_count: 20 },
  ],
}

const MOCK_QC_METRICS: QCMetrics = {
  computed: true,
  call_rate: 0.977817,
  call_rate_pass: true,
  heterozygosity_rate: 0.344262,
  ti_tv_ratio: 2.08,
  total_variants: 623841,
  called_variants: 610000,
  nocall_variants: 13841,
  genetic_sex: 'XX',
  recorded_sex: 'XY',
  sex_check: 'discordant',
  het_outlier_z: null,
  het_outlier_status: 'insufficient_comparable_samples',
}

// ─── QualityControl with QC stats ────────────────────────────────────

describe('QualityControl with QC stats', () => {
  it('shows call rate and het rate when qcStats provided', () => {
    render(<QualityControl variantCount={623841} qcStats={MOCK_QC_STATS} />)
    fireEvent.click(screen.getByText('Sample QC'))
    expect(screen.getByText('97.78%')).toBeInTheDocument()
    expect(screen.getByText('34.43%')).toBeInTheDocument()
  })

  it('renders charts when qcStats is provided', () => {
    render(<QualityControl variantCount={623841} qcStats={MOCK_QC_STATS} />)
    fireEvent.click(screen.getByText('Sample QC'))
    const charts = screen.getAllByTestId('plotly-chart')
    expect(charts).toHaveLength(2)
  })

  it('shows placeholder text when qcStats is null', () => {
    render(<QualityControl variantCount={623841} qcStats={null} />)
    fireEvent.click(screen.getByText('Sample QC'))
    expect(screen.getByText(/Detailed QC charts/)).toBeInTheDocument()
    expect(screen.queryByTestId('plotly-chart')).not.toBeInTheDocument()
  })

  it('shows placeholder text when qcStats is undefined', () => {
    render(<QualityControl variantCount={623841} />)
    fireEvent.click(screen.getByText('Sample QC'))
    expect(screen.getByText(/Detailed QC charts/)).toBeInTheDocument()
  })

  it('shows dashes for call rate and het rate when no qcStats', () => {
    render(<QualityControl variantCount={623841} qcStats={null} />)
    fireEvent.click(screen.getByText('Sample QC'))
    // variant count is shown, call rate and het rate are dashes
    expect(screen.getByText('623,841')).toBeInTheDocument()
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBe(2)
  })

  it('surfaces interpretive QC metrics from /analysis/qc/metrics', () => {
    render(
      <QualityControl
        variantCount={623841}
        qcStats={MOCK_QC_STATS}
        qcMetrics={MOCK_QC_METRICS}
      />,
    )
    fireEvent.click(screen.getByText('Sample QC'))

    expect(screen.getByText('Heterozygosity check')).toBeInTheDocument()
    expect(screen.getByText('No comparable array peers')).toBeInTheDocument()
    expect(
      screen.getByText('No other samples on the same genotyping array to compare against.'),
    ).toBeInTheDocument()

    expect(screen.getByText('Sex concordance')).toBeInTheDocument()
    expect(screen.getByText('Discordant')).toBeInTheDocument()
    expect(screen.getByText('Recorded and inferred sex are discordant.')).toBeInTheDocument()
    expect(screen.getByText(/Inferred: XX/)).toBeInTheDocument()
    expect(screen.getByText(/Recorded: XY/)).toBeInTheDocument()
    expect(screen.getByText('Concordance check only; not an aneuploidy assessment.')).toBeInTheDocument()
  })

  it('does not render interpretive QC section before metrics are computed', () => {
    render(
      <QualityControl
        variantCount={623841}
        qcStats={MOCK_QC_STATS}
        qcMetrics={{ ...MOCK_QC_METRICS, computed: false }}
      />,
    )
    fireEvent.click(screen.getByText('Sample QC'))

    expect(screen.queryByTestId('qc-interpretive-metrics')).not.toBeInTheDocument()
  })
})

// ─── ChromosomeBarChart ──────────────────────────────────────────────

describe('ChromosomeBarChart', () => {
  it('renders a Plotly chart with 3 traces (het, hom, nocall)', () => {
    render(<ChromosomeBarChart data={MOCK_QC_STATS.per_chromosome} />)
    const chart = screen.getByTestId('plotly-chart')
    expect(chart).toBeInTheDocument()
    expect(chart.getAttribute('data-title')).toBe('Variants per Chromosome')
    expect(screen.getByTestId('plotly-trace-count').textContent).toBe('3')

    // Each series must carry its per-chromosome counts in order (chr1..MT), so a
    // het↔hom swap or a dropped no-call series is caught.
    const y = traceMap(chart)
    expect(y['Heterozygous']).toEqual([17000, 15500, 13000, 4000, 0])
    expect(y['Homozygous']).toEqual([32000, 28500, 24000, 17500, 780])
    expect(y['No-call']).toEqual([1000, 1000, 1000, 500, 20])
  })
})

// ─── HeterozygosityHistogram ─────────────────────────────────────────

describe('HeterozygosityHistogram', () => {
  it('renders a Plotly chart with 1 trace', () => {
    render(
      <HeterozygosityHistogram
        data={MOCK_QC_STATS.per_chromosome}
        overallRate={MOCK_QC_STATS.heterozygosity_rate}
      />,
    )
    const chart = screen.getByTestId('plotly-chart')
    expect(chart).toBeInTheDocument()
    expect(chart.getAttribute('data-title')).toBe('Heterozygosity Rate by Chromosome')
    expect(screen.getByTestId('plotly-trace-count').textContent).toBe('1')

    // The single trace's y must be het/(het+hom) per chromosome — not het/total,
    // not a stray count. Literal expectations distinguish the correct ratio.
    const rates = traceMap(chart)['Het rate']
    expect(rates).toHaveLength(5)
    expect(rates[0]).toBeCloseTo(17000 / 49000, 4) // chr1 0.3469
    expect(rates[1]).toBeCloseTo(15500 / 44000, 4) // chr2 0.3523
    expect(rates[2]).toBeCloseTo(13000 / 37000, 4) // chr3 0.3514
    expect(rates[3]).toBeCloseTo(4000 / 21500, 4) // chrX 0.1860
    expect(rates[4]).toBeCloseTo(0, 4) // chrMT 0/780
  })

  it('filters out chromosomes with 0 called variants', () => {
    const dataWithZero: ChromosomeQCStats[] = [
      { chrom: '1', total: 100, het_count: 30, hom_count: 70, nocall_count: 0 },
      { chrom: '2', total: 50, het_count: 0, hom_count: 0, nocall_count: 50 }, // all nocall
    ]
    render(<HeterozygosityHistogram data={dataWithZero} overallRate={0.3} />)
    const chart = screen.getByTestId('plotly-chart')
    expect(chart).toBeInTheDocument()
    // chr2 (het+hom == 0) must be dropped, leaving exactly chr1's single rate.
    const rates = traceMap(chart)['Het rate']
    expect(rates).toEqual([30 / 100])
  })
})
