/** Consequence type donut chart — variant counts per VEP consequence type (P2-25).
 *
 * Displays a donut (pie with hole) chart where each slice represents a distinct
 * VEP SO consequence term. Slices are colored by impact tier:
 *   HIGH (red), MODERATE (amber), LOW (teal), MODIFIER (slate).
 * Uses react-plotly.js for interactive hover, responsive sizing, and dark mode support.
 */

import Plot from '@/components/charts/Plot'
import type { ConsequenceCount } from '@/types/variants'
import { useThemeContext } from '@/lib/ThemeContext'
import { getPlotlyTheme } from '@/lib/plotly-theme'

/** Impact tier → color mapping (consistent with density histogram). */
const TIER_COLORS: Record<string, string> = {
  HIGH: '#DC2626',     // red-600
  MODERATE: '#F59E0B', // amber-500
  LOW: '#0D9488',      // teal-600
  MODIFIER: '#94A3B8', // slate-400
}

/** Format SO term for display: replace underscores with spaces, title-case. */
function formatConsequence(term: string): string {
  return term
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

interface ConsequenceDonutProps {
  items: ConsequenceCount[]
  total: number
}

export default function ConsequenceDonut({ items, total }: ConsequenceDonutProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  if (items.length === 0) {
    return (
      <div className="flex items-center justify-center h-[300px] text-muted-foreground text-sm">
        No consequence data available.
      </div>
    )
  }

  const labels = items.map((i) => formatConsequence(i.consequence))
  const values = items.map((i) => i.count)
  const colors = items.map((i) => TIER_COLORS[i.tier] ?? TIER_COLORS.MODIFIER)

  return (
    <Plot
      data={[
        {
          labels,
          values,
          type: 'pie',
          hole: 0.45,
          marker: { colors },
          textinfo: 'percent',
          textposition: 'inside',
          hovertemplate: '%{label}<br>%{value:,} variants (%{percent})<extra></extra>',
          sort: false,
        },
      ]}
      layout={{
        title: { text: 'Consequence Types', font: { size: 14 } },
        annotations: [
          {
            text: `${total.toLocaleString()}`,
            showarrow: false,
            font: { size: 16, color: pt.annotationColor },
          },
        ],
        showlegend: true,
        legend: {
          orientation: 'v' as const,
          x: 1.05,
          y: 0.5,
          font: { size: 10 },
        },
        margin: { t: 40, b: 20, l: 20, r: 120 },
        paper_bgcolor: pt.paper_bgcolor,
        plot_bgcolor: pt.plot_bgcolor,
        font: pt.font,
        height: 300,
      }}
      config={{ responsive: true, displayModeBar: false }}
      useResizeHandler
      style={{ width: '100%' }}
    />
  )
}
