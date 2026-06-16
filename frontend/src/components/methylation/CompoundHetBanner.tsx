/** MTHFR compound heterozygosity banner for Methylation (P3-53).
 *
 * Displays a prominent banner when MTHFR compound heterozygosity
 * or double-variant status is detected.
 */

import { AlertTriangle } from "lucide-react"
import type { CompoundHetInfo } from "@/types/methylation"

interface CompoundHetBannerProps {
  compoundHet: CompoundHetInfo
}

export default function CompoundHetBanner({ compoundHet }: CompoundHetBannerProps) {
  if (!compoundHet.is_compound_het && !compoundHet.is_double_homozygous) {
    return null
  }

  const isDouble = compoundHet.is_double_homozygous

  return (
    <div
      className="rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/40 p-4"
      role="alert"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
        <div>
          <p className="font-semibold text-amber-800 dark:text-amber-300">
            {isDouble ? "MTHFR Double Variant Detected" : "MTHFR Compound Heterozygote Detected"}
          </p>
          {compoundHet.finding_text && (
            <p className="text-sm text-amber-700 dark:text-amber-400 mt-1">
              {compoundHet.finding_text}
            </p>
          )}
          <div className="flex items-center gap-4 mt-2 text-xs text-amber-700 dark:text-amber-500">
            {compoundHet.c677t_genotype && (
              <span>
                C677T: <span className="font-mono font-medium">{compoundHet.c677t_genotype}</span>
              </span>
            )}
            {compoundHet.a1298c_genotype && (
              <span>
                A1298C: <span className="font-mono font-medium">{compoundHet.a1298c_genotype}</span>
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
