/** Per-chromosome heterozygosity rate histogram (P1-21).
 *
 * Shows the distribution of heterozygosity rates across chromosomes.
 * Each bar represents one chromosome's het/(het+hom) ratio.
 * Uses react-plotly.js for interactive hover tooltips and responsive sizing.
 */

import Plot from 'react-plotly.js'
import type { ChromosomeQCStats } from '@/types/variants'
import { useThemeContext } from '@/lib/ThemeContext'
import { getPlotlyTheme } from '@/lib/plotly-theme'

interface HeterozygosityHistogramProps {
  data: ChromosomeQCStats[]
  overallRate: number
}

export default function HeterozygosityHistogram({
  data,
  overallRate,
}: HeterozygosityHistogramProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  // Compute per-chromosome heterozygosity rates (exclude chromosomes with 0 called)
  const filtered = data.filter((d) => d.het_count + d.hom_count > 0)

  if (filtered.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No called variants to display heterozygosity chart.
      </p>
    )
  }

  const chroms = filtered.map((d) => `chr${d.chrom}`)
  const rates = filtered.map(
    (d) => d.het_count / (d.het_count + d.hom_count),
  )

  return (
    <Plot
      data={[
        {
          x: chroms,
          y: rates,
          type: 'bar',
          name: 'Het rate',
          marker: {
            color: rates.map((r) =>
              r > overallRate ? '#0D9488' : '#5EEAD4',
            ),
          },
          hovertemplate:
            '%{x}: %{y:.3f}<extra></extra>',
        },
      ]}
      layout={{
        title: { text: 'Heterozygosity Rate by Chromosome', font: { size: 14 } },
        xaxis: {
          title: { text: 'Chromosome' },
          tickangle: -45,
          tickfont: { size: 10 },
        },
        yaxis: {
          title: { text: 'Het Rate' },
          range: [0, Math.max(...rates, 0.5) * 1.1],
          gridwidth: 1,
          gridcolor: pt.gridColor,
        },
        shapes: [
          {
            type: 'line',
            x0: -0.5,
            x1: chroms.length - 0.5,
            y0: overallRate,
            y1: overallRate,
            line: { color: '#EF4444', width: 2, dash: 'dash' },
          },
        ],
        annotations: [
          {
            x: chroms.length - 1,
            y: overallRate,
            // The reference line is the pooled AUTOSOMAL heterozygosity rate (the
            // QC baseline used for outlier detection), not the arithmetic mean of
            // the per-chromosome bars (#513).
            text: `Autosomal: ${overallRate.toFixed(3)}`,
            showarrow: false,
            yshift: 12,
            font: { color: '#EF4444', size: 11 },
          },
        ],
        margin: { t: 40, b: 60, l: 60, r: 20 },
        paper_bgcolor: pt.paper_bgcolor,
        plot_bgcolor: pt.plot_bgcolor,
        font: pt.font,
        height: 300,
        showlegend: false,
      }}
      config={{ responsive: true, displayModeBar: false }}
      useResizeHandler
      style={{ width: '100%' }}
    />
  )
}
