/** Concordance report page (Step 70 / MRG-06; Plan §10.6, §10.7).
 *
 * Mounted at `/samples/:id/concordance`. Renders three regions for the
 * merged sample identified by the URL `id`:
 *
 *   1. Header card — sample id, merge metadata (strategy, source count,
 *      merged-at timestamp), back-link to the sample dashboard.
 *   2. Concordance summary — Plan §10.4c bucket counts (match,
 *      filled_nocall, discordant, unique_S1, unique_S2, collapsed_rsid)
 *      rendered as a tabular-numeric stat grid. Sourced from the
 *      `merge_provenance` row via `useMergeProvenance`.
 *   3. Paginated discordant-loci table — one row per `(chrom, pos)` with
 *      gene context (`gene_symbol`, `consequence`, `clinvar_significance`)
 *      from the backend's LEFT JOIN against `annotated_variants`. Prev /
 *      next pagination over the Plan §10.6 default `limit=50` window;
 *      `placeholderData: keepPreviousData` keeps the table mounted across
 *      page transitions (TanStack Query paginated-queries pattern).
 *
 * Error surface:
 *   - 423 (`require_fresh_sample` gate, Plan §7.5) — surface a re-annotate
 *     CTA; the rest of the page does not render.
 *   - 404 (sample exists but isn't merged) — render an empty state pointing
 *     the user back to the dashboard rather than a destructive error.
 *   - All other failures — generic `<PageError>` with retry.
 */

import { useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import {
  ArrowLeft,
  FileText,
  GitMerge,
  Hourglass,
  Lock,
  RefreshCw,
} from "lucide-react"

import {
  useConcordanceReport,
  useMergeProvenance,
  type SamplesApiError,
} from "@/api/samples"
import type {
  ConcordanceReportResponse,
  ConcordanceSummary,
  MergeProvenanceResponse,
} from "@/types/individuals"
import { formatNumber } from "@/lib/format"
import PageEmpty from "@/components/ui/PageEmpty"
import PageError from "@/components/ui/PageError"
import PageLoading from "@/components/ui/PageLoading"

const PAGE_SIZE = 50

const SUMMARY_ROWS: ReadonlyArray<{
  key: keyof ConcordanceSummary
  label: string
  hint: string
}> = [
  { key: "match", label: "Match", hint: "Same call on both sources" },
  {
    key: "filled_nocall",
    label: "Filled no-call",
    hint: "One source no-call, the other called",
  },
  {
    key: "discordant",
    label: "Discordant",
    hint: "Both called, different genotype",
  },
  { key: "unique_S1", label: "Unique to S₁", hint: "Only the first source covered this locus" },
  { key: "unique_S2", label: "Unique to S₂", hint: "Only the second source covered this locus" },
  {
    key: "collapsed_rsid",
    label: "Collapsed rsIDs",
    hint: "Different rsIDs at the same coordinate folded into one row",
  },
]

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—"
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}

function renderStrategyLabel(strategy: string): string {
  if (strategy === "flag_only") return "Flag discordant"
  if (strategy === "prefer_s1") return "Prefer S1"
  if (strategy === "prefer_s2") return "Prefer S2"
  if (strategy === "prefer_23andme") return "Prefer 23andMe"
  if (strategy === "prefer_ancestrydna") return "Prefer AncestryDNA"
  return strategy
}

function StaleSampleNotice({
  error,
  sampleId,
}: {
  error: SamplesApiError
  sampleId: number
}) {
  const detail =
    error.body && typeof error.body === "object"
      ? ((error.body as { detail?: unknown }).detail as
          | { update_url?: string; reannotate_url?: string; message?: string }
          | undefined)
      : undefined
  const reannotateUrl =
    detail?.reannotate_url ?? `/api/annotation/${sampleId}`
  return (
    <div
      className="rounded-lg border border-amber-500/50 bg-amber-500/5 p-5"
      role="alert"
      data-testid="concordance-stale-banner"
    >
      <div className="flex items-start gap-3">
        <Lock className="h-5 w-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="font-medium text-foreground">
            Merged sample needs re-annotation
          </p>
          <p className="text-sm text-muted-foreground mt-1">
            {detail?.message ??
              error.message ??
              "This sample was annotated against an older VEP bundle. Re-annotate to view the concordance report."}
          </p>
          <a
            href={reannotateUrl}
            className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 py-1.5 text-sm font-medium hover:bg-accent transition-colors"
            data-testid="concordance-reannotate-cta"
          >
            <RefreshCw className="h-3.5 w-3.5" /> Re-annotate sample
          </a>
        </div>
      </div>
    </div>
  )
}

function SummaryCard({
  summary,
  totalDiscordant,
}: {
  summary: ConcordanceSummary
  totalDiscordant: number
}) {
  return (
    <section
      aria-label="Concordance summary"
      className="rounded-lg border bg-card p-5"
      data-testid="concordance-summary"
    >
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-foreground">
          Concordance Summary
        </h2>
        <span className="text-xs text-muted-foreground">
          Discordant rows in detail table:{" "}
          <span
            className="font-medium text-foreground tabular-nums"
            data-testid="concordance-total-discordant"
          >
            {formatNumber(totalDiscordant)}
          </span>
        </span>
      </div>
      <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {SUMMARY_ROWS.map((row) => (
          <div
            key={row.key}
            className="rounded-md border border-border/60 bg-muted/20 p-3"
            data-testid={`concordance-bucket-${row.key}`}
          >
            <dt className="text-xs text-muted-foreground" title={row.hint}>
              {row.label}
            </dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums text-foreground">
              {formatNumber(summary[row.key] ?? 0)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function HeaderCard({
  sampleId,
  provenance,
}: {
  sampleId: number
  provenance: MergeProvenanceResponse
}) {
  return (
    <section
      aria-label="Merged sample metadata"
      className="rounded-lg border bg-card p-5"
    >
      <div className="flex items-start gap-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
          <GitMerge className="h-5 w-5 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold text-foreground">
            Concordance Report — Sample #{sampleId}
          </h1>
          <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">Strategy</dt>
              <dd
                className="text-foreground"
                data-testid="concordance-strategy"
              >
                {renderStrategyLabel(provenance.strategy)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Sources</dt>
              <dd
                className="text-foreground tabular-nums"
                data-testid="concordance-source-count"
              >
                {provenance.source_sample_ids.length}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Source IDs</dt>
              <dd className="text-foreground tabular-nums">
                {provenance.source_sample_ids.join(", ")}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Merged at</dt>
              <dd className="text-foreground">
                {formatTimestamp(provenance.merged_at)}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>
  )
}

function DiscordantLociTable({
  data,
  totalDiscordant,
  offset,
  pageSize,
  onPrev,
  onNext,
  isFetching,
}: {
  data: ConcordanceReportResponse | undefined
  totalDiscordant: number
  offset: number
  pageSize: number
  onPrev: () => void
  onNext: () => void
  isFetching: boolean
}) {
  const loci = data?.discordant_loci ?? []
  const pageStart = totalDiscordant === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + pageSize, totalDiscordant)
  const hasPrev = offset > 0
  const hasNext = offset + pageSize < totalDiscordant

  return (
    <section
      aria-label="Discordant loci"
      className="space-y-3"
      data-testid="concordance-discordant-table"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">
          Discordant Loci
        </h2>
        <div className="flex items-center gap-3">
          <span
            className="text-xs text-muted-foreground tabular-nums"
            data-testid="concordance-page-range"
          >
            {totalDiscordant === 0
              ? "0 of 0"
              : `${formatNumber(pageStart)}–${formatNumber(pageEnd)} of ${formatNumber(totalDiscordant)}`}
          </span>
          {isFetching && (
            <span
              className="inline-flex items-center gap-1 text-xs text-muted-foreground"
              data-testid="concordance-fetching-indicator"
            >
              <Hourglass className="h-3 w-3 animate-pulse" /> Loading…
            </span>
          )}
        </div>
      </div>

      {loci.length === 0 ? (
        <PageEmpty
          icon={FileText}
          title="No discordant loci on this page"
          description={
            totalDiscordant === 0
              ? "Sources agree on every overlapping call. The summary card above shows the full per-bucket breakdown."
              : "Use the pagination controls below to navigate to a different page."
          }
        />
      ) : (
        <div className="rounded-lg border bg-card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">rsID</th>
                <th className="px-4 py-2 font-medium">Chrom</th>
                <th className="px-4 py-2 font-medium text-right">Pos</th>
                <th className="px-4 py-2 font-medium">Merged GT</th>
                <th className="px-4 py-2 font-medium">Source calls</th>
                <th className="px-4 py-2 font-medium">Alt rsID</th>
                <th className="px-4 py-2 font-medium">Gene</th>
                <th className="px-4 py-2 font-medium">Consequence</th>
                <th className="px-4 py-2 font-medium">ClinVar</th>
              </tr>
            </thead>
            <tbody>
              {loci.map((locus) => (
                <tr
                  key={`${locus.chrom}:${locus.pos}:${locus.rsid}`}
                  className="border-t border-border hover:bg-muted/20"
                  data-testid={`concordance-locus-${locus.rsid}`}
                >
                  <td className="px-4 py-2 font-mono text-xs">
                    <Link
                      to={`/variants/${locus.rsid}`}
                      className="text-primary hover:underline"
                    >
                      {locus.rsid}
                    </Link>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-foreground">
                    {locus.chrom}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-foreground">
                    {formatNumber(locus.pos)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-foreground">
                    {locus.genotype || "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-muted-foreground whitespace-pre-wrap">
                    {locus.discordant_alt_genotype || "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                    {locus.alt_rsid || "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-foreground">
                    {locus.gene_symbol ?? (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {locus.consequence ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {locus.clinvar_significance ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onPrev}
          disabled={!hasPrev || isFetching}
          className="px-3 py-1.5 text-sm rounded-md border border-input bg-background hover:bg-accent text-foreground disabled:opacity-50"
          data-testid="concordance-prev-button"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!hasNext || isFetching}
          className="px-3 py-1.5 text-sm rounded-md border border-input bg-background hover:bg-accent text-foreground disabled:opacity-50"
          data-testid="concordance-next-button"
        >
          Next
        </button>
      </div>
    </section>
  )
}

export default function ConcordanceReport() {
  const { id } = useParams<{ id: string }>()
  const sampleId = Number(id)
  const validId = Number.isFinite(sampleId) && sampleId > 0

  const [offset, setOffset] = useState(0)

  // Reset pagination when the route sample changes; a persisted offset would
  // otherwise land the next sample on a stale page index. Adjusted during
  // render (React's recommended alternative to a setState-in-effect) so the
  // reset applies before the new sample's page is read.
  const [trackedSampleId, setTrackedSampleId] = useState(sampleId)
  if (sampleId !== trackedSampleId) {
    setTrackedSampleId(sampleId)
    setOffset(0)
  }

  const provenanceQuery = useMergeProvenance(validId ? sampleId : null)
  const reportQuery = useConcordanceReport(
    validId ? sampleId : null,
    PAGE_SIZE,
    offset,
  )

  // Prefer the report's `concordance_summary` (always present on success)
  // but fall back to provenance for the header card. Both come from the
  // same `merge_provenance` row.
  const summary = useMemo<ConcordanceSummary | null>(() => {
    if (reportQuery.data) return reportQuery.data.concordance_summary
    if (provenanceQuery.data) return provenanceQuery.data.concordance_summary
    return null
  }, [reportQuery.data, provenanceQuery.data])

  if (!validId) {
    return (
      <div className="p-6 max-w-6xl mx-auto">
        <PageError message="Invalid sample id in URL." />
      </div>
    )
  }

  // 423 from either query — surface a stale-sample banner and don't render
  // the rest of the page (the gate also blocks discordant-loci reads).
  const staleError =
    (provenanceQuery.error && provenanceQuery.error.isStaleSample()
      ? provenanceQuery.error
      : null) ??
    (reportQuery.error && reportQuery.error.isStaleSample()
      ? reportQuery.error
      : null)

  if (staleError) {
    return (
      <div className="p-6 max-w-6xl mx-auto space-y-4">
        <BackLink sampleId={sampleId} />
        <StaleSampleNotice error={staleError} sampleId={sampleId} />
      </div>
    )
  }

  // 404 on provenance — the sample exists but isn't a merged sample. Don't
  // render a destructive error; point the user back to the dashboard.
  if (
    provenanceQuery.error &&
    provenanceQuery.error.status === 404
  ) {
    return (
      <div className="p-6 max-w-6xl mx-auto space-y-4">
        <BackLink sampleId={sampleId} />
        <PageEmpty
          icon={GitMerge}
          title="No merge provenance for this sample"
          description="This sample was uploaded directly rather than created from a merge of two source samples, so there is no concordance to report."
        />
      </div>
    )
  }

  if (provenanceQuery.isLoading || (!provenanceQuery.data && !provenanceQuery.error)) {
    return (
      <div className="p-6 max-w-6xl mx-auto">
        <PageLoading message="Loading merge provenance…" />
      </div>
    )
  }

  if (provenanceQuery.error || !provenanceQuery.data) {
    return (
      <div className="p-6 max-w-6xl mx-auto space-y-4">
        <BackLink sampleId={sampleId} />
        <PageError
          message={
            provenanceQuery.error
              ? provenanceQuery.error.message
              : "Failed to load merge provenance."
          }
          onRetry={() => provenanceQuery.refetch()}
        />
      </div>
    )
  }

  return (
    <div
      className="p-6 max-w-6xl mx-auto space-y-6"
      data-testid="concordance-report-page"
    >
      <BackLink sampleId={sampleId} />
      <HeaderCard sampleId={sampleId} provenance={provenanceQuery.data} />

      {summary && (
        <SummaryCard
          summary={summary}
          totalDiscordant={reportQuery.data?.total_discordant ?? 0}
        />
      )}

      {reportQuery.error && !reportQuery.error.isStaleSample() ? (
        <PageError
          message={reportQuery.error.message}
          onRetry={() => reportQuery.refetch()}
        />
      ) : reportQuery.isLoading && !reportQuery.data ? (
        <PageLoading message="Loading discordant loci…" />
      ) : (
        <DiscordantLociTable
          data={reportQuery.data}
          totalDiscordant={reportQuery.data?.total_discordant ?? 0}
          offset={offset}
          pageSize={PAGE_SIZE}
          onPrev={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
          onNext={() => setOffset((o) => o + PAGE_SIZE)}
          isFetching={reportQuery.isFetching}
        />
      )}
    </div>
  )
}

function BackLink({ sampleId }: { sampleId: number }) {
  return (
    <Link
      to={`/?sample_id=${sampleId}`}
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      data-testid="concordance-back-link"
    >
      <ArrowLeft className="h-3.5 w-3.5" /> Back to sample dashboard
    </Link>
  )
}
