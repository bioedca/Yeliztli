/** Analysis Module Dashboard — all findings sorted by evidence (P3-43).
 *
 * Shows all findings from every analysis module in a unified view,
 * sorted by evidence level (highest first). Supports filtering by
 * module and minimum evidence level. Each finding links to its
 * module page for detailed exploration.
 */

import { useState, useMemo } from "react"
import { useSearchParams, Link } from "react-router-dom"
import { ClipboardList, Star, Filter } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { pathwayLevelBadge } from "@/lib/pathwayLevel"
import { getModuleMeta } from "@/lib/modules"
import { useFindings, useFindingsSummary } from "@/api/findings"
import EvidenceStars from "@/components/ui/EvidenceStars"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import type { Finding, FindingSummaryItem } from "@/types/findings"

// ── Evidence level labels ────────────────────────────────────────────

function evidenceLabel(level: number | null): string {
  switch (level) {
    case 4:
      return "Definitive"
    case 3:
      return "Strong"
    case 2:
      return "Moderate"
    case 1:
      return "Preliminary"
    default:
      return "Unknown"
  }
}

function indeterminateReasonLabel(reason: string): string {
  switch (reason) {
    case "off_chip":
      return "not typed on this array"
    case "no_call":
      return "no-call in this sample"
    case "palindrome_strand_ambiguous":
      return "strand-ambiguous palindromic homozygote"
    case "discordant_haplotype":
      return "discordant haplotype markers"
    case "unresolved":
      return "alleles could not be resolved"
    default:
      return reason.replace(/_/g, " ")
  }
}

function indeterminateReasonItems(
  detail: Record<string, unknown> | null,
): Array<{ rsid: string; reason: string }> {
  const raw = detail?.indeterminate_reasons
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return []
  }
  return Object.entries(raw)
    .filter(
      (entry): entry is [string, string] =>
        typeof entry[0] === "string" && typeof entry[1] === "string",
    )
    .map(([rsid, reason]) => ({ rsid, reason }))
}

// ── Components ───────────────────────────────────────────────────────

function ModuleSummaryChip({
  item,
  selected,
  onClick,
}: {
  item: FindingSummaryItem
  selected: boolean
  onClick: () => void
}) {
  const meta = getModuleMeta(item.module)
  const Icon = meta.icon

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
        selected
          ? "border-primary bg-primary/10 text-primary"
          : "border-border bg-card text-foreground hover:bg-accent"
      }`}
    >
      <Icon className={`h-3.5 w-3.5 ${meta.color}`} />
      <span>{meta.label}</span>
      <span className="rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
        {item.count}
      </span>
      {item.max_evidence_level != null && (
        <EvidenceStars level={item.max_evidence_level} />
      )}
    </button>
  )
}

function FindingRow({ finding }: { finding: Finding }) {
  const meta = getModuleMeta(finding.module)
  const Icon = meta.icon
  const [searchParams] = useSearchParams()
  const sampleParam = searchParams.get("sample_id")
  const moduleLink = meta.route
    ? sampleParam
      ? `${meta.route}?sample_id=${sampleParam}`
      : meta.route
    : null
  const reasonItems = indeterminateReasonItems(finding.detail)

  return (
    <div className="flex items-start gap-3 rounded-lg border bg-card p-4 transition-colors hover:bg-accent/50">
      {/* Evidence level indicator */}
      <div className="flex flex-col items-center gap-1 pt-0.5">
        <div
          className={`flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold ${
            (finding.evidence_level ?? 0) >= 4
              ? "bg-primary/15 text-primary"
              : (finding.evidence_level ?? 0) >= 3
                ? "bg-teal-500/15 text-teal-700 dark:text-teal-400"
                : (finding.evidence_level ?? 0) >= 2
                  ? "bg-amber-500/15 text-amber-700 dark:text-amber-400"
                  : "bg-muted text-muted-foreground"
          }`}
        >
          {finding.evidence_level ?? "–"}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <EvidenceStars level={finding.evidence_level ?? 0} />
          <span className="text-xs text-muted-foreground">
            {evidenceLabel(finding.evidence_level)}
          </span>
          {moduleLink ? (
            <Link
              to={moduleLink}
              aria-label={`View ${meta.label} module`}
              className={`flex items-center gap-1 text-xs font-medium ${meta.color} hover:underline`}
            >
              <Icon className="h-3 w-3" />
              {meta.label}
            </Link>
          ) : (
            // No dedicated page for this module — show the label without a link
            // rather than navigate to the Dashboard (issue #544).
            <span
              className={`flex items-center gap-1 text-xs font-medium ${meta.color}`}
            >
              <Icon className="h-3 w-3" />
              {meta.label}
            </span>
          )}
        </div>

        <p className="mt-1 text-sm font-medium text-foreground leading-snug">
          {finding.finding_text}
        </p>

        <div className="mt-1.5 flex items-center gap-3 flex-wrap text-xs text-muted-foreground">
          {finding.gene_symbol && (
            <Link
              to={`/genes/${finding.gene_symbol}${sampleParam ? `?sample_id=${sampleParam}` : ""}`}
              className="font-mono hover:text-primary hover:underline"
            >
              {finding.gene_symbol}
            </Link>
          )}
          {finding.rsid && (
            <Link
              to={`/variants/${finding.rsid}${sampleParam ? `?sample_id=${sampleParam}` : ""}`}
              className="font-mono hover:text-primary hover:underline"
            >
              {finding.rsid}
            </Link>
          )}
          {finding.clinvar_significance && (
            <span>ClinVar: {finding.clinvar_significance}</span>
          )}
          {finding.zygosity && <span>{finding.zygosity}</span>}
          {finding.metabolizer_status && (
            <span>{finding.metabolizer_status}</span>
          )}
          {finding.pathway_level && (
            <span
              data-testid="finding-pathway-level"
              className={cn(
                "rounded px-1.5 py-0.5 font-medium",
                pathwayLevelBadge(finding.pathway_level),
              )}
            >
              {finding.pathway_level}
            </span>
          )}
          {finding.category && (
            <span className="text-muted-foreground/70">
              {finding.category.replace(/_/g, " ")}
            </span>
          )}
        </div>

        {reasonItems.length > 0 && (
          <p
            className="mt-2 text-xs leading-relaxed text-amber-700 dark:text-amber-400"
            data-testid="finding-indeterminate-reasons"
          >
            Indeterminate{" "}
            {reasonItems.length === 1 ? "variant" : "variants"}:{" "}
            {reasonItems.map((item, index) => (
              <span key={item.rsid}>
                <span className="font-mono">{item.rsid}</span>{" "}
                {indeterminateReasonLabel(item.reason)}
                {index < reasonItems.length - 1 ? "; " : ""}
              </span>
            ))}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────

export default function FindingsExplorer() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedModule, setSelectedModule] = useState<string | null>(null)
  const [minStars, setMinStars] = useState<number | null>(null)

  const findingsQuery = useFindings(sampleId, {
    module: selectedModule ?? undefined,
    minStars: minStars ?? undefined,
  })
  const summaryQuery = useFindingsSummary(sampleId)

  // Group findings by evidence level for section headers
  const groupedFindings = useMemo(() => {
    if (!findingsQuery.data) return []
    const groups: { level: number; label: string; findings: Finding[] }[] = []
    let currentLevel: number | null = null

    for (const f of findingsQuery.data) {
      const level = f.evidence_level ?? 0
      if (level !== currentLevel) {
        currentLevel = level
        groups.push({
          level,
          label: `${evidenceLabel(level)} Evidence`,
          findings: [],
        })
      }
      groups[groups.length - 1].findings.push(f)
    }
    return groups
  }, [findingsQuery.data])

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">All Findings</h1>
        <PageEmpty icon={ClipboardList} title="Select a sample to view analysis findings." />
      </div>
    )
  }

  // Loading
  if (findingsQuery.isLoading || summaryQuery.isLoading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">All Findings</h1>
        <PageLoading message="Loading findings..." />
      </div>
    )
  }

  // Error
  if (findingsQuery.isError) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">All Findings</h1>
        <PageError
          message={findingsQuery.error?.message ?? "Failed to load findings."}
          onRetry={() => findingsQuery.refetch()}
        />
      </div>
    )
  }

  const findings = findingsQuery.data ?? []
  const summary = summaryQuery.data

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">All Findings</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {summary
              ? `${summary.total_findings} findings across ${summary.modules.length} modules`
              : `${findings.length} findings`}
          </p>
        </div>
      </div>

      {/* Module filter chips */}
      {summary && summary.modules.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Filter className="h-3.5 w-3.5" />
            <span>Filter by module</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setSelectedModule(null)}
              className={`rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
                selectedModule === null
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border bg-card text-foreground hover:bg-accent"
              }`}
            >
              All
            </button>
            {summary.modules.map((item) => (
              <ModuleSummaryChip
                key={item.module}
                item={item}
                selected={selectedModule === item.module}
                onClick={() =>
                  setSelectedModule(
                    selectedModule === item.module ? null : item.module,
                  )
                }
              />
            ))}
          </div>
        </div>
      )}

      {/* Evidence level filter */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">Min evidence:</span>
        {[null, 4, 3, 2, 1].map((level) => (
          <button
            key={level ?? "all"}
            type="button"
            onClick={() => setMinStars(level)}
            className={`rounded border px-2 py-1 text-xs font-medium transition-colors ${
              minStars === level
                ? "border-primary bg-primary/10 text-primary"
                : "border-border bg-card text-muted-foreground hover:bg-accent"
            }`}
          >
            {level == null ? (
              "All"
            ) : (
              <span className="flex items-center gap-1">
                <Star className="h-3 w-3" />
                {level}+
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Empty state */}
      {findings.length === 0 && (
        <div className="rounded-lg border bg-card p-8 text-center">
          <Star className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
          <p className="text-sm text-muted-foreground">
            {selectedModule || minStars
              ? "No findings match the current filters."
              : "No findings yet. Run annotation to generate analysis findings."}
          </p>
          {(selectedModule || minStars) && (
            <button
              type="button"
              onClick={() => {
                setSelectedModule(null)
                setMinStars(null)
              }}
              className="mt-3 text-sm text-primary hover:underline"
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {/* Findings grouped by evidence level */}
      {groupedFindings.map((group) => (
        <section key={group.level} aria-label={group.label}>
          <div className="flex items-center gap-2 mb-3">
            <h2 className="text-sm font-semibold text-foreground">
              {group.label}
            </h2>
            <EvidenceStars level={group.level} />
            <span className="text-xs text-muted-foreground">
              ({group.findings.length})
            </span>
          </div>
          <div className="space-y-2">
            {group.findings.map((finding) => (
              <FindingRow key={finding.id} finding={finding} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
