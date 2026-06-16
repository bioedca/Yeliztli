/** Big Five personality radar chart for Traits & Personality module (P3-64).
 *
 * Visual-only radar chart showing Big Five personality trait associations.
 * No numeric claims — the chart shows relative associations only.
 * Each axis represents a Big Five dimension: Openness, Conscientiousness,
 * Extraversion, Agreeableness, Neuroticism.
 */

import { cn } from "@/lib/utils"
import type { SNPDetail } from "@/types/traits"

interface BigFiveRadarChartProps {
  /** SNP details from the personality_big_five pathway. */
  snpDetails: SNPDetail[]
  className?: string
}

/** Big Five dimension labels and their mapping from trait_domain. */
const BIG_FIVE_DIMENSIONS = [
  { key: "openness", label: "Openness" },
  { key: "conscientiousness", label: "Conscientiousness" },
  { key: "extraversion", label: "Extraversion" },
  { key: "agreeableness", label: "Agreeableness" },
  { key: "neuroticism", label: "Neuroticism" },
] as const

const SVG_SIZE = 300
const LABEL_EDGE_PADDING = 8

/** Map category levels to numeric values for radar display. */
function categoryToValue(category: string): number {
  switch (category) {
    case "Elevated":
      return 0.8
    case "Moderate":
      return 0.5
    case "Standard":
      return 0.3
    default:
      return 0.3
  }
}

/** Compute the Big Five dimension values from SNP details. */
function computeDimensionValues(snpDetails: SNPDetail[]): number[] {
  return BIG_FIVE_DIMENSIONS.map(({ key }) => {
    const domainSnps = snpDetails.filter(
      (s) => s.trait_domain?.toLowerCase() === key,
    )
    if (domainSnps.length === 0) return 0.3

    const avg =
      domainSnps.reduce((sum, s) => sum + categoryToValue(s.category), 0) /
      domainSnps.length
    return avg
  })
}

export default function BigFiveRadarChart({
  snpDetails,
  className,
}: BigFiveRadarChartProps) {
  const values = computeDimensionValues(snpDetails)
  const n = BIG_FIVE_DIMENSIONS.length

  // SVG dimensions
  const cx = SVG_SIZE / 2
  const cy = SVG_SIZE / 2
  const maxR = 100

  // Helper: polar to cartesian (top = 0°)
  const toXY = (i: number, r: number) => {
    const angle = (2 * Math.PI * i) / n - Math.PI / 2
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    }
  }

  // Background rings (3 concentric)
  const rings = [0.33, 0.66, 1.0]

  // Build polygon points for the data
  const dataPoints = values.map((v, i) => toXY(i, v * maxR))
  const dataPath = dataPoints.map((p) => `${p.x},${p.y}`).join(" ")

  return (
    <div className={cn("w-full max-w-[320px] mx-auto", className)}>
      <svg
        viewBox={`0 0 ${SVG_SIZE} ${SVG_SIZE}`}
        className="w-full"
        role="img"
        aria-label="Big Five personality trait associations radar chart. Visual representation only — no numeric claims."
      >
        {/* Background rings */}
        {rings.map((scale) => {
          const ringPoints = Array.from({ length: n }, (_, i) =>
            toXY(i, scale * maxR),
          )
          const ringPath = ringPoints.map((p) => `${p.x},${p.y}`).join(" ")
          return (
            <polygon
              key={scale}
              points={ringPath}
              fill="none"
              stroke="currentColor"
              strokeWidth="1"
              className="text-border"
            />
          )
        })}

        {/* Axis lines */}
        {Array.from({ length: n }, (_, i) => {
          const p = toXY(i, maxR)
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={p.x}
              y2={p.y}
              stroke="currentColor"
              strokeWidth="1"
              className="text-border"
            />
          )
        })}

        {/* Data polygon */}
        <polygon
          points={dataPath}
          fill="currentColor"
          fillOpacity={0.15}
          stroke="currentColor"
          strokeWidth="2"
          className="text-primary"
        />

        {/* Data points */}
        {dataPoints.map((p, i) => (
          <circle
            key={i}
            cx={p.x}
            cy={p.y}
            r="4"
            fill="currentColor"
            className="text-primary"
          />
        ))}

        {/* Dimension labels */}
        {BIG_FIVE_DIMENSIONS.map((dim, i) => {
          const labelR = maxR + 24
          const p = toXY(i, labelR)
          const isRightEdgeLabel = p.x > cx + maxR * 0.75
          const isLeftEdgeLabel = p.x < cx - maxR * 0.5
          let x = p.x
          let textAnchor: "start" | "middle" | "end" = "middle"
          if (isRightEdgeLabel) {
            x = SVG_SIZE - LABEL_EDGE_PADDING
            textAnchor = "end"
          } else if (isLeftEdgeLabel) {
            x = LABEL_EDGE_PADDING
            textAnchor = "start"
          }
          return (
            <text
              key={dim.key}
              x={x}
              y={p.y}
              textAnchor={textAnchor}
              dominantBaseline="central"
              className="fill-foreground text-[11px] font-medium"
            >
              {dim.label}
            </text>
          )
        })}
      </svg>

      <p className="text-xs text-muted-foreground text-center mt-2 italic">
        Visual representation of genetic associations only — not a personality assessment.
      </p>
    </div>
  )
}
