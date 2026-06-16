/** FH status summary card (P3-21).
 *
 * Prominent card displaying familial hypercholesterolemia status
 * (Positive/Negative) with affected genes and variant summary.
 */

import { cn } from "@/lib/utils"
import { getClinvarSignificanceTextClass } from "@/lib/clinvar-significance"
import type { FHStatusResponse } from "@/types/cardiovascular"
import EvidenceStars from "@/components/ui/EvidenceStars"
import { HeartPulse, ShieldCheck } from "lucide-react"

interface FHStatusCardProps {
  fhStatus: FHStatusResponse
}

export default function FHStatusCard({ fhStatus }: FHStatusCardProps) {
  const isPositive = fhStatus.status === "Positive"

  return (
    <div
      className={cn(
        "rounded-lg border p-5",
        isPositive
          ? "border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30"
          : "border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30",
      )}
      data-testid="fh-status-card"
    >
      <div className="flex items-start gap-4">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg shrink-0",
            isPositive
              ? "bg-red-100 dark:bg-red-900/50 text-red-600 dark:text-red-400"
              : "bg-green-100 dark:bg-green-900/50 text-green-600 dark:text-green-400",
          )}
        >
          {isPositive ? (
            <HeartPulse className="h-5 w-5" />
          ) : (
            <ShieldCheck className="h-5 w-5" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <h3 className="text-lg font-semibold text-foreground">
              Familial Hypercholesterolemia
            </h3>
            <span
              className={cn(
                "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
                isPositive
                  ? "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300"
                  : "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300",
              )}
              data-testid="fh-status-badge"
            >
              {fhStatus.status}
            </span>
          </div>

          <p className="text-sm text-muted-foreground mb-3">
            {fhStatus.summary_text}
          </p>

          {isPositive && (
            <div className="space-y-2">
              {/* Affected genes */}
              {fhStatus.affected_genes.length > 0 && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Affected genes:</span>
                  <div className="flex gap-1">
                    {fhStatus.affected_genes.map((gene) => (
                      <span
                        key={gene}
                        className="inline-flex items-center rounded-full bg-red-100 dark:bg-red-900/50 px-2 py-0.5 text-xs font-medium text-red-800 dark:text-red-300"
                      >
                        {gene}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Variant count + homozygous flag */}
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span>{fhStatus.variant_count} variant{fhStatus.variant_count !== 1 ? "s" : ""} found</span>
                {fhStatus.has_homozygous && (
                  <span className="text-red-700 dark:text-red-400 font-medium">
                    Homozygous variant present
                  </span>
                )}
                {fhStatus.highest_evidence_level > 0 && (
                  <span className="flex items-center gap-1">
                    Highest evidence: <EvidenceStars level={fhStatus.highest_evidence_level} />
                  </span>
                )}
              </div>

              {/* FH variant details */}
              {fhStatus.variants.length > 0 && (
                <div className="mt-3 pt-3 border-t border-red-200/50 dark:border-red-800/50">
                  <p className="text-xs font-medium text-muted-foreground mb-2">FH Variants</p>
                  <div className="space-y-1.5">
                    {fhStatus.variants.map((v) => (
                      <div
                        key={`${v.gene_symbol}-${v.rsid}`}
                        className="flex items-center justify-between text-xs"
                      >
                        <span className="font-mono text-foreground">
                          {v.gene_symbol} {v.rsid}
                          {v.genotype && <span className="text-muted-foreground ml-1">({v.genotype})</span>}
                        </span>
                        <span className={cn(
                          "font-medium",
                          getClinvarSignificanceTextClass(v.clinvar_significance),
                        )}>
                          {v.clinvar_significance}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
