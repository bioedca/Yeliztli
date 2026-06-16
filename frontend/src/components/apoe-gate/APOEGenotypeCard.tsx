/** APOE genotype summary card (P3-22d).
 *
 * Shows the APOE diplotype determination (e.g. ε3/ε4) with underlying
 * SNP genotypes (rs429358, rs7412) and ε4/ε2 allele presence indicators.
 */

import { cn } from "@/lib/utils"
import { Dna } from "lucide-react"
import type { APOEGenotypeResponse } from "@/types/apoe"

interface APOEGenotypeCardProps {
  genotype: APOEGenotypeResponse
}

/** Format diplotype for display (e.g. "e3/e4" → "ε3/ε4"). */
function formatDiplotype(diplotype: string): string {
  return diplotype.replace(/e(\d)/g, "ε$1")
}

export default function APOEGenotypeCard({ genotype }: APOEGenotypeCardProps) {
  const isDetermined = genotype.status === "determined"

  return (
    <div
      className="rounded-lg border bg-card p-5"
      data-testid="apoe-genotype-card"
    >
      <div className="flex items-start gap-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary shrink-0">
          <Dna className="h-5 w-5" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <h3 className="text-lg font-semibold text-foreground">
              APOE Genotype
            </h3>
            {isDetermined && genotype.diplotype && (
              <span
                className="inline-flex items-center rounded-full bg-teal-100 px-3 py-0.5 text-sm font-semibold text-teal-800 dark:bg-teal-900/30 dark:text-teal-300"
                data-testid="apoe-diplotype-badge"
              >
                {formatDiplotype(genotype.diplotype)}
              </span>
            )}
          </div>

          {isDetermined ? (
            <div className="space-y-3">
              {/* SNP genotypes */}
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-md bg-muted/50 p-3">
                  <p className="text-xs text-muted-foreground mb-1">rs429358 (codon 112)</p>
                  <p className="text-sm font-mono font-medium text-foreground">
                    {genotype.rs429358_genotype || "—"}
                  </p>
                </div>
                <div className="rounded-md bg-muted/50 p-3">
                  <p className="text-xs text-muted-foreground mb-1">rs7412 (codon 158)</p>
                  <p className="text-sm font-mono font-medium text-foreground">
                    {genotype.rs7412_genotype || "—"}
                  </p>
                </div>
              </div>

              {/* Allele indicators */}
              <div className="flex items-center gap-4 text-xs">
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-medium",
                    genotype.has_e4
                      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"
                      : "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300",
                  )}
                  data-testid="apoe-e4-indicator"
                >
                  {genotype.has_e4
                    ? `ε4 present (${genotype.e4_count ?? 0} ${genotype.e4_count === 1 ? "copy" : "copies"})`
                    : "No ε4 alleles"
                  }
                </span>
                {genotype.has_e2 && (
                  <span
                    className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300 px-2.5 py-0.5 font-medium"
                    data-testid="apoe-e2-indicator"
                  >
                    ε2 present ({genotype.e2_count ?? 0} {genotype.e2_count === 1 ? "copy" : "copies"})
                  </span>
                )}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="apoe-genotype-status">
              {genotype.status === "not_run" && "APOE analysis has not been run yet."}
              {genotype.status === "missing_snps" && "One or both APOE SNPs (rs429358, rs7412) are missing from this sample."}
              {genotype.status === "no_call" && "APOE SNPs are present but have no-call genotypes."}
              {genotype.status === "ambiguous" && "APOE genotype could not be unambiguously determined."}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
