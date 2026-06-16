/** Donut chart for LAI-derived global ancestry proportions (AMv2 Step 6).
 *
 * Displayed alongside the Tier 1 NNLS bar chart for comparison.
 * Uses react-plotly.js for consistent styling with other charts.
 */

import Plot from "@/components/charts/Plot"
import type { LAIGlobalAncestryEntry } from "@/types/ancestry"
import { useThemeContext } from "@/lib/ThemeContext"
import { getPlotlyTheme } from "@/lib/plotly-theme"
import { POPULATION_LABELS, POPULATION_ORDER } from "@/components/ancestry/constants"

interface AncestryPieChartProps {
  globalAncestry: Record<string, LAIGlobalAncestryEntry>
}

export default function AncestryPieChart({ globalAncestry }: AncestryPieChartProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  // Filter and sort by canonical order, exclude near-zero (<0.1%)
  const entries = POPULATION_ORDER
    .filter((pop) => globalAncestry[pop] && globalAncestry[pop].percentage >= 0.1)
    .map((pop) => ({
      pop,
      entry: globalAncestry[pop],
    }))

  if (entries.length === 0) {
    return (
      <div className="flex items-center justify-center h-[280px] text-muted-foreground text-sm">
        No LAI ancestry data available.
      </div>
    )
  }

  const labels = entries.map((e) => POPULATION_LABELS[e.pop] ?? e.pop)
  const values = entries.map((e) => e.entry.percentage)
  const colors = entries.map((e) => e.entry.color)

  return (
    <div data-testid="ancestry-pie-chart">
      <Plot
        data={[
          {
            labels,
            values,
            type: "pie",
            hole: 0.45,
            marker: { colors },
            textinfo: "percent",
            textposition: "inside",
            hovertemplate: "%{label}<br>%{value:.1f}%<extra></extra>",
            sort: false,
          },
        ]}
        layout={{
          showlegend: true,
          legend: {
            orientation: "v" as const,
            x: 1.05,
            y: 0.5,
            font: { size: 10 },
          },
          margin: { t: 10, b: 10, l: 10, r: 120 },
          paper_bgcolor: pt.paper_bgcolor,
          plot_bgcolor: pt.plot_bgcolor,
          font: pt.font,
          height: 280,
        }}
        config={{ responsive: true, displayModeBar: false }}
        useResizeHandler
        style={{ width: "100%" }}
      />
      <p className="text-xs text-muted-foreground text-center mt-1">
        Based on chromosome painting analysis
      </p>
    </div>
  )
}
