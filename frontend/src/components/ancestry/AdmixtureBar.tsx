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

// Narrow stacked segments can't hold an in-bar label: Plotly rotates/clips it to
// illegible vertical text (and an outside label on a 1–4% sliver overlaps its
// neighbours). Admixed samples routinely have several small components, so this
// is the common case. Suppress the in-bar label below MIN_LABEL_PCT and append
// the wider "±CI%" suffix only at/above MIN_CI_LABEL_PCT; the hover tooltip and
// legend still convey every population, so no information is lost.
const MIN_LABEL_PCT = 5
const MIN_CI_LABEL_PCT = 10

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
    const pct = frac * 100
    const halfWidth = hasCi
      ? ((ci_high[pop] ?? frac) - (ci_low[pop] ?? frac)) / 2 * 100
      : null

    // Only label segments wide enough to render legibly; attach the \u00B1CI suffix
    // only on the widest ones. Narrow segments get no in-bar text (hover/legend
    // cover them) so Plotly never has to rotate/clip a label into a sliver.
    let label = ""
    if (pct >= MIN_CI_LABEL_PCT && halfWidth != null && halfWidth > 0.05) {
      label = `${pct.toFixed(1)}% \u00B1${halfWidth.toFixed(1)}%`
    } else if (pct >= MIN_LABEL_PCT) {
      label = `${pct.toFixed(1)}%`
    }

    return {
      x: [pct],
      y: ["Ancestry"],
      name: POPULATION_LABELS[pop] ?? pop,
      type: "bar" as const,
      orientation: "h" as const,
      marker: {
        color: POPULATION_COLORS[pop] ?? "#94A3B8",
      },
      text: [label],
      // "auto" lets Plotly place a shown label inside when it fits (outside
      // otherwise) instead of forcing it inside and rotating/clipping it;
      // cliponaxis keeps an outside label from being cut off at the axis edge.
      textposition: "auto" as const,
      cliponaxis: false,
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
