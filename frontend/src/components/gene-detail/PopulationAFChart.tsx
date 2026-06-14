/**
 * Population allele frequency horizontal bar chart (P3-42).
 *
 * Shows per-population gnomAD allele frequencies for variants
 * in the gene. Uses Plotly.js for interactive charting.
 */

import Plot from "@/components/charts/Plot"
import type { PopulationAFSummary } from "@/types/gene-detail"
import { useThemeContext } from "@/lib/ThemeContext"
import { getPlotlyTheme } from "@/lib/plotly-theme"

const POPULATIONS = [
  { key: "gnomad_af_global" as const, label: "Global", color: "#0D9488" },
  { key: "gnomad_af_eur" as const, label: "European", color: "#2563EB" },
  { key: "gnomad_af_afr" as const, label: "African", color: "#7C3AED" },
  { key: "gnomad_af_eas" as const, label: "East Asian", color: "#059669" },
  { key: "gnomad_af_amr" as const, label: "Latino/Admixed", color: "#D97706" },
  { key: "gnomad_af_sas" as const, label: "South Asian", color: "#DC2626" },
  { key: "gnomad_af_fin" as const, label: "Finnish", color: "#4F46E5" },
]

interface PopulationAFChartProps {
  data: PopulationAFSummary[]
  selectedVariant?: string | null
}

export default function PopulationAFChart({ data, selectedVariant }: PopulationAFChartProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  if (data.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
        No population frequency data available for variants in this gene.
      </div>
    )
  }

  // If a variant is selected, show its population breakdown; otherwise show first variant
  const variant = selectedVariant
    ? data.find((d) => d.rsid === selectedVariant) ?? data[0]
    : data[0]

  // Filter out populations with zero/null AF to avoid log(0) on the log scale
  const popEntries = POPULATIONS
    .map((pop) => ({ ...pop, value: (variant[pop.key] ?? 0) as number }))
    .filter((p) => p.value > 0)

  const values = popEntries.map((p) => p.value)
  const labels = popEntries.map((p) => p.label)
  const colors = popEntries.map((p) => p.color)

  return (
    <div data-testid="population-af-chart">
      <div className="text-xs text-muted-foreground mb-2">
        Showing: <span className="font-mono">{variant.rsid}</span>
        {variant.hgvs_protein && (
          <span className="ml-1 text-muted-foreground">({variant.hgvs_protein})</span>
        )}
      </div>
      <Plot
        data={[
          {
            type: "bar",
            orientation: "h",
            x: values,
            y: labels,
            marker: { color: colors },
            text: values.map((v) => (v < 0.0001 ? v.toExponential(2) : v.toFixed(4))),
            textposition: "outside",
            hovertemplate: "%{y}: %{x:.6f}<extra></extra>",
          },
        ]}
        layout={{
          height: 240,
          margin: { l: 110, r: 60, t: 10, b: 30 },
          xaxis: {
            title: { text: "Allele Frequency", font: { size: 11 } },
            type: "log",
            autorange: true,
          },
          yaxis: { autorange: "reversed" },
          paper_bgcolor: pt.paper_bgcolor,
          plot_bgcolor: pt.plot_bgcolor,
          font: { ...pt.font, size: 11 },
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
      />

      {/* Variant count when multiple variants available */}
      {data.length > 1 && (
        <div className="mt-2 text-xs text-muted-foreground">
          {data.length} variant{data.length !== 1 ? "s" : ""} with population frequency data
        </div>
      )}
    </div>
  )
}
