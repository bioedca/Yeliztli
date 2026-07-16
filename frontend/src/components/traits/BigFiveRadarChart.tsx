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
function computeDimensionValues(snpDetails: SNPDetail[]): Array<number | null> {
  return BIG_FIVE_DIMENSIONS.map(({ key }) => {
    const domainSnps = snpDetails.filter(
      (s) => s.trait_domain?.toLowerCase() === key,
    )
    if (domainSnps.length === 0) return null

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
  const dimensions = BIG_FIVE_DIMENSIONS.map((dimension, index) => ({
    ...dimension,
    value: values[index] ?? null,
  }))
  const assessedDimensions = dimensions.filter(
    (dimension) => dimension.value !== null,
  )
  const missingDimensions = dimensions.filter(
    (dimension) => dimension.value === null,
  )
  const assessedCount = assessedDimensions.length
  const coverageSummary = `${assessedCount} of ${n} dimensions assessed.${
    missingDimensions.length > 0
      ? ` Not assessed: ${missingDimensions.map(({ label }) => label).join(", ")}.`
      : ""
  }`

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

  // Missing dimensions have no point at any radius. A complete polygon is only
  // meaningful when every dimension has an assessed value.
  const dataPoints = dimensions.flatMap((dimension, index) => {
    if (dimension.value === null) return []
    return [
      {
        ...toXY(index, dimension.value * maxR),
        dimension,
      },
    ]
  })
  const hasCompleteProfile = assessedCount === n
  const dataPath = dataPoints.map((p) => `${p.x},${p.y}`).join(" ")

  return (
    <div className={cn("w-full max-w-[320px] mx-auto", className)}>
      <svg
        viewBox={`0 0 ${SVG_SIZE} ${SVG_SIZE}`}
        className="w-full"
        role="img"
        aria-label={`Big Five personality trait associations radar chart. ${coverageSummary} Visual representation only — no numeric claims and not a personality assessment.`}
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
          const dimension = dimensions[i]
          const isAssessed = dimension.value !== null
          return (
            <line
              key={i}
              data-big-five-axis=""
              data-dimension={dimension.key}
              data-assessment-state={isAssessed ? "assessed" : "not-assessed"}
              x1={cx}
              y1={cy}
              x2={p.x}
              y2={p.y}
              stroke="currentColor"
              strokeWidth="1"
              strokeDasharray={isAssessed ? undefined : "4 4"}
              className={isAssessed ? "text-border" : "text-muted-foreground"}
            />
          )
        })}

        {/* Incomplete data cannot form an honest profile polygon. */}
        {!hasCompleteProfile &&
          dataPoints.map(({ x, y, dimension }) => (
            <line
              key={dimension.key}
              data-big-five-measured-spoke=""
              data-dimension={dimension.key}
              x1={cx}
              y1={cy}
              x2={x}
              y2={y}
              stroke="currentColor"
              strokeWidth="2"
              className="text-primary"
            />
          ))}

        {hasCompleteProfile && (
          <polygon
            data-big-five-profile=""
            points={dataPath}
            fill="currentColor"
            fillOpacity={0.15}
            stroke="currentColor"
            strokeWidth="2"
            className="text-primary"
          />
        )}

        {/* Data points */}
        {dataPoints.map(({ x, y, dimension }) => (
          <circle
            key={dimension.key}
            data-big-five-point=""
            data-dimension={dimension.key}
            data-assessment-state="assessed"
            cx={x}
            cy={y}
            r="4"
            fill="currentColor"
            className="text-primary"
          />
        ))}

        {/* Dimension labels */}
        {BIG_FIVE_DIMENSIONS.map((dim, i) => {
          const isAssessed = dimensions[i].value !== null
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
              data-big-five-label=""
              data-dimension={dim.key}
              data-assessment-state={isAssessed ? "assessed" : "not-assessed"}
              x={x}
              y={isAssessed ? p.y : p.y - 5}
              textAnchor={textAnchor}
              dominantBaseline="central"
              className={cn(
                "text-[11px] font-medium",
                isAssessed ? "fill-foreground" : "fill-muted-foreground",
              )}
            >
              {dim.label}
              {!isAssessed && (
                <tspan
                  x={x}
                  dy="12"
                  className="fill-muted-foreground text-[9px] font-normal"
                >
                  Not assessed
                </tspan>
              )}
            </text>
          )
        })}
      </svg>

      <p
        data-big-five-coverage=""
        className="text-xs text-muted-foreground text-center mt-2"
      >
        {coverageSummary}
      </p>
      <p className="text-xs text-muted-foreground text-center mt-1 italic">
        Visual representation of genetic associations only — not a personality
        assessment.
      </p>
    </div>
  )
}
