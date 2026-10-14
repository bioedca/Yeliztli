/** High-confidence findings preview for the dashboard (P1-20, wired P3-43a).
 *
 * Shows top findings (>=3 stars) across all analysis modules from the
 * findings summary API. Links to the full findings explorer page.
 */

import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  HIGH_CONFIDENCE_MIN_STARS,
  MIN_STARS_PARAM,
} from '@/lib/findings-confidence'
import {
  Star,
  Pill,
  Apple,
  ShieldAlert,
  HeartPulse,
  Brain,
  Baby,
  Globe,
  SearchCheck,
  ClipboardList,
  Loader2,
  type LucideIcon,
} from 'lucide-react'
import EvidenceStars from '@/components/ui/EvidenceStars'
import { useFindingsSummary } from '@/api/findings'
import type { Finding } from '@/types/findings'

const MODULE_ICONS: Record<string, LucideIcon> = {
  pharmacogenomics: Pill,
  nutrigenomics: Apple,
  cancer: ShieldAlert,
  cardiovascular: HeartPulse,
  apoe: Brain,
  carrier: Baby,
  ancestry: Globe,
  rare_variants: SearchCheck,
}

function moduleLabel(key: string): string {
  const labels: Record<string, string> = {
    pharmacogenomics: 'Pharmacogenomics',
    nutrigenomics: 'Nutrigenomics',
    cancer: 'Cancer',
    cardiovascular: 'Cardiovascular',
    apoe: 'APOE',
    carrier: 'Carrier Status',
    ancestry: 'Ancestry',
    rare_variants: 'Rare Variants',
  }
  return labels[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function FindingItem({ finding, sampleParam }: { finding: Finding; sampleParam: string | null }) {
  const Icon = MODULE_ICONS[finding.module] ?? ClipboardList

  return (
    <div className="flex items-start gap-3 py-2">
      <div className="pt-0.5">
        <EvidenceStars level={finding.evidence_level ?? 0} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          {finding.gene_symbol && (
            <Link
              to={`/genes/${finding.gene_symbol}${sampleParam ? `?sample_id=${sampleParam}` : ''}`}
              className="font-mono text-xs font-medium text-foreground hover:text-primary hover:underline"
            >
              {finding.gene_symbol}
            </Link>
          )}
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Icon className="h-3 w-3" />
            {moduleLabel(finding.module)}
          </span>
        </div>
        <p className="mt-0.5 text-sm text-foreground leading-snug line-clamp-1">
          {finding.finding_text}
        </p>
      </div>
    </div>
  )
}

/** The section frame shared by every non-list state. */
function PreviewShell({ children }: { children: ReactNode }) {
  return (
    <section aria-label="High-confidence findings">
      <h2 className="text-sm font-semibold text-foreground mb-3">
        High-Confidence Findings
      </h2>
      <div className={cn('rounded-lg border bg-card p-6 text-center')}>{children}</div>
    </section>
  )
}

export default function FindingsPreview({ sampleId }: { sampleId: number | null }) {
  const [searchParams] = useSearchParams()
  const sampleParam = searchParams.get('sample_id')
  const { data: summary, isLoading, isFetching, isError, refetch } = useFindingsSummary(sampleId)

  const highConfidence = summary?.high_confidence_findings ?? []

  // The section is explicitly about >=3-star findings and the preview is capped
  // at five, so neither `total_findings` nor `highConfidence.length` is the real
  // figure. `total_findings` is the unfiltered all-module count -- printing it
  // under this heading read as ~311k high-confidence findings when there were 55
  // (#2047). Derive the true count from the per-level counts the summary already
  // carries; if the API omits them, say nothing rather than guess.
  const highConfidenceTotal =
    summary?.evidence_level_counts?.reduce(
      (sum, { evidence_level, count }) =>
        evidence_level >= HIGH_CONFIDENCE_MIN_STARS ? sum + count : sum,
      0,
    ) ?? null

  // Land on the same set the count describes. FindingsExplorer seeds its
  // min-stars filter from this parameter, so the destination matches the label.
  const highConfidenceHref = (() => {
    const params = new URLSearchParams()
    if (sampleParam) params.set('sample_id', sampleParam)
    if (highConfidenceTotal !== null) {
      params.set(MIN_STARS_PARAM, String(HIGH_CONFIDENCE_MIN_STARS))
    }
    const query = params.toString()
    return `/findings${query ? `?${query}` : ''}`
  })()

  // The summary takes seconds on a real sample. Until it arrives, or if it
  // fails, an empty list says nothing about the sample, so neither state may
  // fall through to "No findings yet — Run annotation" (#2106). That includes a
  // cached empty summary being refetched after annotation starts or finishes:
  // TanStack keeps the cached data while fetching, so `isLoading` is false.
  const listIsEmpty = highConfidence.length === 0
  if (isLoading || (listIsEmpty && isFetching)) {
    return (
      <PreviewShell>
        <p role="status" className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading findings…
        </p>
      </PreviewShell>
    )
  }

  if (isError && (!summary || listIsEmpty)) {
    return (
      <PreviewShell>
        <p role="alert" className="text-sm text-muted-foreground">
          Couldn&apos;t load findings.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 text-xs text-primary hover:underline"
        >
          Try again
        </button>
      </PreviewShell>
    )
  }

  if (listIsEmpty) {
    // An annotated sample can have findings without any high-confidence ones;
    // telling it to run annotation would be wrong (#2106).
    if ((summary?.total_findings ?? 0) > 0) {
      return (
        <PreviewShell>
          <p className="text-sm text-muted-foreground">No high-confidence findings</p>
          <p className="mt-1 text-xs text-muted-foreground">
            None of this sample&apos;s findings is rated {HIGH_CONFIDENCE_MIN_STARS} stars or
            higher.
          </p>
          <Link
            to={`/findings${sampleParam ? `?sample_id=${sampleParam}` : ''}`}
            className="mt-2 inline-block text-xs text-primary hover:underline"
          >
            Show all findings
          </Link>
        </PreviewShell>
      )
    }
    return (
      <PreviewShell>
        <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-muted">
          <Star className="h-5 w-5 text-muted-foreground" />
        </div>
        <p className="mt-3 text-sm text-muted-foreground">
          No findings yet
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Run annotation to see high-confidence findings across all analysis modules.
        </p>
      </PreviewShell>
    )
  }

  return (
    <section aria-label="High-confidence findings">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-foreground">
          High-Confidence Findings
        </h2>
        {summary && (
          <Link
            to={highConfidenceHref}
            className="text-xs text-primary hover:underline"
          >
            {highConfidenceTotal === null
              ? 'Show all findings →'
              : `Show all ${highConfidenceTotal.toLocaleString()} →`}
          </Link>
        )}
      </div>

      <div className="rounded-lg border bg-card divide-y">
        {highConfidence.map((finding) => (
          <div key={finding.id} className="px-4">
            <FindingItem finding={finding} sampleParam={sampleParam} />
          </div>
        ))}
      </div>
    </section>
  )
}
