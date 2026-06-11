/** Per-PGS provenance + monogenic-exclusion disclosure for PRS cards (SW-B3).
 *
 * Renders two pieces shared by every polygenic-score card:
 *  1. A monogenic-exclusion note — a polygenic percentile reflects only
 *     common-variant burden and is reported independently of rare large-effect
 *     (monogenic) variants. When the sample carries a reportable monogenic
 *     finding in one of the score's genes, the note is escalated to an amber
 *     warning cross-referencing it (the monogenic result is the dominant one).
 *  2. A provenance line tracing the score to its PGS Catalog origin
 *     (accession, development method, variant count, license).
 */

import type { ReactNode } from "react"
import { AlertTriangle, Dna, ShieldCheck } from "lucide-react"

/** Provenance + monogenic-exclusion fields shared by all PRS finding types. */
export interface PRSProvenanceFields {
  pgs_id: string | null
  pgs_license: string | null
  development_method: string | null
  genome_build: string | null
  variants_number: number | null
  source_url: string | null
  monogenic_genes: string[]
  monogenic_carrier_genes: string[]
  monogenic_note: string | null
}

/** Compose the human-readable provenance line, or null when no fields are set. */
function provenanceParts(p: PRSProvenanceFields): ReactNode[] | null {
  const parts: ReactNode[] = []
  if (p.pgs_id) {
    parts.push(
      p.source_url ? (
        <a
          key="pgs"
          href={p.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="underline decoration-dotted hover:text-foreground"
        >
          {p.pgs_id}
        </a>
      ) : (
        <span key="pgs">{p.pgs_id}</span>
      ),
    )
  }
  if (p.development_method) parts.push(<span key="method">{p.development_method}</span>)
  if (p.variants_number != null)
    parts.push(<span key="nvar">{p.variants_number.toLocaleString()} variants</span>)
  if (p.pgs_license) parts.push(<span key="lic">{p.pgs_license}</span>)
  return parts.length > 0 ? parts : null
}

export default function PRSProvenance({ prs }: { prs: PRSProvenanceFields }) {
  const parts = provenanceParts(prs)
  const hasCarrier = prs.monogenic_carrier_genes.length > 0

  if (!prs.monogenic_note && !parts) return null

  return (
    <>
      {prs.monogenic_note && (
        <div
          className={
            hasCarrier
              ? "rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 p-2.5 mb-3"
              : "rounded-md bg-slate-50 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-700 p-2.5 mb-3"
          }
          data-testid="prs-monogenic-note"
        >
          <div className="flex items-start gap-2">
            {hasCarrier ? (
              <AlertTriangle
                className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0"
                aria-hidden="true"
              />
            ) : (
              <ShieldCheck
                className="h-4 w-4 text-slate-500 dark:text-slate-400 mt-0.5 shrink-0"
                aria-hidden="true"
              />
            )}
            <p
              className={
                hasCarrier
                  ? "text-xs text-amber-800 dark:text-amber-300"
                  : "text-xs text-slate-600 dark:text-slate-300"
              }
            >
              {prs.monogenic_note}
            </p>
          </div>
        </div>
      )}

      {parts && (
        <div className="mt-2 pt-2 border-t border-border/50" data-testid="prs-provenance">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Dna className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span>
              PGS Catalog:{" "}
              {parts.map((part, i) => (
                <span key={i}>
                  {i > 0 && " · "}
                  {part}
                </span>
              ))}
            </span>
          </p>
        </div>
      )}
    </>
  )
}
