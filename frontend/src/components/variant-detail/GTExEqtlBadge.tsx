/** GTEx eQTL regulatory-context badge (SW-F3).
 *
 * Renders the context-only GTEx eQTL summary for a variant: which genes'
 * expression it is associated with, in how many tissues, with the strongest
 * association highlighted. An eQTL is a statistical ASSOCIATION with expression,
 * not a causal-mechanism claim — and is explicitly NOT ACMG evidence. Renders
 * nothing when there is no eQTL association (or the optional DB is absent). */

import { Activity } from "lucide-react"
import type { GTExEqtlBadge } from "@/types/variant-detail"

export default function GTExEqtlBadgeCard({ badge }: { badge: GTExEqtlBadge | null | undefined }) {
  if (!badge) return null

  return (
    <div
      className="rounded-md border border-sky-200 bg-sky-50 p-3 dark:border-sky-900 dark:bg-sky-950/30"
      data-testid="gtex-eqtl-badge"
    >
      <div className="flex items-start gap-2">
        <Activity className="mt-0.5 h-4 w-4 shrink-0 text-sky-600 dark:text-sky-400" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-sky-900 dark:text-sky-200">
            GTEx eQTL regulatory context
          </p>
          <dl className="mt-1.5 space-y-0.5 text-xs text-sky-800 dark:text-sky-300">
            {badge.top_gene_id && (
              <div className="flex justify-between gap-2">
                <dt className="text-sky-700/80 dark:text-sky-400/80">Top gene</dt>
                <dd className="font-mono">{badge.top_gene_id}</dd>
              </div>
            )}
            {badge.top_tissue && (
              <div className="flex justify-between gap-2">
                <dt className="text-sky-700/80 dark:text-sky-400/80">Top tissue</dt>
                <dd>{badge.top_tissue.replace(/_/g, " ")}</dd>
              </div>
            )}
            {badge.top_pval_nominal != null && (
              <div className="flex justify-between gap-2">
                <dt className="text-sky-700/80 dark:text-sky-400/80">p-value</dt>
                <dd className="font-mono">{badge.top_pval_nominal.toExponential(1)}</dd>
              </div>
            )}
            <div className="flex justify-between gap-2">
              <dt className="text-sky-700/80 dark:text-sky-400/80">Associations</dt>
              <dd>
                {badge.n_associations} across {badge.tissues.length}{" "}
                {badge.tissues.length === 1 ? "tissue" : "tissues"}
              </dd>
            </div>
          </dl>
          <p className="mt-2 text-[10px] italic leading-snug text-sky-700/80 dark:text-sky-400/80">
            Regulatory association, not mechanism — the causal variant is often a correlated LD
            neighbor. Context only; not ACMG evidence (no PP3/PS3 uplift).
          </p>
        </div>
      </div>
    </div>
  )
}
