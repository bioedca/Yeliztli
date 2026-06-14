/** Per-chromosome ideogram with variant density overlay (P2-24).
 *
 * Displays a horizontal ideogram for each chromosome (proportional to GRCh37 size)
 * with a heatmap overlay showing variant density per 1 Mb bin.
 * Color intensity represents total variant count. Hover shows per-tier breakdown.
 * Uses react-plotly.js for interactivity and responsive sizing.
 */

import Plot from '@/components/charts/Plot'
import type { DensityBin } from '@/types/variants'
import { useThemeContext } from '@/lib/ThemeContext'
import { getPlotlyTheme } from '@/lib/plotly-theme'

/** GRCh37 chromosome sizes in base pairs. */
const CHROM_SIZES_GRCH37: Record<string, number> = {
  '1': 249_250_621,
  '2': 243_199_373,
  '3': 198_022_430,
  '4': 191_154_276,
  '5': 180_915_260,
  '6': 171_115_067,
  '7': 159_138_663,
  '8': 146_364_022,
  '9': 141_213_431,
  '10': 135_534_747,
  '11': 135_006_516,
  '12': 133_851_895,
  '13': 115_169_878,
  '14': 107_349_540,
  '15': 102_531_392,
  '16': 90_354_753,
  '17': 81_195_210,
  '18': 78_077_248,
  '19': 59_128_983,
  '20': 63_025_520,
  '21': 48_129_895,
  '22': 51_304_566,
  'X': 155_270_560,
  'Y': 59_373_566,
  'MT': 16_569,
}

const BIN_SIZE = 1_000_000

/** Chromosomes in display order (top-to-bottom: chr1 at top). */
const CHROM_DISPLAY_ORDER = [
  '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
  '11', '12', '13', '14', '15', '16', '17', '18', '19', '20',
  '21', '22', 'X', 'Y', 'MT',
] as const

/** Maximum number of 1 Mb bins (chr1 is largest). */
const MAX_BINS = Math.ceil(
  Math.max(...Object.values(CHROM_SIZES_GRCH37)) / BIN_SIZE,
)

interface ChromosomeIdeogramProps {
  bins: DensityBin[]
}

export default function ChromosomeIdeogram({ bins }: ChromosomeIdeogramProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  if (bins.length === 0) {
    return (
      <div className="flex items-center justify-center h-[500px] text-muted-foreground text-sm">
        No variant density data available for ideogram.
      </div>
    )
  }

  // Index bins by chrom → bin_start for O(1) lookups.
  const binMap = new Map<string, Map<number, DensityBin>>()
  for (const bin of bins) {
    if (!binMap.has(bin.chrom)) binMap.set(bin.chrom, new Map())
    binMap.get(bin.chrom)!.set(bin.bin_start, bin)
  }

  // Build heatmap z-matrix and custom hover text.
  // Rows = chromosomes (reversed for top-to-bottom display), columns = Mb bins.
  const yLabels = [...CHROM_DISPLAY_ORDER].reverse().map((c) => `chr${c}`)
  const reversedChroms = [...CHROM_DISPLAY_ORDER].reverse()
  const z: (number | null)[][] = []
  const hoverText: string[][] = []

  for (const chrom of reversedChroms) {
    const chromSize = CHROM_SIZES_GRCH37[chrom]
    const chromBins = chromSize ? Math.ceil(chromSize / BIN_SIZE) : 0
    const row: (number | null)[] = []
    const hoverRow: string[] = []
    const chromBinMap = binMap.get(chrom)

    for (let binIdx = 0; binIdx < MAX_BINS; binIdx++) {
      if (binIdx >= chromBins) {
        // Beyond chromosome size — mark as null (transparent).
        row.push(null)
        hoverRow.push('')
      } else {
        const binStart = binIdx * BIN_SIZE
        const bin = chromBinMap?.get(binStart)
        if (bin && bin.total > 0) {
          row.push(bin.total)
          const startMb = binStart / BIN_SIZE
          const endMb = (binStart + BIN_SIZE) / BIN_SIZE
          hoverRow.push(
            `chr${chrom}: ${startMb}–${endMb} Mb<br>` +
            `Total: ${bin.total}<br>` +
            `High: ${bin.high} · Moderate: ${bin.moderate}<br>` +
            `Low: ${bin.low} · Modifier: ${bin.modifier}`,
          )
        } else {
          row.push(0)
          const startMb = binIdx
          hoverRow.push(`chr${chrom}: ${startMb}–${startMb + 1} Mb<br>No variants`)
        }
      }
    }
    z.push(row)
    hoverText.push(hoverRow)
  }

  // X-axis values in Mb.
  const xValues = Array.from({ length: MAX_BINS }, (_, i) => i)

  // Chromosome outline shapes (light border to show full extent).
  const shapes: Partial<Plotly.Shape>[] = reversedChroms.map((chrom, rowIdx) => {
    const chromSize = CHROM_SIZES_GRCH37[chrom] ?? 0
    const chromBins = Math.ceil(chromSize / BIN_SIZE)
    return {
      type: 'rect' as const,
      xref: 'x' as const,
      yref: 'y' as const,
      x0: -0.5,
      x1: chromBins - 0.5,
      y0: rowIdx - 0.5,
      y1: rowIdx + 0.5,
      line: { color: isDark ? '#475569' : '#CBD5E1', width: 1 },
      fillcolor: 'rgba(0,0,0,0)',
      layer: 'above' as const,
    }
  })

  // Teal-based colorscale matching medical theme.
  const colorscale: [number, string][] = [
    [0, '#F0FDFA'],      // teal-50 (near zero)
    [0.25, '#CCFBF1'],   // teal-100
    [0.5, '#5EEAD4'],    // teal-300
    [0.75, '#14B8A6'],   // teal-500
    [1, '#0F766E'],      // teal-700 (max density)
  ]

  return (
    <Plot
      data={[
        {
          z,
          x: xValues,
          y: yLabels,
          type: 'heatmap',
          colorscale,
          showscale: true,
          colorbar: {
            title: { text: 'Variants', side: 'right' as const },
            thickness: 12,
            len: 0.5,
            tickfont: { size: 10 },
          },
          // Plotly.js heatmap supports 2D text arrays, but @types/plotly.js types are too narrow.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          text: hoverText as any,
          hovertemplate: '%{text}<extra></extra>',
          hoverinfo: 'text',
          xgap: 0,
          ygap: 1,
          zmin: 0,
          connectgaps: false,
        },
      ]}
      layout={{
        title: { text: 'Chromosome Ideogram — Variant Density', font: { size: 14 } },
        xaxis: {
          title: { text: 'Position (Mb)' },
          tickfont: { size: 10 },
          showgrid: false,
          zeroline: false,
        },
        yaxis: {
          tickfont: { size: 10 },
          showgrid: false,
          zeroline: false,
          autorange: true,
          dtick: 1,
        },
        margin: { t: 40, b: 50, l: 55, r: 80 },
        paper_bgcolor: pt.paper_bgcolor,
        plot_bgcolor: pt.plot_bgcolor,
        font: pt.font,
        height: 600,
        shapes: shapes as Plotly.Shape[],
      }}
      config={{ responsive: true, displayModeBar: false }}
      useResizeHandler
      style={{ width: '100%' }}
    />
  )
}
