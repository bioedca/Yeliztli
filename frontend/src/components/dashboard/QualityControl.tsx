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
import type { HetOutlierStatus, QCMetrics, SexCheckStatus } from '@/types/qc'
import ChromosomeBarChart from '@/components/charts/ChromosomeBarChart'
import HeterozygosityHistogram from '@/components/charts/HeterozygosityHistogram'

interface QualityControlProps {
  variantCount: number | null
  qcStats?: QCStats | null
  qcMetrics?: QCMetrics | null
}

const HET_STATUS_COPY: Record<HetOutlierStatus, { label: string; text: string; tone: string }> = {
  within_range: {
    label: 'Within range',
    text: 'Heterozygosity is within the expected range for this genotyping array.',
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200',
  },
  outlier: {
    label: 'Outlier',
    text: 'Heterozygosity is outside the expected range for this genotyping array.',
    tone: 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200',
  },
  insufficient_samples: {
    label: 'Not enough samples',
    text: 'Not enough samples to compare.',
    tone: 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-200',
  },
  insufficient_comparable_samples: {
    label: 'No comparable array peers',
    text: 'No other samples on the same genotyping array to compare against.',
    tone: 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200',
  },
}

const SEX_CHECK_COPY: Record<SexCheckStatus, { label: string; text: string; tone: string }> = {
  concordant: {
    label: 'Concordant',
    text: 'Recorded and inferred sex are concordant.',
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200',
  },
  discordant: {
    label: 'Discordant',
    text: 'Recorded and inferred sex are discordant.',
    tone: 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200',
  },
  indeterminate: {
    label: 'Indeterminate',
    text: 'Sex concordance is indeterminate.',
    tone: 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-200',
  },
}

function StatusBadge({ children, tone }: { children: string; tone: string }) {
  return (
    <span className={cn('inline-flex rounded-full border px-2 py-0.5 text-xs font-medium', tone)}>
      {children}
    </span>
  )
}

function formatSexValue(value: string | null): string {
  return value?.trim() || 'unavailable'
}

export default function QualityControl({ variantCount, qcStats, qcMetrics }: QualityControlProps) {
  const [expanded, setExpanded] = useState(false)

  const callRate = qcStats
    ? `${(qcStats.call_rate * 100).toFixed(2)}%`
    : '—'

  const hetRate = qcStats
    ? `${(qcStats.heterozygosity_rate * 100).toFixed(2)}%`
    : '—'

  const hasInterpretiveMetrics =
    qcMetrics?.computed === true &&
    (qcMetrics.het_outlier_status != null || qcMetrics.sex_check != null)
  const hetStatus = qcMetrics?.het_outlier_status
    ? HET_STATUS_COPY[qcMetrics.het_outlier_status]
    : null
  const sexStatus = qcMetrics?.sex_check ? SEX_CHECK_COPY[qcMetrics.sex_check] : null

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

          {hasInterpretiveMetrics && (
            <div
              className="border-t border-border pt-4"
              data-testid="qc-interpretive-metrics"
            >
              <div className="grid gap-4 md:grid-cols-2">
                {hetStatus && (
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        Heterozygosity check
                      </p>
                      <StatusBadge tone={hetStatus.tone}>{hetStatus.label}</StatusBadge>
                    </div>
                    <p className="text-sm text-foreground">{hetStatus.text}</p>
                    {typeof qcMetrics?.het_outlier_z === 'number' && (
                      <p className="text-xs text-muted-foreground">
                        z-score {qcMetrics.het_outlier_z.toFixed(2)}
                      </p>
                    )}
                  </div>
                )}

                {sexStatus && (
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        Sex concordance
                      </p>
                      <StatusBadge tone={sexStatus.tone}>{sexStatus.label}</StatusBadge>
                    </div>
                    <p className="text-sm text-foreground">{sexStatus.text}</p>
                    <p className="text-xs text-muted-foreground">
                      Inferred: {formatSexValue(qcMetrics?.genetic_sex ?? null)} · Recorded:{' '}
                      {formatSexValue(qcMetrics?.recorded_sex ?? null)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Concordance check only; not an aneuploidy assessment.
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
