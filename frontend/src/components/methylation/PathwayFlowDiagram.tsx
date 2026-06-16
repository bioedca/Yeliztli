/** Pathway flow diagram for MTHFR & Methylation (P3-53).
 *
 * SVG visualization showing the 5 methylation pathways and their
 * biochemical relationships. Each node is colored by pathway level
 * (Elevated / Moderate / Standard).
 */

import { cn } from "@/lib/utils"
import type { PathwaySummary, PathwayLevel } from "@/types/methylation"

interface PathwayFlowDiagramProps {
  pathways: PathwaySummary[]
  selectedPathwayId: string | null
  onSelectPathway: (pathwayId: string) => void
}

const LEVEL_FILL: Record<PathwayLevel, { bg: string; border: string; text: string }> = {
  Elevated: {
    bg: "fill-amber-100 dark:fill-amber-950",
    border: "stroke-amber-400 dark:stroke-amber-600",
    text: "fill-amber-800 dark:fill-amber-300",
  },
  Moderate: {
    bg: "fill-blue-100 dark:fill-blue-950",
    border: "stroke-blue-400 dark:stroke-blue-600",
    text: "fill-blue-800 dark:fill-blue-300",
  },
  Standard: {
    bg: "fill-emerald-100 dark:fill-emerald-950",
    border: "stroke-emerald-400 dark:stroke-emerald-600",
    text: "fill-emerald-800 dark:fill-emerald-300",
  },
}

const SELECTED_RING = "stroke-primary stroke-[3]"

/** Positions for the 5 pathway nodes in the flow diagram. */
const NODE_LAYOUT: Record<string, { x: number; y: number; label: string }> = {
  folate_mthfr: { x: 80, y: 60, label: "Folate &\nMTHFR" },
  methionine_cycle: { x: 280, y: 60, label: "Methionine\nCycle" },
  transsulfuration: { x: 480, y: 60, label: "Trans-\nsulfuration" },
  bh4_neurotransmitter: { x: 180, y: 180, label: "BH4 &\nNeurotransmitter" },
  choline_betaine: { x: 380, y: 180, label: "Choline &\nBetaine" },
}

/** Flow connections between pathways. */
const EDGES: Array<{ from: string; to: string }> = [
  { from: "folate_mthfr", to: "methionine_cycle" },
  { from: "methionine_cycle", to: "transsulfuration" },
  { from: "folate_mthfr", to: "bh4_neurotransmitter" },
  { from: "methionine_cycle", to: "choline_betaine" },
]

const NODE_W = 130
const NODE_H = 56

function PathwayNode({
  pathwayId,
  layout,
  level,
  selected,
  onClick,
}: {
  pathwayId: string
  layout: { x: number; y: number; label: string }
  level: PathwayLevel
  selected: boolean
  onClick: () => void
}) {
  const fill = LEVEL_FILL[level] || LEVEL_FILL.Standard
  const lines = layout.label.split("\n")

  return (
    <g
      className="cursor-pointer"
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onClick()
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`${lines.join(" ")} — ${level}`}
    >
      <rect
        x={layout.x - NODE_W / 2}
        y={layout.y - NODE_H / 2}
        width={NODE_W}
        height={NODE_H}
        rx={10}
        className={cn(fill.bg, selected ? SELECTED_RING : fill.border, "stroke-[2]")}
      />
      {lines.map((line, i) => (
        <text
          key={`${pathwayId}-line-${i}`}
          x={layout.x}
          y={layout.y + (i - (lines.length - 1) / 2) * 16}
          textAnchor="middle"
          dominantBaseline="central"
          className={cn("text-[11px] font-semibold pointer-events-none", fill.text)}
        >
          {line}
        </text>
      ))}
    </g>
  )
}

export default function PathwayFlowDiagram({
  pathways,
  selectedPathwayId,
  onSelectPathway,
}: PathwayFlowDiagramProps) {
  const levelMap = new Map(pathways.map((p) => [p.pathway_id, p.level]))

  return (
    <svg
      viewBox="0 0 570 240"
      className="w-full max-w-2xl mx-auto"
      role="img"
      aria-label="Methylation pathway flow diagram showing biochemical relationships"
    >
      {/* Edges */}
      {EDGES.map(({ from, to }) => {
        const f = NODE_LAYOUT[from]
        const t = NODE_LAYOUT[to]
        if (!f || !t) return null

        // Calculate edge start/end at node boundaries
        const dx = t.x - f.x
        const dy = t.y - f.y
        const dist = Math.sqrt(dx * dx + dy * dy)
        const nx = dx / dist
        const ny = dy / dist

        const x1 = f.x + nx * (NODE_W / 2)
        const y1 = f.y + ny * (NODE_H / 2)
        const x2 = t.x - nx * (NODE_W / 2)
        const y2 = t.y - ny * (NODE_H / 2)

        return (
          <line
            key={`${from}-${to}`}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            className="stroke-border dark:stroke-muted-foreground"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            markerEnd="url(#arrowhead)"
          />
        )
      })}

      {/* Arrow marker */}
      <defs>
        <marker
          id="arrowhead"
          markerWidth="8"
          markerHeight="6"
          refX="7"
          refY="3"
          orient="auto"
        >
          <polygon
            points="0 0, 8 3, 0 6"
            className="fill-border dark:fill-muted-foreground"
          />
        </marker>
      </defs>

      {/* Nodes */}
      {Object.entries(NODE_LAYOUT).map(([id, layout]) => (
        <PathwayNode
          key={id}
          pathwayId={id}
          layout={layout}
          level={levelMap.get(id) || "Standard"}
          selected={selectedPathwayId === id}
          onClick={() => onSelectPathway(id)}
        />
      ))}
    </svg>
  )
}
