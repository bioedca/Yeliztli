/** Chromosome painting visualization for LAI results (AMv2 Step 6).
 *
 * Renders 22 autosomes as horizontal bars with two rows per chromosome
 * (one per haplotype), segments colored by ancestry population.
 * Uses SVG for rendering with hover tooltips.
 */

import { useState } from "react"
import type { ChromosomePaintingSegment } from "@/types/ancestry"
import {
  POPULATION_LABELS,
  POPULATION_ORDER,
  getPopulationColor,
  resolvePopulationColors,
} from "@/components/ancestry/constants"
import type { PopulationColorMap } from "@/components/ancestry/constants"

/** Human chromosome lengths in base pairs (GRCh38). */
const CHROMOSOME_LENGTHS: Record<string, number> = {
  chr1: 248_956_422, chr2: 242_193_529, chr3: 198_295_559,
  chr4: 190_214_555, chr5: 181_538_259, chr6: 170_805_979,
  chr7: 159_345_973, chr8: 145_138_636, chr9: 138_394_717,
  chr10: 133_797_422, chr11: 135_086_622, chr12: 133_275_309,
  chr13: 114_364_328, chr14: 107_043_718, chr15: 101_991_189,
  chr16: 90_338_345, chr17: 83_257_441, chr18: 80_373_285,
  chr19: 58_617_616, chr20: 64_444_167, chr21: 46_709_983,
  chr22: 50_818_468,
}

const MAX_CHR_LENGTH = Math.max(...Object.values(CHROMOSOME_LENGTHS))

/** Sorted chromosome keys: chr1, chr2, ..., chr22. */
const SORTED_CHROMS = Array.from({ length: 22 }, (_, i) => `chr${i + 1}`)

interface TooltipState {
  x: number
  y: number
  text: string
}

interface ChromosomePaintingProps {
  painting: Record<string, ChromosomePaintingSegment[]>
  populationColors?: PopulationColorMap
}

export default function ChromosomePainting({
  painting,
  populationColors,
}: ChromosomePaintingProps) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  const resolvedPopulationColors = populationColors ?? resolvePopulationColors(null, painting)

  const labelWidth = 48
  const barPadding = 2
  const hapHeight = 8
  const chromRowHeight = hapHeight * 2 + barPadding + 12 // 2 hap bars + gap + spacing
  const chartWidth = 700
  const barWidth = chartWidth - labelWidth - 16
  const svgHeight = SORTED_CHROMS.length * chromRowHeight + 8

  function formatMb(bp: number): string {
    return `${(bp / 1_000_000).toFixed(1)} Mb`
  }

  function handleSegmentHover(
    e: React.MouseEvent<SVGRectElement>,
    chrom: string,
    seg: ChromosomePaintingSegment,
    hap: 0 | 1,
  ) {
    const pop = hap === 0 ? seg.hap0 : seg.hap1
    const label = POPULATION_LABELS[pop] ?? pop
    const rect = e.currentTarget.closest("svg")?.getBoundingClientRect()
    if (!rect) return
    setTooltip({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top - 28,
      text: `${chrom}: ${formatMb(seg.start)}\u2013${formatMb(seg.end)} \u2014 ${label}`,
    })
  }

  return (
    <div data-testid="chromosome-painting" className="relative">
      <svg
        viewBox={`0 0 ${chartWidth} ${svgHeight}`}
        className="w-full"
        style={{ maxHeight: `${svgHeight}px` }}
        onMouseLeave={() => setTooltip(null)}
      >
        {SORTED_CHROMS.map((chrom, i) => {
          const segments = painting[chrom] ?? []
          const chrLen = CHROMOSOME_LENGTHS[chrom] ?? MAX_CHR_LENGTH
          const scale = barWidth / MAX_CHR_LENGTH
          const chrWidth = chrLen * scale
          const y = i * chromRowHeight + 4

          return (
            <g key={chrom} data-testid={`painting-${chrom}`}>
              {/* Label */}
              <text
                x={labelWidth - 6}
                y={y + hapHeight + barPadding / 2}
                textAnchor="end"
                dominantBaseline="central"
                className="fill-muted-foreground"
                fontSize={10}
              >
                {chrom.replace("chr", "")}
              </text>

              {/* Background track */}
              <rect
                x={labelWidth}
                y={y}
                width={chrWidth}
                height={hapHeight * 2 + barPadding}
                rx={2}
                className="fill-muted/20"
              />

              {/* Haplotype 0 segments */}
              {segments.map((seg, si) => {
                const sx = labelWidth + seg.start * scale
                const sw = Math.max((seg.end - seg.start) * scale, 0.5)
                return (
                  <rect
                    key={`h0-${si}`}
                    x={sx}
                    y={y}
                    width={sw}
                    height={hapHeight}
                    fill={getPopulationColor(resolvedPopulationColors, seg.hap0)}
                    data-population={seg.hap0}
                    data-haplotype="0"
                    onMouseMove={(e) => handleSegmentHover(e, chrom, seg, 0)}
                    onMouseLeave={() => setTooltip(null)}
                    className="cursor-pointer"
                  />
                )
              })}

              {/* Haplotype 1 segments */}
              {segments.map((seg, si) => {
                const sx = labelWidth + seg.start * scale
                const sw = Math.max((seg.end - seg.start) * scale, 0.5)
                return (
                  <rect
                    key={`h1-${si}`}
                    x={sx}
                    y={y + hapHeight + barPadding}
                    width={sw}
                    height={hapHeight}
                    fill={getPopulationColor(resolvedPopulationColors, seg.hap1)}
                    data-population={seg.hap1}
                    data-haplotype="1"
                    onMouseMove={(e) => handleSegmentHover(e, chrom, seg, 1)}
                    onMouseLeave={() => setTooltip(null)}
                    className="cursor-pointer"
                  />
                )
              })}
            </g>
          )
        })}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute pointer-events-none z-10 rounded bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md border"
          style={{ left: tooltip.x, top: tooltip.y, transform: "translateX(-50%)" }}
        >
          {tooltip.text}
        </div>
      )}

      {/* Legend */}
      <div data-testid="painting-legend" className="flex flex-wrap gap-3 mt-3 px-1">
        {POPULATION_ORDER.map((pop) => (
          <div
            key={pop}
            className="flex items-center gap-1.5 text-xs text-muted-foreground"
            data-population={pop}
          >
            <span
              className="inline-block h-3 w-3 rounded-sm"
              style={{ backgroundColor: getPopulationColor(resolvedPopulationColors, pop) }}
            />
            {POPULATION_LABELS[pop]}
          </div>
        ))}
      </div>
    </div>
  )
}
