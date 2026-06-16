/** `/individuals/{id}` page — metadata, linked samples table, and
 * aggregated high-confidence findings panel
 * (Step 50 / IND-06; Plan §9.5).
 *
 * Three sections rendered top-to-bottom:
 *
 *   1. Header card — display_name, biological_sex, notes, timestamps.
 *   2. Linked samples table — per-sample vendor, file format, variant
 *      count, status (ready when variant count loaded), created date,
 *      with click-through to filter the dashboard by that sample.
 *   3. Aggregated high-confidence findings — union across linked
 *      samples, deduplicated by rsid (per Plan §9.5), with multi-source
 *      provenance chips showing which samples carry each finding.
 *
 * Findings are fetched per linked sample from
 * `/api/analysis/findings/summary?sample_id=N` and aggregated in the
 * client. Findings without an rsid (e.g. haplogroup / pathway-level)
 * are not collapsed; the dedup contract is "same rsid across linked
 * samples → one row with multi-source provenance" so that high-evidence
 * variant-level findings don't double-count in the aggregate.
 */

import { useMemo, useState } from "react"
import { Link, useParams, useNavigate } from "react-router-dom"
import { useQueries, useQuery } from "@tanstack/react-query"
import {
  ArrowLeft,
  ClipboardList,
  FlaskConical,
  GitMerge,
  Star,
  User,
  Users,
  type LucideIcon,
} from "lucide-react"

import { useIndividual } from "@/api/individuals"
import { MergeWizard } from "@/components/individuals/MergeWizard"
import type { LinkedSample } from "@/types/individuals"
import type {
  Finding,
  FindingsSummaryResponse,
} from "@/types/findings"
import { formatFileFormat, formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"
import EvidenceStars from "@/components/ui/EvidenceStars"
import PageEmpty from "@/components/ui/PageEmpty"
import PageError from "@/components/ui/PageError"
import PageLoading from "@/components/ui/PageLoading"

interface AggregatedFinding {
  /** Stable key — `rsid` when present, otherwise `module:sampleId:findingId`. */
  key: string
  /** Representative finding (highest evidence_level wins ties). */
  finding: Finding
  /** Sample names contributing to this finding (provenance chips). */
  sourceSamples: LinkedSample[]
}

function aggregateFindings(
  perSample: Array<{ sample: LinkedSample; findings: Finding[] | undefined }>,
): AggregatedFinding[] {
  const byKey = new Map<string, AggregatedFinding>()

  for (const { sample, findings } of perSample) {
    if (!findings) continue
    for (const finding of findings) {
      // Per Plan §9.5: dedupe by rsid when present; otherwise treat each
      // sample's null-rsid finding as its own row so haplogroup and
      // pathway-level findings still surface per sample.
      const key = finding.rsid
        ? `rsid:${finding.rsid}`
        : `noid:${sample.id}:${finding.module}:${finding.id}`

      const existing = byKey.get(key)
      if (!existing) {
        byKey.set(key, {
          key,
          finding,
          sourceSamples: [sample],
        })
        continue
      }

      // Keep the highest evidence-level finding as the representative,
      // accumulate source samples for provenance chips.
      if (
        (finding.evidence_level ?? 0) > (existing.finding.evidence_level ?? 0)
      ) {
        existing.finding = finding
      }
      if (!existing.sourceSamples.some((s) => s.id === sample.id)) {
        existing.sourceSamples.push(sample)
      }
    }
  }

  return Array.from(byKey.values()).sort((a, b) => {
    const evidenceDiff =
      (b.finding.evidence_level ?? 0) - (a.finding.evidence_level ?? 0)
    if (evidenceDiff !== 0) return evidenceDiff
    return a.finding.module.localeCompare(b.finding.module)
  })
}

function vendorLabel(vendor: string | null | undefined): string {
  if (!vendor) return "—"
  if (vendor === "23andme") return "23andMe"
  if (vendor === "ancestrydna") return "AncestryDNA"
  return vendor.charAt(0).toUpperCase() + vendor.slice(1)
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—"
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleDateString()
}

const MODULE_ICONS: Record<string, LucideIcon> = {
  pharmacogenomics: ClipboardList,
  nutrigenomics: ClipboardList,
  cancer: ClipboardList,
  cardiovascular: ClipboardList,
  apoe: ClipboardList,
  carrier: ClipboardList,
  ancestry: ClipboardList,
  rare_variants: ClipboardList,
}

function FindingRow({ row }: { row: AggregatedFinding }) {
  const Icon = MODULE_ICONS[row.finding.module] ?? ClipboardList
  return (
    <div
      className="flex items-start gap-3 px-4 py-3"
      data-testid={`aggregated-finding-${row.key}`}
    >
      <div className="pt-0.5">
        <EvidenceStars level={row.finding.evidence_level ?? 0} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          {row.finding.gene_symbol && (
            <span className="font-mono text-xs font-medium text-foreground">
              {row.finding.gene_symbol}
            </span>
          )}
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Icon className="h-3 w-3" />
            {row.finding.module.replace(/_/g, " ")}
          </span>
          {row.finding.rsid && (
            <span className="font-mono text-xs text-muted-foreground">
              {row.finding.rsid}
            </span>
          )}
        </div>
        <p className="mt-0.5 text-sm text-foreground leading-snug">
          {row.finding.finding_text}
        </p>
        <div
          className="mt-1.5 flex items-center gap-1 flex-wrap"
          aria-label="Source samples"
        >
          {row.sourceSamples.map((sample) => (
            <span
              key={sample.id}
              className={cn(
                "inline-flex items-center gap-1 rounded-full",
                "border border-border bg-muted/40 px-2 py-0.5",
                "text-[10px] font-medium text-muted-foreground",
              )}
              data-testid={`provenance-chip-${row.key}-${sample.id}`}
            >
              <FlaskConical className="h-2.5 w-2.5" />
              {sample.name}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

function LinkedSampleRow({ sample }: { sample: LinkedSample }) {
  const {
    data: variantCount,
    isLoading: countLoading,
    isError: countError,
  } = useQuery({
    queryKey: ["variants-total-count", sample.id],
    queryFn: async () => {
      const res = await fetch(`/api/variants/count?sample_id=${sample.id}`)
      if (!res.ok) return null
      const body = (await res.json()) as { total?: number }
      return body.total ?? null
    },
    staleTime: Infinity,
  })

  const statusLabel = countLoading
    ? "Loading…"
    : countError
      ? "Unavailable"
      : variantCount == null || variantCount === 0
        ? "No variants"
        : "Ready"

  return (
    <tr
      className="border-t border-border hover:bg-muted/30"
      data-testid={`linked-sample-row-${sample.id}`}
    >
      <td className="px-4 py-2 text-sm">
        <Link
          to={`/?sample_id=${sample.id}`}
          className="font-medium text-foreground hover:text-primary hover:underline"
        >
          {sample.name}
        </Link>
      </td>
      <td className="px-4 py-2 text-sm text-foreground">
        {vendorLabel(sample.vendor)}
      </td>
      <td className="px-4 py-2 text-xs text-muted-foreground">
        {formatFileFormat(sample.file_format)}
      </td>
      <td className="px-4 py-2 text-sm text-right tabular-nums text-foreground">
        {countLoading || countError || variantCount == null
          ? "—"
          : formatNumber(variantCount)}
      </td>
      <td className="px-4 py-2 text-xs text-muted-foreground">{statusLabel}</td>
      <td className="px-4 py-2 text-xs text-muted-foreground">
        {formatTimestamp(sample.created_at)}
      </td>
    </tr>
  )
}

export default function IndividualDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const individualId = Number(id)
  const validId = Number.isFinite(individualId) && individualId > 0

  const {
    data: individual,
    isLoading,
    isError,
    error,
    refetch,
  } = useIndividual(validId ? individualId : null)

  const linkedSamples = individual?.linked_samples ?? []

  // Plan §10.7 — "Merge samples" action is visible only when ≥2 linked
  // samples. The two-source wizard surface (Plan §10.5 step 1) covers
  // exactly-2 linked samples; 3+ sample pickers are deferred.
  const [showMergeWizard, setShowMergeWizard] = useState(false)
  const canMerge = linkedSamples.length === 2
  const mergeSourcePair = canMerge
    ? ([linkedSamples[0].id, linkedSamples[1].id] as [number, number])
    : null

  const findingsQueries = useQueries({
    queries: linkedSamples.map((sample) => ({
      queryKey: ["findings-summary", sample.id] as const,
      queryFn: async (): Promise<FindingsSummaryResponse> => {
        const res = await fetch(
          `/api/analysis/findings/summary?sample_id=${sample.id}`,
        )
        if (!res.ok) {
          throw new Error(`Findings summary failed: ${res.status}`)
        }
        return (await res.json()) as FindingsSummaryResponse
      },
      staleTime: Infinity,
    })),
  })

  // findingsQueries identity churns each render; key the memo on a
  // fingerprint of the resolved findings so we recompute whenever the
  // underlying data actually changes. The fingerprint includes every
  // mutable field that affects aggregation or rendering (evidence_level,
  // module, rsid, gene_symbol, finding_text) — not just finding ids —
  // so an updated payload reusing the same ids still recomputes.
  const sampleIdsKey = linkedSamples.map((s) => s.id).join(",")
  const findingsFingerprint = findingsQueries
    .map(
      (q) =>
        q.data?.high_confidence_findings
          ?.map((f) =>
            [
              f.id,
              f.evidence_level ?? "",
              f.module,
              f.rsid ?? "",
              f.gene_symbol ?? "",
              f.finding_text,
            ].join("~"),
          )
          .join(",") ?? "",
    )
    .join("|")
  const aggregated = useMemo(
    () =>
      aggregateFindings(
        linkedSamples.map((sample, idx) => ({
          sample,
          findings: findingsQueries[idx]?.data?.high_confidence_findings,
        })),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sampleIdsKey, findingsFingerprint],
  )

  if (!validId) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <PageError message="Invalid individual id in URL." />
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <PageLoading message="Loading individual…" />
      </div>
    )
  }

  if (isError || !individual) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <PageError
          message={
            error instanceof Error
              ? error.message
              : "Failed to load the individual."
          }
          onRetry={() => refetch()}
        />
      </div>
    )
  }

  const anyFindingsLoading = findingsQueries.some(
    (q) => linkedSamples.length > 0 && q.isLoading,
  )

  // The aggregated section is built from each sample's /findings/summary
  // high_confidence_findings, which the backend caps at a top-N preview per
  // sample, whereas the header `aggregated_findings_count` is the uncapped
  // deduplicated total. Surface that gap rather than presenting two
  // contradictory totals with the preview list silently truncated (#827).
  const hiddenFindingsCount = Math.max(
    0,
    individual.aggregated_findings_count - aggregated.length,
  )

  return (
    <div
      className="p-6 max-w-5xl mx-auto space-y-6"
      data-testid="individual-detail-page"
    >
      <div>
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back
        </button>
      </div>

      {/* ── 1. Metadata header ─────────────────────────────── */}
      <section
        aria-label="Individual metadata"
        className="rounded-lg border bg-card p-5"
      >
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
            <User className="h-6 w-6 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-semibold text-foreground">
              {individual.display_name}
            </h1>
            <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
              <div>
                <dt className="text-muted-foreground">Biological sex</dt>
                <dd className="text-foreground">
                  {individual.biological_sex ?? "—"}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Linked samples</dt>
                <dd className="text-foreground tabular-nums">
                  {linkedSamples.length}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">High-confidence findings</dt>
                <dd className="text-foreground tabular-nums">
                  {individual.aggregated_findings_count}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Created</dt>
                <dd className="text-foreground">
                  {formatTimestamp(individual.created_at)}
                </dd>
              </div>
            </dl>
            {individual.notes && (
              <p className="mt-3 text-sm text-muted-foreground whitespace-pre-wrap">
                {individual.notes}
              </p>
            )}
          </div>
        </div>
      </section>

      {/* ── 2. Linked samples table ────────────────────────── */}
      <section aria-label="Linked samples">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-foreground">
            Linked Samples
          </h2>
          {canMerge && (
            <button
              type="button"
              onClick={() => setShowMergeWizard(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
              data-testid="merge-samples-button"
            >
              <GitMerge className="h-3.5 w-3.5" />
              Merge samples
            </button>
          )}
        </div>
        {linkedSamples.length === 0 ? (
          <PageEmpty
            icon={Users}
            title="No samples linked yet"
            description="Assign existing samples to this individual from Settings → Samples, or upload a new file."
          />
        ) : (
          <div className="rounded-lg border bg-card overflow-x-auto">
            <table className="w-full text-left">
              <thead className="bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">Sample</th>
                  <th className="px-4 py-2 font-medium">Vendor</th>
                  <th className="px-4 py-2 font-medium">Format</th>
                  <th className="px-4 py-2 font-medium text-right">Variants</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {linkedSamples.map((sample) => (
                  <LinkedSampleRow key={sample.id} sample={sample} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {showMergeWizard && mergeSourcePair && (
        <MergeWizard
          individualId={individual.id}
          individualDisplayName={individual.display_name}
          linkedSamples={linkedSamples}
          sourceSampleIds={mergeSourcePair}
          onClose={() => setShowMergeWizard(false)}
        />
      )}

      {/* ── 3. Aggregated high-confidence findings ─────────── */}
      <section aria-label="Aggregated high-confidence findings">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-foreground">
            High-Confidence Findings (Aggregated)
          </h2>
          {aggregated.length > 0 && (
            <span className="text-xs text-muted-foreground tabular-nums">
              {hiddenFindingsCount > 0
                ? `showing ${aggregated.length} of ${individual.aggregated_findings_count}`
                : `${aggregated.length} unique`}
            </span>
          )}
        </div>

        {linkedSamples.length === 0 ? (
          <PageEmpty
            icon={Star}
            title="Link samples to see aggregated findings"
            description="High-confidence findings are unioned across this individual's linked samples and deduplicated by rsid."
          />
        ) : anyFindingsLoading ? (
          <PageLoading message="Aggregating findings…" />
        ) : aggregated.length === 0 ? (
          <PageEmpty
            icon={Star}
            title="No high-confidence findings yet"
            description="Findings appear here once linked samples finish annotation."
          />
        ) : (
          <div className="rounded-lg border bg-card divide-y">
            {aggregated.map((row) => (
              <FindingRow key={row.key} row={row} />
            ))}
            {hiddenFindingsCount > 0 && (
              <p
                className="px-4 py-2 text-xs text-muted-foreground"
                data-testid="aggregated-findings-overflow"
              >
                and {hiddenFindingsCount} more high-confidence finding
                {hiddenFindingsCount === 1 ? "" : "s"} not shown — this section is a
                top-findings preview per sample; open each sample to see all.
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
