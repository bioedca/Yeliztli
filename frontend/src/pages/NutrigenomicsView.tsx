/** Nutrigenomics module page (P3-11).
 *
 * Displays pathway consideration cards per nutrient pathway
 * (Folate Metabolism, Vitamin D, B12, Omega-3, Iron, Lactose)
 * showing Elevated/Moderate/Standard status with drill-down
 * to individual SNPs and genotypes. Literature panel with PubMed links.
 * No gauge charts — categorical display only.
 *
 * PRD E2E flow T3-11: Nutrigenomics page shows pathway consideration
 * cards with categorical labels and drill-down to individual SNPs.
 */

import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Apple } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import WithheldResults from "@/components/ui/WithheldResults"
import { useNutrigenomicsPathways } from "@/api/nutrigenomics"
import PathwayCard from "@/components/nutrigenomics/PathwayCard"
import PathwayDetailPanel from "@/components/nutrigenomics/PathwayDetailPanel"

export default function NutrigenomicsView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedPathway, setSelectedPathway] = useState<{
    id: string
    name: string
  } | null>(null)

  const pathwaysQuery = useNutrigenomicsPathways(sampleId)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Nutrigenomics</h1>
        <PageEmpty icon={Apple} title="Select a sample to view nutrigenomics results." />
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
          <Apple className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Nutrigenomics</h1>
          <p className="text-sm text-muted-foreground">
            Categorical pathway scoring for nutrient metabolism based on your genotype
          </p>
        </div>
      </div>

      {/* Loading state */}
      {pathwaysQuery.isLoading && <PageLoading message="Loading nutrigenomics data..." />}

      {/* Error state */}
      {pathwaysQuery.isError && !pathwaysQuery.isLoading && (
        <PageError
          message={
            pathwaysQuery.error instanceof Error
              ? pathwaysQuery.error.message
              : "An unexpected error occurred."
          }
          onRetry={() => pathwaysQuery.refetch()}
        />
      )}

      {/* Main content */}
      {!pathwaysQuery.isLoading && !pathwaysQuery.isError && (
        <>
          {/* Pathway consideration cards */}
          {pathwaysQuery.data && pathwaysQuery.data.items.length > 0 && (
            <section aria-label="Nutrient pathway results">
              <h2 className="text-lg font-semibold mb-3">Pathway Results</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {pathwaysQuery.data.items.map((pathway) => (
                  <PathwayCard
                    key={pathway.pathway_id}
                    pathway={pathway}
                    selected={selectedPathway?.id === pathway.pathway_id}
                    onClick={() =>
                      setSelectedPathway(
                        selectedPathway?.id === pathway.pathway_id
                          ? null
                          : { id: pathway.pathway_id, name: pathway.pathway_name },
                      )
                    }
                  />
                ))}
              </div>
            </section>
          )}

          {/* Empty state */}
          {pathwaysQuery.data && pathwaysQuery.data.items.length === 0 && (
            pathwaysQuery.data.withheld ? (
              <WithheldResults label="Nutrigenomics" />
            ) : (
              <PageEmpty
                icon={Apple}
                title="No nutrigenomics results yet."
                description="Run annotation to generate pathway scores."
              />
            )
          )}
        </>
      )}

      {/* Pathway detail slide-in panel */}
      {selectedPathway && sampleId && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-30 bg-black/20"
            onClick={() => setSelectedPathway(null)}
            aria-hidden="true"
          />
          <PathwayDetailPanel
            pathwayId={selectedPathway.id}
            pathwayName={selectedPathway.name}
            sampleId={sampleId}
            onClose={() => setSelectedPathway(null)}
          />
        </>
      )}
    </div>
  )
}
