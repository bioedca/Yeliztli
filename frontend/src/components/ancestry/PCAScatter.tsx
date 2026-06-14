/** PCA scatter plot — user projected onto reference panel PCA space (P3-27, AMv2 Step 5).
 *
 * Displays an interactive scatter plot with:
 * - Reference panel samples colored by population
 * - User sample as a prominent marker (star)
 * - Population centroids as diamond markers
 * - PC selection dropdown (PC1 vs PC2, PC1 vs PC3, etc.)
 * Uses react-plotly.js for interactivity.
 */

import { useState } from "react"
import Plot from "@/components/charts/Plot"
import type { PCACoordinatesResponse } from "@/types/ancestry"
import { POPULATION_COLORS, POPULATION_LABELS } from "./constants"
import { useThemeContext } from "@/lib/ThemeContext"
import { getPlotlyTheme } from "@/lib/plotly-theme"

interface PCAScatterProps {
  pcaData: PCACoordinatesResponse
}

export default function PCAScatter({ pcaData }: PCAScatterProps) {
  const { isDark } = useThemeContext()
  const pt = getPlotlyTheme(isDark)

  // PC axis selection (0-indexed)
  const nPCs = pcaData.n_components
  const [pcX, setPcX] = useState(0)
  const [pcY, setPcY] = useState(1)

  const pcOptions = Array.from({ length: nPCs }, (_, i) => i)

  const traces: Plotly.Data[] = []

  // Reference population samples
  for (const [pop, coords] of Object.entries(pcaData.reference_samples)) {
    if (coords.length === 0) continue
    const label = POPULATION_LABELS[pop] ?? pop
    const color = POPULATION_COLORS[pop] ?? "#94A3B8"

    traces.push({
      x: coords.map((c) => c[pcX]),
      y: coords.map((c) => c[pcY]),
      mode: "markers",
      type: "scatter",
      name: label,
      marker: {
        color,
        size: 5,
        opacity: 0.5,
      },
      hovertemplate: `${label}<br>${pcaData.pc_labels[pcX] ?? `PC${pcX + 1}`}: %{x:.4f}<br>${pcaData.pc_labels[pcY] ?? `PC${pcY + 1}`}: %{y:.4f}<extra></extra>`,
    })
  }

  // Population centroids
  const centroidPops = Object.keys(pcaData.centroids).filter(
    (p) => pcaData.centroids[p]?.length > Math.max(pcX, pcY)
  )
  if (centroidPops.length > 0) {
    traces.push({
      x: centroidPops.map((p) => pcaData.centroids[p][pcX]),
      y: centroidPops.map((p) => pcaData.centroids[p][pcY]),
      mode: "text+markers",
      type: "scatter",
      name: "Centroids",
      text: centroidPops.map((p) => POPULATION_LABELS[p] ?? p),
      textposition: "top center",
      textfont: { size: 9, color: pt.annotationColor },
      marker: {
        symbol: "diamond",
        size: 10,
        color: centroidPops.map((p) => POPULATION_COLORS[p] ?? "#94A3B8"),
        line: { width: 1, color: isDark ? "#94A3B8" : "#1E293B" },
      },
      hovertemplate: "%{text}<br>" + `${pcaData.pc_labels[pcX] ?? `PC${pcX + 1}`}: %{x:.4f}<br>${pcaData.pc_labels[pcY] ?? `PC${pcY + 1}`}: %{y:.4f}<extra>Centroid</extra>`,
      showlegend: false,
    })
  }

  // User sample (prominent star marker)
  if (pcaData.user.length > Math.max(pcX, pcY)) {
    const userLabel = POPULATION_LABELS[pcaData.top_population] ?? pcaData.top_population
    traces.push({
      x: [pcaData.user[pcX]],
      y: [pcaData.user[pcY]],
      mode: "markers",
      type: "scatter",
      name: "You",
      marker: {
        symbol: "star",
        size: 16,
        color: "#0D9488",
        line: { width: 2, color: isDark ? "#1E293B" : "#FFFFFF" },
      },
      hovertemplate: `Your sample<br>${pcaData.pc_labels[pcX] ?? `PC${pcX + 1}`}: %{x:.4f}<br>${pcaData.pc_labels[pcY] ?? `PC${pcY + 1}`}: %{y:.4f}<br>Top: ${userLabel}<extra></extra>`,
    })
  }

  return (
    <div data-testid="pca-scatter">
      {/* PC axis selectors */}
      {nPCs > 2 && (
        <div className="flex items-center gap-4 mb-3" data-testid="pc-selectors">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            X axis:
            <select
              value={pcX}
              onChange={(e) => setPcX(Number(e.target.value))}
              className="rounded border bg-background px-2 py-1 text-xs text-foreground"
              data-testid="pc-x-select"
            >
              {pcOptions.map((i) => (
                <option key={i} value={i}>
                  {pcaData.pc_labels[i] ?? `PC${i + 1}`}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Y axis:
            <select
              value={pcY}
              onChange={(e) => setPcY(Number(e.target.value))}
              className="rounded border bg-background px-2 py-1 text-xs text-foreground"
              data-testid="pc-y-select"
            >
              {pcOptions.map((i) => (
                <option key={i} value={i}>
                  {pcaData.pc_labels[i] ?? `PC${i + 1}`}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <Plot
        data={traces}
        layout={{
          xaxis: {
            title: { text: pcaData.pc_labels[pcX] ?? `PC${pcX + 1}`, font: { size: 12 } },
            zeroline: false,
          },
          yaxis: {
            title: { text: pcaData.pc_labels[pcY] ?? `PC${pcY + 1}`, font: { size: 12 } },
            zeroline: false,
          },
          showlegend: true,
          legend: {
            orientation: "v" as const,
            x: 1.02,
            y: 1,
            font: { size: 10 },
          },
          margin: { t: 20, b: 50, l: 60, r: 140 },
          paper_bgcolor: pt.paper_bgcolor,
          plot_bgcolor: pt.plot_bgcolor,
          font: pt.font,
          height: 450,
          hovermode: "closest",
        }}
        config={{ responsive: true, displayModeBar: false }}
        useResizeHandler
        style={{ width: "100%" }}
      />
    </div>
  )
}
