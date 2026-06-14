/** Variant density histogram — variants per 1 Mb bin, colored by consequence tier (P2-23).
 *
 * Displays a stacked bar chart where each bar represents a 1 Mb genomic bin.
 * Bars are colored by VEP consequence impact tier: HIGH, MODERATE, LOW, MODIFIER.
 * Uses react-plotly.js for interactive hover, responsive sizing, and dark mode support.
 */

import Plot from '@/components/charts/Plot'
import type { DensityBin } from '@/types/variants'
import { useThemeContext } from '@/lib/ThemeContext'
import { getPlotlyTheme } from '@/lib/plotly-theme'

/** Consequence tier colors — medical teal palette. */
const TIER_COLORS = {
  HIGH: '#DC2626',     // red-600 — serious impact
  MODERATE: '#F59E0B', // amber-500 — moderate impact
  LOW: '#0D9488',      // teal-600 — low impact
  MODIFIER: '#94A3B8', // slate-400 — regulatory/intergenic
} as const

interface VariantDensityHistogramProps {
  bins: DensityBin[]
}

export default function VariantDensityHistogram({ bins }: VariantDensityHistogramProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  if (bins.length === 0) {
    return (
      <div className="flex items-center justify-center h-[300px] text-muted-foreground text-sm">
        No variant density data available.
      </div>
    )
  }

  // Build x-axis labels: "chr1:0-1M", "chr1:1-2M", etc.
  const labels = bins.map((b) => {
    const startMb = b.bin_start / 1_000_000
    const endMb = b.bin_end / 1_000_000
    return `chr${b.chrom}:${startMb}-${endMb}M`
  })

  // Chromosome boundary markers for x-axis gridlines
  const chromBoundaries: number[] = []
  for (let i = 1; i < bins.length; i++) {
    if (bins[i].chrom !== bins[i - 1].chrom) {
      chromBoundaries.push(i - 0.5)
    }
  }

  // Chromosome center positions for tick labels
  const chromTicks: { pos: number; label: string }[] = []
  let chromStart = 0
  for (let i = 0; i <= bins.length; i++) {
    if (i === bins.length || (i > 0 && bins[i].chrom !== bins[i - 1].chrom)) {
      chromTicks.push({
        pos: (chromStart + i - 1) / 2,
        label: `chr${bins[chromStart].chrom}`,
      })
      chromStart = i
    }
  }

  return (
    <Plot
      data={[
        {
          x: labels,
          y: bins.map((b) => b.modifier),
          type: 'bar',
          name: 'Modifier',
          marker: { color: TIER_COLORS.MODIFIER },
          hovertemplate: '%{x}<br>Modifier: %{y:,}<extra></extra>',
        },
        {
          x: labels,
          y: bins.map((b) => b.low),
          type: 'bar',
          name: 'Low',
          marker: { color: TIER_COLORS.LOW },
          hovertemplate: '%{x}<br>Low: %{y:,}<extra></extra>',
        },
        {
          x: labels,
          y: bins.map((b) => b.moderate),
          type: 'bar',
          name: 'Moderate',
          marker: { color: TIER_COLORS.MODERATE },
          hovertemplate: '%{x}<br>Moderate: %{y:,}<extra></extra>',
        },
        {
          x: labels,
          y: bins.map((b) => b.high),
          type: 'bar',
          name: 'High',
          marker: { color: TIER_COLORS.HIGH },
          hovertemplate: '%{x}<br>High: %{y:,}<extra></extra>',
        },
      ]}
      layout={{
        barmode: 'stack',
        title: { text: 'Variant Density (per 1 Mb)', font: { size: 14 } },
        xaxis: {
          title: { text: 'Genomic Position' },
          tickvals: chromTicks.map((t) => t.pos),
          ticktext: chromTicks.map((t) => t.label),
          tickfont: { size: 10 },
          showgrid: false,
        },
        yaxis: {
          title: { text: 'Variant Count' },
          gridwidth: 1,
          gridcolor: pt.gridColor,
        },
        margin: { t: 40, b: 60, l: 60, r: 20 },
        legend: { orientation: 'h', y: -0.25 },
        paper_bgcolor: pt.paper_bgcolor,
        plot_bgcolor: pt.plot_bgcolor,
        font: pt.font,
        height: 300,
        shapes: chromBoundaries.map((x) => ({
          type: 'line' as const,
          x0: x,
          x1: x,
          y0: 0,
          y1: 1,
          yref: 'paper' as const,
          line: { color: isDark ? '#475569' : '#CBD5E1', width: 1, dash: 'dot' as const },
        })),
      }}
      config={{ responsive: true, displayModeBar: false }}
      useResizeHandler
      style={{ width: '100%' }}
    />
  )
}
