/** Carrier status module page (P3-38).
 *
 * Displays heterozygous P/LP carrier variants from the 7-gene panel
 * with reproductive framing. Gene cards show per-variant details.
 * BRCA1/2 dual-role cross-link banners link to Cancer module.
 *
 * PRD E2E flow: Carrier page shows het P/LP gene cards with
 * reproductive framing and BRCA1/2 cross-link to Cancer.
 */

import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Heart, ChevronDown, ChevronUp } from "lucide-react"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { useCarrierVariants, useCarrierDisclaimer } from "@/api/carrier"
import type { CarrierVariant } from "@/types/carrier"
import VariantCard from "@/components/carrier/VariantCard"
import VariantDetailPanel from "@/components/carrier/VariantDetailPanel"

export default function CarrierStatusView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedVariant, setSelectedVariant] = useState<CarrierVariant | null>(null)
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

  const variantsQuery = useCarrierVariants(sampleId)
  const disclaimerQuery = useCarrierDisclaimer()

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Carrier Status</h1>
        <PageEmpty icon={Heart} title="Select a sample to view carrier status results." />
      </div>
    )
  }

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
          <Heart className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Carrier Status</h1>
          <p className="text-sm text-muted-foreground">
            Reproductive carrier screening, plus the autosomal-dominant genes BRCA1/2
            (personal-risk context)
          </p>
        </div>
      </div>

      {/* Module disclaimer (P3-37) */}
      {disclaimerQuery.data && (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 mb-6" data-testid="carrier-disclaimer">
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
      {variantsQuery.isLoading && (
        <PageLoading message="Loading carrier status data..." />
      )}

      {/* Error state */}
      {variantsQuery.isError && !variantsQuery.isLoading && (
        <PageError
          message={variantsQuery.error instanceof Error ? variantsQuery.error.message : "An unexpected error occurred."}
          onRetry={() => { variantsQuery.refetch(); }}
        />
      )}

      {/* Main content */}
      {!variantsQuery.isLoading && !variantsQuery.isError && (
        <>
          {/* Summary bar */}
          {variantsQuery.data && variantsQuery.data.total > 0 && (
            <div className="rounded-lg border bg-card p-4 mb-6" data-testid="carrier-summary">
              <div className="flex items-center gap-6 text-sm">
                <div>
                  <span className="text-muted-foreground">Carrier variants: </span>
                  <span className="font-semibold">{variantsQuery.data.total}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Genes: </span>
                  <span className="font-semibold">
                    {variantsQuery.data.genes_with_findings.join(", ")}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Gene cards grid */}
          <section aria-label="Carrier status findings">
            <h2 className="text-lg font-semibold mb-3">Carrier Findings</h2>
            <p className="text-sm text-muted-foreground mb-4">
              Heterozygous pathogenic and likely pathogenic variants in the 7-gene carrier panel
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
                    sampleId={sampleId}
                  />
                ))}
              </div>
            ) : (
              <PageEmpty
                icon={Heart}
                title="No carrier variants identified in the 7-gene panel for this sample."
                description="This does not rule out carrier status. Genotyping arrays detect only a subset of known variants. Consult a genetic counselor for comprehensive carrier screening."
              />
            )}
          </section>

          {/* Per-gene notes section */}
          {disclaimerQuery.data && Object.keys(disclaimerQuery.data.gene_notes).length > 0 && (
            <section className="mt-8" aria-label="Gene-specific notes">
              <h2 className="text-lg font-semibold mb-3">Gene Panel Notes</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {Object.entries(disclaimerQuery.data.gene_notes).map(([gene, note]) => (
                  <div
                    key={gene}
                    className="rounded-lg border bg-card p-3"
                    data-testid={`gene-note-${gene}`}
                  >
                    <h3 className="text-sm font-semibold text-foreground mb-1">{gene}</h3>
                    <p className="text-xs text-muted-foreground">{note}</p>
                  </div>
                ))}
              </div>
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
            geneNote={disclaimerQuery.data?.gene_notes[selectedVariant.gene_symbol]}
            onClose={() => setSelectedVariant(null)}
          />
        </>
      )}
    </div>
  )
}
