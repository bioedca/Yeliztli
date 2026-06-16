/** ClinVar significance breakdown bar chart (P2-26).
 *
 * Displays a horizontal bar chart where each bar represents a distinct
 * ClinVar clinical significance category. Bars are colored by clinical
 * impact: pathogenic/likely pathogenic (red), VUS (amber), benign/likely
 * benign (teal), other (slate).
 * Uses react-plotly.js for interactive hover, responsive sizing, and dark mode support.
 */

import Plot from '@/components/charts/Plot'
import type { ClinvarSignificanceCount } from '@/types/variants'
import { useThemeContext } from '@/lib/ThemeContext'
import { getPlotlyTheme } from '@/lib/plotly-theme'

/** ClinVar significance → color mapping by clinical impact. */
const SIGNIFICANCE_COLORS: Record<string, string> = {
  Pathogenic: '#DC2626',                                     // red-600
  'Likely pathogenic': '#EF4444',                            // red-500
  'Pathogenic/Likely pathogenic': '#DC2626',                 // red-600
  'Uncertain significance': '#F59E0B',                       // amber-500
  'Likely benign': '#14B8A6',                                // teal-500
  Benign: '#0D9488',                                         // teal-600
  'Benign/Likely benign': '#0D9488',                         // teal-600
  'Conflicting classifications of pathogenicity': '#8B5CF6', // violet-500 (current ClinVar wording, #799)
  'Conflicting interpretations of pathogenicity': '#8B5CF6', // violet-500 (legacy ClinVar wording)
  'Drug response': '#3B82F6',                                // blue-500
  'Risk factor': '#F97316',                                  // orange-500
  other: '#94A3B8',                                          // slate-400
}

function getBarColor(significance: string): string {
  // Check exact match first, then normalize.
  if (SIGNIFICANCE_COLORS[significance]) return SIGNIFICANCE_COLORS[significance]
  const lower = significance.toLowerCase()
  // "Conflicting classifications of pathogenicity" contains "pathogenic" but is an
  // aggregate of disagreeing submissions, not a pathogenic call — keep it violet,
  // never red (#799), and ahead of the pathogenic substring test.
  if (lower.includes('conflicting'))
    return SIGNIFICANCE_COLORS['Conflicting classifications of pathogenicity']
  if (lower.includes('pathogenic') && !lower.includes('benign'))
    return SIGNIFICANCE_COLORS.Pathogenic
  if (lower.includes('benign')) return SIGNIFICANCE_COLORS.Benign
  if (lower.includes('uncertain')) return SIGNIFICANCE_COLORS['Uncertain significance']
  return SIGNIFICANCE_COLORS.other
}

/** Format significance for display: title-case, replace underscores. */
function formatSignificance(term: string): string {
  return term.replace(/_/g, ' ')
}

interface ClinvarBreakdownProps {
  items: ClinvarSignificanceCount[]
  total: number
}

export default function ClinvarBreakdown({ items, total }: ClinvarBreakdownProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  if (items.length === 0) {
    return (
      <div className="flex items-center justify-center h-[300px] text-muted-foreground text-sm">
        No ClinVar significance data available.
      </div>
    )
  }

  // Reverse for horizontal bar chart so highest count appears at top
  const sorted = [...items].reverse()
  const labels = sorted.map((i) => formatSignificance(i.significance))
  const values = sorted.map((i) => i.count)
  const colors = sorted.map((i) => getBarColor(i.significance))

  return (
    <Plot
      data={[
        {
          x: values,
          y: labels,
          type: 'bar',
          orientation: 'h',
          marker: { color: colors },
          hovertemplate: '%{y}<br>%{x:,} variants<extra></extra>',
          textposition: 'auto' as const,
        },
      ]}
      layout={{
        title: { text: `ClinVar Significance (${total.toLocaleString()} total)`, font: { size: 14 } },
        margin: { t: 40, b: 40, l: 200, r: 20 },
        paper_bgcolor: pt.paper_bgcolor,
        plot_bgcolor: pt.plot_bgcolor,
        font: pt.font,
        height: 300,
        xaxis: {
          title: { text: 'Variant count' },
          gridcolor: pt.gridColor,
        },
        yaxis: {
          automargin: true,
        },
        bargap: 0.3,
      }}
      config={{ responsive: true, displayModeBar: false }}
      useResizeHandler
      style={{ width: '100%' }}
    />
  )
}
