/** Haplogroup assignment card with traversal path display (P3-34).
 *
 * Shows terminal haplogroup, traversal path (e.g. H -> H1 -> H1a),
 * per-node SNP match counts, overall confidence, and defining SNP
 * match fraction. Y-chromosome row hidden when sex_inferred = 'XX'.
 *
 * PRD P3-34: Ancestry UI haplogroup extension.
 */

import { cn } from "@/lib/utils"
import type { HaplogroupAssignment } from "@/types/ancestry"

interface HaplogroupCardProps {
  assignments: HaplogroupAssignment[]
}

const TREE_LABELS: Record<string, string> = {
  mt: "Mitochondrial (mtDNA)",
  Y: "Y-Chromosome",
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100)
  const color =
    pct >= 80
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
      : pct >= 50
        ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
        : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", color)}
      data-testid="haplogroup-confidence-badge"
    >
      {pct}% confidence
    </span>
  )
}

function TraversalPath({ assignment }: { assignment: HaplogroupAssignment }) {
  const { traversal_path } = assignment
  if (traversal_path.length === 0) return null

  return (
    <div className="mt-3" data-testid="haplogroup-traversal-path">
      <p className="text-xs font-medium text-muted-foreground mb-2">Traversal Path</p>
      <div className="flex flex-wrap items-center gap-1">
        {traversal_path.map((step, i) => (
          <div key={`${step.haplogroup}-${i}`} className="flex items-center gap-1">
            {i > 0 && (
              <span className="text-muted-foreground text-xs" aria-hidden="true">
                &rarr;
              </span>
            )}
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded px-2 py-1 text-xs",
                step.haplogroup === assignment.haplogroup
                  ? "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300 font-semibold"
                  : "bg-muted text-foreground",
              )}
              data-highlighted={step.haplogroup === assignment.haplogroup ? "" : undefined}
              title={`${step.snps_present}/${step.snps_total} defining SNPs matched`}
            >
              <span>{step.haplogroup}</span>
              <span className="text-muted-foreground font-mono text-[10px]">
                {step.snps_present}/{step.snps_total}
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function AssignmentRow({ assignment }: { assignment: HaplogroupAssignment }) {
  const treeLabel = TREE_LABELS[assignment.type] ?? assignment.type
  return (
    <div
      className="py-4 first:pt-0 last:pb-0"
      data-testid={`haplogroup-assignment-${assignment.type}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground mb-1">{treeLabel}</p>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-foreground" data-testid="haplogroup-name">
              {assignment.haplogroup}
            </span>
            <ConfidenceBadge confidence={assignment.confidence} />
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            {assignment.defining_snps_present} / {assignment.defining_snps_total} defining SNPs matched
          </p>
        </div>
      </div>

      <TraversalPath assignment={assignment} />

      {assignment.finding_text && (
        <p className="text-sm text-muted-foreground mt-2">{assignment.finding_text}</p>
      )}
    </div>
  )
}

export default function HaplogroupCard({ assignments }: HaplogroupCardProps) {
  if (assignments.length === 0) {
    return (
      <div
        className="rounded-lg border bg-card p-5"
        data-testid="haplogroup-card"
      >
        <h2 className="text-lg font-semibold mb-2">Haplogroup Assignment</h2>
        <p className="text-sm text-muted-foreground">
          No haplogroup assignments available. Run the annotation pipeline to generate haplogroup results.
        </p>
      </div>
    )
  }

  return (
    <div
      className="rounded-lg border bg-card p-5"
      data-testid="haplogroup-card"
    >
      <h2 className="text-lg font-semibold mb-4">Haplogroup Assignment</h2>
      <div className="divide-y">
        {assignments.map((a) => (
          <AssignmentRow key={a.type} assignment={a} />
        ))}
      </div>
    </div>
  )
}
