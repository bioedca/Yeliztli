/** Chronotype dial visualization for Gene Sleep module (P3-50).
 *
 * Displays the user's chronotype tendency on a semicircular dial
 * ranging from "Early Bird" (morning) through "Intermediate" to
 * "Night Owl" (evening), based on the Chronotype & Circadian Rhythm
 * pathway level.
 */

import { cn } from "@/lib/utils"
import type { PathwayLevel } from "@/types/sleep"
import { Sun, Moon, Sunrise, CircleHelp } from "lucide-react"

interface ChronotypeDialProps {
  level: PathwayLevel
  /**
   * Number of chronotype SNPs actually genotyped. When 0, the only chronotype
   * marker (PER3 rs57875989) was not called, so a morning/evening tendency cannot
   * be determined — the dial must show an insufficient-data state rather than the
   * default `Standard` → "Early Bird" label (gh #167).
   */
  calledSnps?: number
  className?: string
}

type DialConfig = {
  label: string
  description: string
  rotation: number
  color: string
  icon: typeof Sun
}

/** Shown when no chronotype marker was genotyped — never a directional call. */
const INSUFFICIENT_CONFIG: DialConfig = {
  label: "Insufficient data",
  description:
    "No chronotype marker (PER3 rs57875989) was genotyped in this sample, so a " +
    "morning/evening tendency cannot be determined.",
  rotation: 90,
  color: "text-muted-foreground",
  icon: CircleHelp,
}

const DIAL_CONFIG: Record<PathwayLevel, DialConfig> = {
  Elevated: {
    label: "Night Owl",
    description: "Genetic variants suggest a strong evening chronotype preference.",
    rotation: 135,
    color: "text-amber-600 dark:text-amber-400",
    icon: Moon,
  },
  Moderate: {
    label: "Intermediate",
    description: "Mixed chronotype signals — moderate flexibility in sleep timing.",
    rotation: 90,
    color: "text-blue-600 dark:text-blue-400",
    icon: Sunrise,
  },
  Standard: {
    label: "Early Bird",
    description: "No strong evening chronotype variants detected — typical morning tendency.",
    rotation: 45,
    color: "text-emerald-600 dark:text-emerald-400",
    icon: Sun,
  },
}

export default function ChronotypeDial({ level, calledSnps, className }: ChronotypeDialProps) {
  // No genotyped chronotype marker ⇒ withhold any directional call (gh #167).
  const noData = calledSnps === 0
  const config = noData ? INSUFFICIENT_CONFIG : DIAL_CONFIG[level] || DIAL_CONFIG.Standard
  const Icon = config.icon

  // SVG semicircle dial with needle indicator
  // Arc from 0° (left, morning) to 180° (right, evening)
  const needleAngle = config.rotation
  const needleRad = (needleAngle * Math.PI) / 180
  const cx = 100
  const cy = 90
  const radius = 70
  const needleX = cx + radius * 0.85 * Math.cos(Math.PI - needleRad)
  const needleY = cy - radius * 0.85 * Math.sin(Math.PI - needleRad)

  return (
    <div className={cn("rounded-lg border bg-card p-5", className)}>
      <h3 className="text-sm font-semibold mb-3">Chronotype Tendency</h3>
      <div className="flex flex-col items-center">
        <svg
          viewBox="0 0 200 110"
          className="w-full max-w-[240px]"
          role="img"
          aria-label={`Chronotype dial showing ${config.label}`}
        >
          {/* Background arc segments */}
          {/* Morning (green) — 0° to 60° */}
          <path
            d={describeArc(cx, cy, radius, 0, 60)}
            fill="none"
            stroke="currentColor"
            strokeWidth="12"
            strokeLinecap="round"
            className="text-emerald-200 dark:text-emerald-900/50"
          />
          {/* Intermediate (blue) — 60° to 120° */}
          <path
            d={describeArc(cx, cy, radius, 60, 120)}
            fill="none"
            stroke="currentColor"
            strokeWidth="12"
            strokeLinecap="round"
            className="text-blue-200 dark:text-blue-900/50"
          />
          {/* Evening (amber) — 120° to 180° */}
          <path
            d={describeArc(cx, cy, radius, 120, 180)}
            fill="none"
            stroke="currentColor"
            strokeWidth="12"
            strokeLinecap="round"
            className="text-amber-200 dark:text-amber-900/50"
          />

          {/* Needle — omitted when there is no chronotype data, so the dial never
              points at a direction it cannot support (gh #167). */}
          {!noData && (
            <line
              x1={cx}
              y1={cy}
              x2={needleX}
              y2={needleY}
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              className={config.color}
            />
          )}
          {/* Center dot */}
          <circle cx={cx} cy={cy} r="5" fill="currentColor" className={config.color} />

          {/* Labels */}
          <text x="18" y="105" className="fill-muted-foreground text-[9px]" textAnchor="middle">
            Early
          </text>
          <text x={cx} y="14" className="fill-muted-foreground text-[9px]" textAnchor="middle">
            Intermediate
          </text>
          <text x="182" y="105" className="fill-muted-foreground text-[9px]" textAnchor="middle">
            Evening
          </text>
        </svg>

        {/* Result label */}
        <div className="flex items-center gap-2 mt-2">
          <Icon className={cn("h-5 w-5", config.color)} aria-hidden="true" />
          <span className={cn("font-semibold", config.color)}>{config.label}</span>
        </div>
        <p className="text-xs text-muted-foreground text-center mt-1 max-w-[260px]">
          {config.description}
        </p>
      </div>
    </div>
  )
}

/** Describe a semicircular arc path for SVG. */
function describeArc(
  cx: number,
  cy: number,
  r: number,
  startAngle: number,
  endAngle: number,
): string {
  const startRad = (startAngle * Math.PI) / 180
  const endRad = (endAngle * Math.PI) / 180
  const x1 = cx + r * Math.cos(Math.PI - startRad)
  const y1 = cy - r * Math.sin(Math.PI - startRad)
  const x2 = cx + r * Math.cos(Math.PI - endRad)
  const y2 = cy - r * Math.sin(Math.PI - endRad)
  const largeArc = endAngle - startAngle > 180 ? 1 : 0
  return `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`
}
