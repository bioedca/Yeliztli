/** Gene Health module page (P3-66).
 *
 * Displays disease risk cards grouped by body system (Neurological,
 * Metabolic, Autoimmune, Sensory) with pathway drill-down panels,
 * cross-module links to APOE/Allergy/Methylation/Nutrigenomics/Traits,
 * and a module disclaimer.
 *
 * PRD: 17 disease conditions from existing ClinVar + GWAS data,
 * grouped by system, with cross-links to APOE/Allergy.
 */

import { useState } from "react"
import { useSearchParams, Link } from "react-router-dom"
import {
  Activity,
  AlertTriangle,
  ArrowRight,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { getModuleMeta } from "@/lib/modules"
import { useGeneHealthPathways } from "@/api/gene-health"
import type { CrossModuleItem } from "@/types/gene-health"
import PathwayCard from "@/components/gene-health/PathwayCard"
import PathwayDetailPanel from "@/components/gene-health/PathwayDetailPanel"
import EvidenceStars from "@/components/ui/EvidenceStars"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"

/** Cross-module finding card with navigation link. */
function CrossModuleCard({
  item,
  sampleId,
}: {
  item: CrossModuleItem
  sampleId: number
}) {
  // Route from the shared registry (the sidebar/router source of truth), not a
  // hand-duplicated local map — null for panel-only modules → non-navigable (#838).
  const targetRoute = getModuleMeta(item.target_module).route
  // Canonical display name from the shared registry (matches the sidebar /
  // Command Palette), not an ad-hoc capitalize of the raw key (#699).
  const moduleName = getModuleMeta(item.target_module).label

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-mono font-medium">{item.gene}</span>
          {item.rsid && (
            <span className="text-muted-foreground">({item.rsid})</span>
          )}
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            Gene Health
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
            {moduleName}
          </span>
        </div>
        <EvidenceStars level={item.evidence_level} />
      </div>
      <p className="text-sm text-muted-foreground mb-2">{item.finding_text}</p>
      {targetRoute && (
        <Link
          to={`${targetRoute}?sample_id=${sampleId}`}
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
        >
          View in {moduleName}
          <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      )}
    </div>
  )
}

export default function GeneHealthView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedPathway, setSelectedPathway] = useState<{
    id: string
    name: string
  } | null>(null)

  const pathwaysQuery = useGeneHealthPathways(sampleId)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Gene Health</h1>
        <PageEmpty icon={Activity} title="Select a sample to view gene health results." />
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
          <Activity className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Gene Health</h1>
          <p className="text-sm text-muted-foreground">
            Disease risk associations grouped by body system — neurological, metabolic, autoimmune, and sensory
          </p>
        </div>
      </div>

      {/* Module disclaimer */}
      {pathwaysQuery.data?.module_disclaimer && (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20 p-4 mb-6">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
            <p className="text-sm text-muted-foreground">
              {pathwaysQuery.data.module_disclaimer}
            </p>
          </div>
        </div>
      )}

      {/* Loading state */}
      {pathwaysQuery.isLoading && (
        <PageLoading message="Loading gene health data..." />
      )}

      {/* Error state */}
      {pathwaysQuery.isError && !pathwaysQuery.isLoading && (
        <PageError
          message={pathwaysQuery.error instanceof Error ? pathwaysQuery.error.message : "An unexpected error occurred."}
          onRetry={() => { pathwaysQuery.refetch(); }}
        />
      )}

      {/* Main content */}
      {!pathwaysQuery.isLoading && !pathwaysQuery.isError && (
        <>
          {pathwaysQuery.data && pathwaysQuery.data.items.length > 0 && (
            <>
              {/* Pathway cards grouped by system */}
              <section aria-label="Disease system pathway results">
                <h2 className="text-lg font-semibold mb-3">Body System Results</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
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

              {/* Cross-module findings */}
              {pathwaysQuery.data.cross_module.length > 0 && (
                <section className="mt-6" aria-label="Cross-module findings">
                  <h2 className="text-lg font-semibold mb-3">Related Findings in Other Modules</h2>
                  <div className="space-y-3">
                    {pathwaysQuery.data.cross_module.map((item, idx) => (
                      <CrossModuleCard
                        key={`${item.rsid ?? item.gene}-${item.source_module}-${item.target_module}-${idx}`}
                        item={item}
                        sampleId={sampleId}
                      />
                    ))}
                  </div>
                </section>
              )}
            </>
          )}

          {/* Empty state */}
          {pathwaysQuery.data && pathwaysQuery.data.items.length === 0 && (
            <PageEmpty icon={Activity} title="No gene health results yet." description="Run annotation to generate disease risk assessments." />
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
