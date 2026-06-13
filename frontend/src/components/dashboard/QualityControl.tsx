/** Collapsible QC summary card for the dashboard (P1-20, P1-21).
 *
 * Shows basic sample quality metrics: call rate, heterozygosity rate, Ti/Tv.
 * When QC data is available, displays Plotly.js charts:
 *   - Per-chromosome variant count bar chart (stacked het/hom/nocall)
 *   - Per-chromosome heterozygosity rate histogram with the autosomal-rate baseline line
 */

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { formatNumber } from '@/lib/format'
import { ChevronDown, ChevronRight, FlaskConical } from 'lucide-react'
import type { QCStats } from '@/types/variants'
import ChromosomeBarChart from '@/components/charts/ChromosomeBarChart'
import HeterozygosityHistogram from '@/components/charts/HeterozygosityHistogram'

interface QualityControlProps {
  variantCount: number | null
  qcStats?: QCStats | null
}

export default function QualityControl({ variantCount, qcStats }: QualityControlProps) {
  const [expanded, setExpanded] = useState(false)

  const callRate = qcStats
    ? `${(qcStats.call_rate * 100).toFixed(2)}%`
    : '—'

  const hetRate = qcStats
    ? `${(qcStats.heterozygosity_rate * 100).toFixed(2)}%`
    : '—'

  return (
    <section aria-label="Sample quality control">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className={cn(
          'flex w-full items-center justify-between rounded-lg border bg-card px-4 py-3',
          'text-sm font-medium text-foreground hover:bg-accent/50 transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          expanded && 'rounded-b-none border-b-0',
        )}
        aria-expanded={expanded}
        aria-controls="qc-content"
      >
        <span className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 text-muted-foreground" />
          Sample QC
        </span>
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {expanded && (
        <div
          id="qc-content"
          className="rounded-b-lg border border-t-0 bg-card px-4 py-4 space-y-4"
        >
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-muted-foreground">Total Variants</p>
              <p className="text-sm font-medium text-foreground">
                {variantCount != null ? formatNumber(variantCount) : '—'}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Call Rate</p>
              <p className={cn(
                'text-sm font-medium',
                qcStats ? 'text-foreground' : 'text-muted-foreground',
              )}>
                {callRate}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Het Rate</p>
              <p className={cn(
                'text-sm font-medium',
                qcStats ? 'text-foreground' : 'text-muted-foreground',
              )}>
                {hetRate}
              </p>
            </div>
          </div>

          {qcStats && qcStats.per_chromosome.length > 0 ? (
            <div className="space-y-4">
              <ChromosomeBarChart data={qcStats.per_chromosome} />
              <HeterozygosityHistogram
                data={qcStats.per_chromosome}
                overallRate={qcStats.heterozygosity_rate}
              />
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Detailed QC charts will be available after annotation.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
