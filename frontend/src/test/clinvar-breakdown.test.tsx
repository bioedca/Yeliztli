/** Tests for ClinVar significance breakdown bar chart (P2-26).
 *
 * T2-23: ClinVar bar chart correctly groups by significance.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from './test-utils'
import ClinvarBreakdown from '@/components/charts/ClinvarBreakdown'
import type { ClinvarSignificanceCount } from '@/types/variants'

// Mock react-plotly.js since it requires a browser canvas
vi.mock('react-plotly.js', () => ({
  default: ({
    data,
    layout,
  }: {
    data: Array<{ x?: number[]; y?: string[]; marker?: { color?: string[] } }>
    layout: { title?: { text?: string } }
  }) => (
    <div data-testid="plotly-chart" data-title={layout?.title?.text}>
      <span data-testid="plotly-labels">{data[0]?.y?.join(',')}</span>
      <span data-testid="plotly-values">{data[0]?.x?.join(',')}</span>
      <span data-testid="plotly-colors">{data[0]?.marker?.color?.join(',')}</span>
    </div>
  ),
}))

const MOCK_ITEMS: ClinvarSignificanceCount[] = [
  { significance: 'Benign', count: 200 },
  { significance: 'Likely benign', count: 150 },
  { significance: 'Uncertain significance', count: 80 },
  { significance: 'Likely pathogenic', count: 15 },
  { significance: 'Pathogenic', count: 5 },
]

const MOCK_TOTAL = 450

describe('ClinvarBreakdown', () => {
  it('renders a Plotly bar chart with correct title', () => {
    render(<ClinvarBreakdown items={MOCK_ITEMS} total={MOCK_TOTAL} />)
    const chart = screen.getByTestId('plotly-chart')
    expect(chart).toBeInTheDocument()
    expect(chart.getAttribute('data-title')).toBe('ClinVar Significance (450 total)')
  })

  it('shows empty state message when items array is empty', () => {
    render(<ClinvarBreakdown items={[]} total={0} />)
    expect(screen.getByText('No ClinVar significance data available.')).toBeInTheDocument()
    expect(screen.queryByTestId('plotly-chart')).not.toBeInTheDocument()
  })

  it('renders formatted significance labels', () => {
    const items: ClinvarSignificanceCount[] = [
      { significance: 'Uncertain_significance', count: 10 },
    ]
    render(<ClinvarBreakdown items={items} total={10} />)
    const labels = screen.getByTestId('plotly-labels').textContent
    expect(labels).toContain('Uncertain significance')
  })

  it('passes correct values to Plotly (reversed for top-to-bottom order)', () => {
    render(<ClinvarBreakdown items={MOCK_ITEMS} total={MOCK_TOTAL} />)
    const values = screen.getByTestId('plotly-values').textContent
    // Items are reversed so highest count is at top in horizontal bar
    expect(values).toBe('5,15,80,150,200')
  })

  it('renders with single item', () => {
    const singleItem: ClinvarSignificanceCount[] = [
      { significance: 'Pathogenic', count: 3 },
    ]
    render(<ClinvarBreakdown items={singleItem} total={3} />)
    expect(screen.getByTestId('plotly-chart')).toBeInTheDocument()
  })

  it('colours "Conflicting classifications of pathogenicity" violet, not red (#799)', () => {
    const items: ClinvarSignificanceCount[] = [
      { significance: 'Pathogenic', count: 5 },
      { significance: 'Conflicting classifications of pathogenicity', count: 15 },
    ]
    render(<ClinvarBreakdown items={items} total={20} />)
    const labels = (screen.getByTestId('plotly-labels').textContent ?? '').split(',')
    const colors = (screen.getByTestId('plotly-colors').textContent ?? '').split(',')
    const conflictingIdx = labels.indexOf('Conflicting classifications of pathogenicity')
    expect(conflictingIdx).toBeGreaterThanOrEqual(0)
    expect(colors[conflictingIdx]).toBe('#8B5CF6') // violet
    expect(colors[conflictingIdx]).not.toBe('#DC2626') // not the red pathogenic bar
    // Sanity — a genuine pathogenic bar is still red.
    expect(colors[labels.indexOf('Pathogenic')]).toBe('#DC2626')
  })

  it('renders with many significance categories', () => {
    const manyItems: ClinvarSignificanceCount[] = [
      { significance: 'Benign', count: 300 },
      { significance: 'Likely benign', count: 200 },
      { significance: 'Uncertain significance', count: 100 },
      { significance: 'Likely pathogenic', count: 20 },
      { significance: 'Pathogenic', count: 5 },
      { significance: 'Drug response', count: 10 },
      { significance: 'Risk factor', count: 8 },
      { significance: 'Conflicting interpretations of pathogenicity', count: 15 },
    ]
    const total = manyItems.reduce((sum, item) => sum + item.count, 0)
    render(<ClinvarBreakdown items={manyItems} total={total} />)
    expect(screen.getByTestId('plotly-chart')).toBeInTheDocument()
  })
})
