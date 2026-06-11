/** Cancer predisposition module page (P3-18).
 *
 * Two-tier layout:
 * - Primary tier: Monogenic pathogenic variant cards (ClinVar P/LP from 28-gene panel)
 * - Secondary tier: PRS gauge charts (breast, prostate, colorectal, melanoma)
 *   with permanent "Research Use Only" badge and per-score ancestry mismatch warnings
 *
 * BRCA1/2 cross-link: persistent info banner linking to Carrier Status page.
 * Module-specific disclaimer (P3-17) shown at the top.
 *
 * PRD E2E flow T3-21: Cancer page shows monogenic variants in primary tier,
 * PRS in secondary tier with "Research Use Only" badge.
 */

import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { ShieldAlert, ChevronDown, ChevronUp } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { useCancerVariants, useCancerPRS, useCancerDisclaimer } from "@/api/cancer"
import type { CancerVariant } from "@/types/cancer"
import VariantCard from "@/components/cancer/VariantCard"
import PRSGaugeCard from "@/components/cancer/PRSGaugeCard"
import VariantDetailPanel from "@/components/cancer/VariantDetailPanel"
import AbsoluteRiskOverlay from "@/components/cancer/AbsoluteRiskOverlay"
import TraitArchitectureCard from "@/components/ui/TraitArchitectureCard"
import ClinicalConfirmationGate from "@/components/ui/ClinicalConfirmationGate"

export default function CancerView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedVariant, setSelectedVariant] = useState<CancerVariant | null>(null)
  const [disclaimerExpanded, setDisclaimerExpanded] = useState(false)

  // Close detail panel on Escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && selectedVariant) {
        setSelectedVariant(null)
      }
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [selectedVariant])

  const variantsQuery = useCancerVariants(sampleId)
  const prsQuery = useCancerPRS(sampleId)
  const disclaimerQuery = useCancerDisclaimer()

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Cancer Predisposition</h1>
        <PageEmpty icon={ShieldAlert} title="Select a sample to view cancer predisposition results." />
      </div>
    )
  }

  const isLoading = variantsQuery.isLoading || prsQuery.isLoading
  const hasError = variantsQuery.isError || prsQuery.isError

  return (
    <div className="p-6">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <ShieldAlert className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Cancer Predisposition</h1>
          <p className="text-sm text-muted-foreground">
            Monogenic cancer gene panel and polygenic risk scores
          </p>
        </div>
      </div>

      {/* Module disclaimer (P3-17) */}
      {disclaimerQuery.data && (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 mb-6" data-testid="cancer-disclaimer">
          <button
            type="button"
            className="flex items-center justify-between w-full text-left"
            onClick={() => setDisclaimerExpanded(!disclaimerExpanded)}
            aria-expanded={disclaimerExpanded}
          >
            <h2 className="text-sm font-semibold text-amber-800 dark:text-amber-300">
              {disclaimerQuery.data.title}
            </h2>
            {disclaimerExpanded ? (
              <ChevronUp className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0" />
            ) : (
              <ChevronDown className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0" />
            )}
          </button>
          {disclaimerExpanded && (
            <p className="text-sm text-amber-700 dark:text-amber-400 mt-3 whitespace-pre-line">
              {disclaimerQuery.data.text}
            </p>
          )}
        </div>
      )}

      {/* Loading state */}
      {isLoading && <PageLoading message="Loading cancer data..." />}

      {/* Error state */}
      {hasError && !isLoading && (
        <PageError
          message={
            variantsQuery.error instanceof Error
              ? variantsQuery.error.message
              : prsQuery.error instanceof Error
                ? prsQuery.error.message
                : "An unexpected error occurred."
          }
          onRetry={() => {
            variantsQuery.refetch()
            prsQuery.refetch()
          }}
        />
      )}

      {/* Main content */}
      {!isLoading && !hasError && (
        <>
          {/* ── Primary Tier: Monogenic Variants ── */}
          <section aria-label="Monogenic cancer predisposition variants" className="mb-8">
            <h2 className="text-lg font-semibold mb-3">Monogenic Findings</h2>
            <p className="text-sm text-muted-foreground mb-4">
              Pathogenic and likely pathogenic variants in the 28-gene cancer predisposition panel
            </p>

            {variantsQuery.data && variantsQuery.data.items.length > 0 ? (
              <>
                <ClinicalConfirmationGate />
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                  {variantsQuery.data.items.map((variant) => (
                    <VariantCard
                      key={`${variant.gene_symbol}-${variant.rsid}`}
                      variant={variant}
                      onClick={() =>
                        setSelectedVariant(
                          selectedVariant?.rsid === variant.rsid ? null : variant,
                        )
                      }
                      selected={selectedVariant?.rsid === variant.rsid}
                      sampleId={sampleId}
                    />
                  ))}
                </div>
              </>
            ) : (
              <PageEmpty
                icon={ShieldAlert}
                title="No pathogenic or likely pathogenic variants found in the cancer gene panel."
                description="This does not eliminate cancer risk. Consult a genetic counselor for comprehensive assessment."
              />
            )}
          </section>

          {/* ── Secondary Tier: PRS Results ── */}
          {prsQuery.data && prsQuery.data.items.length > 0 && (
            <section aria-label="Cancer polygenic risk scores" data-testid="prs-tier">
              <div className="flex items-center gap-3 mb-3">
                <h2 className="text-lg font-semibold">Polygenic Risk Scores</h2>
                <span
                  className="inline-flex items-center rounded-full bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-300 px-2.5 py-0.5 text-xs font-medium"
                  data-testid="prs-research-badge"
                >
                  Research Use Only
                </span>
              </div>
              <p className="text-sm text-muted-foreground mb-4">
                Population percentile estimates derived from published GWAS with bootstrap confidence intervals
              </p>

              {prsQuery.data.insufficient_traits.length > 0 && (
                <p className="text-xs text-amber-700 dark:text-amber-400 mb-3">
                  Insufficient SNP coverage for: {prsQuery.data.insufficient_traits.join(", ")}
                </p>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {prsQuery.data.items.map((prs) => (
                  <PRSGaugeCard key={prs.trait} prs={prs} />
                ))}
              </div>
              <TraitArchitectureCard />
            </section>
          )}

          {/* Opt-in breast absolute-risk overlay (SW-B8) */}
          <AbsoluteRiskOverlay sampleId={sampleId} />

          {/* Empty state for PRS */}
          {prsQuery.data && prsQuery.data.items.length === 0 && (
            <section aria-label="Cancer polygenic risk scores">
              <h2 className="text-lg font-semibold mb-3">Polygenic Risk Scores</h2>
              <PageEmpty
                icon={ShieldAlert}
                title="No PRS results yet."
                description="Run annotation to compute polygenic risk scores."
              />
            </section>
          )}
        </>
      )}

      {/* Variant detail slide-in panel */}
      {selectedVariant && sampleId && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-30 bg-black/20"
            onClick={() => setSelectedVariant(null)}
            aria-hidden="true"
          />
          <VariantDetailPanel
            variant={selectedVariant}
            sampleId={sampleId}
            onClose={() => setSelectedVariant(null)}
          />
        </>
      )}
    </div>
  )
}
