/** Cardiovascular module page (P3-21).
 *
 * Single-tier layout:
 * - FH status card (prominent, from fh-status API)
 * - Monogenic variant cards (ClinVar P/LP from 16-gene panel)
 *   grouped by cardiovascular category (FH, Lipid, Channelopathy, Cardiomyopathy)
 *
 * Module-specific disclaimer shown at the top.
 *
 * PRD P3-21: Cardiovascular UI — FH status, cardiomyopathy/channelopathy variant list.
 */

import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { HeartPulse, ChevronDown, ChevronUp, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import SectionError from "@/components/ui/SectionError"
import {
  useCardiovascularVariants,
  useFHStatus,
  useCardiovascularDisclaimer,
} from "@/api/cardiovascular"
import type { CardiovascularVariant } from "@/types/cardiovascular"
import VariantCard from "@/components/cardiovascular/VariantCard"
import FHStatusCard from "@/components/cardiovascular/FHStatusCard"
import VariantDetailPanel from "@/components/cardiovascular/VariantDetailPanel"

export default function CardiovascularView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedVariant, setSelectedVariant] = useState<CardiovascularVariant | null>(null)
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

  const variantsQuery = useCardiovascularVariants(sampleId)
  const fhStatusQuery = useFHStatus(sampleId)
  const disclaimerQuery = useCardiovascularDisclaimer()

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Cardiovascular</h1>
        <PageEmpty icon={HeartPulse} title="Select a sample to view cardiovascular results." />
      </div>
    )
  }

  // Gate the page on the PRIMARY query (the 16-gene monogenic variants) only.
  // The FH-status card is a secondary section that degrades to a localized
  // inline error/spinner, so an FH-status 500 no longer hides the
  // successfully-computed monogenic variant results (#642).
  const isLoading = variantsQuery.isLoading
  const hasError = variantsQuery.isError

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
          <HeartPulse className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Cardiovascular</h1>
          <p className="text-sm text-muted-foreground">
            Monogenic cardiac variants and familial hypercholesterolemia status
          </p>
        </div>
      </div>

      {/* Module disclaimer */}
      {disclaimerQuery.data && (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 mb-6" data-testid="cardiovascular-disclaimer">
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
      {isLoading && <PageLoading message="Loading cardiovascular data..." />}

      {/* Error state — only when the PRIMARY variants query fails. */}
      {hasError && !isLoading && (
        <PageError
          message={
            variantsQuery.error instanceof Error
              ? variantsQuery.error.message
              : "An unexpected error occurred."
          }
          onRetry={() => variantsQuery.refetch()}
        />
      )}

      {/* Main content */}
      {!isLoading && !hasError && (
        <>
          {/* ── FH Status Card — secondary section: render its own
              error/spinner so an FH-status failure or slow load never hides
              the monogenic variant results below (#642). Hidden entirely only
              when it resolved with no data. ── */}
          {(fhStatusQuery.isError || fhStatusQuery.isLoading || fhStatusQuery.data) && (
            <section aria-label="Familial hypercholesterolemia status" className="mb-8">
              {fhStatusQuery.isError ? (
                <SectionError
                  label="familial hypercholesterolemia status"
                  message={fhStatusQuery.error instanceof Error ? fhStatusQuery.error.message : undefined}
                  onRetry={() => fhStatusQuery.refetch()}
                />
              ) : fhStatusQuery.isLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
                </div>
              ) : (
                fhStatusQuery.data && <FHStatusCard fhStatus={fhStatusQuery.data} />
              )}
            </section>
          )}

          {/* ── Monogenic Variants ── */}
          <section aria-label="Cardiovascular monogenic variants">
            <h2 className="text-lg font-semibold mb-3">Monogenic Findings</h2>
            <p className="text-sm text-muted-foreground mb-4">
              Pathogenic and likely pathogenic variants in the 16-gene cardiovascular panel
            </p>

            {variantsQuery.data && variantsQuery.data.items.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {variantsQuery.data.items.map((variant) => (
                  <VariantCard
                    key={`${variant.gene_symbol}-${variant.rsid}`}
                    variant={variant}
                    onClick={() =>
                      setSelectedVariant(
                        selectedVariant?.rsid === variant.rsid &&
                          selectedVariant?.gene_symbol === variant.gene_symbol
                          ? null
                          : variant,
                      )
                    }
                    selected={
                      selectedVariant?.rsid === variant.rsid &&
                      selectedVariant?.gene_symbol === variant.gene_symbol
                    }
                  />
                ))}
              </div>
            ) : (
              <PageEmpty
                icon={HeartPulse}
                title="No pathogenic or likely pathogenic variants found in the cardiovascular gene panel."
                description="This does not eliminate cardiovascular risk. Consult a healthcare provider for comprehensive assessment."
              />
            )}
          </section>
        </>
      )}

      {/* Variant detail slide-in panel */}
      {selectedVariant && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-30 bg-black/20"
            onClick={() => setSelectedVariant(null)}
            aria-hidden="true"
          />
          <VariantDetailPanel
            variant={selectedVariant}
            onClose={() => setSelectedVariant(null)}
          />
        </>
      )}
    </div>
  )
}
