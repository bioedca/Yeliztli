/** Admixture bar chart — ancestry population fractions (P3-27, AMv2 Step 7).
 *
 * Displays a horizontal stacked bar chart showing the estimated
 * proportion of each reference population in the user's ancestry.
 * Supports optional bootstrap 95% CI error bars and ±X% labels.
 * Uses react-plotly.js for interactive hover and responsive sizing.
 */

import Plot from "@/components/charts/Plot"
import { POPULATION_COLORS, POPULATION_LABELS } from "./constants"
import { useThemeContext } from "@/lib/ThemeContext"
import { getPlotlyTheme } from "@/lib/plotly-theme"

interface AdmixtureBarProps {
  admixture_fractions: Record<string, number>
  ci_low?: Record<string, number>
  ci_high?: Record<string, number>
}

export default function AdmixtureBar({ admixture_fractions, ci_low, ci_high }: AdmixtureBarProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)
  // Sort populations by fraction descending
  const sorted = Object.entries(admixture_fractions)
    .filter(([, frac]) => frac > 0.001)
    .sort((a, b) => b[1] - a[1])

  if (sorted.length === 0) {
    return (
      <div className="flex items-center justify-center h-[120px] text-muted-foreground text-sm">
        No admixture data available.
      </div>
    )
  }

  const hasCi = ci_low && ci_high

  const traces = sorted.map(([pop, frac]) => {
    const halfWidth = hasCi
      ? ((ci_high[pop] ?? frac) - (ci_low[pop] ?? frac)) / 2 * 100
      : null

    return {
      x: [frac * 100],
      y: ["Ancestry"],
      name: POPULATION_LABELS[pop] ?? pop,
      type: "bar" as const,
      orientation: "h" as const,
      marker: {
        color: POPULATION_COLORS[pop] ?? "#94A3B8",
      },
      text: [halfWidth != null && halfWidth > 0.05
        ? `${(frac * 100).toFixed(1)}% \u00B1${halfWidth.toFixed(1)}%`
        : `${(frac * 100).toFixed(1)}%`],
      textposition: "inside" as const,
      hovertemplate: hasCi
        ? `${POPULATION_LABELS[pop] ?? pop}: %{x:.1f}% (${((ci_low[pop] ?? frac) * 100).toFixed(1)}–${((ci_high[pop] ?? frac) * 100).toFixed(1)}%)<extra></extra>`
        : `${POPULATION_LABELS[pop] ?? pop}: %{x:.1f}%<extra></extra>`,
    }
  })

  return (
    <div data-testid="admixture-bar">
      <Plot
        data={traces}
        layout={{
          barmode: "stack",
          showlegend: true,
          legend: {
            orientation: "h" as const,
            x: 0,
            y: -0.3,
            font: { size: 11 },
          },
          xaxis: {
            title: { text: "Percentage (%)", font: { size: 11 } },
            range: [0, 100],
            fixedrange: true,
          },
          yaxis: {
            visible: false,
            fixedrange: true,
          },
          margin: { t: 10, b: 60, l: 10, r: 20 },
          paper_bgcolor: pt.paper_bgcolor,
          plot_bgcolor: pt.plot_bgcolor,
          font: pt.font,
          height: 120,
        }}
        config={{ responsive: true, displayModeBar: false }}
        useResizeHandler
        style={{ width: "100%" }}
      />
    </div>
  )
}
