/** Traits & Personality module page (P3-64).
 *
 * Displays PRS gauge charts with "Research Use Only" banners, Big Five
 * radar chart (visual only, no numeric claims), pathway cards with
 * drill-down panels, cross-module links, and a module disclaimer header.
 * All findings hard-capped at evidence level 2.
 *
 * PRD E2E flow T3-70: Dashboard -> click Traits card -> traits page shows
 * PRS gauges with "Research Use Only" banners and module disclaimer.
 */

import { useState } from "react"
import { useSearchParams, Link } from "react-router-dom"
import {
  Fingerprint,
  Loader2,
  AlertTriangle,
  ArrowRight,
  FlaskConical,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { getModuleMeta } from "@/lib/modules"
import { useTraitsPathways, useTraitsPRS, useTraitsDisclaimer, useTraitsPathwayDetail } from "@/api/traits"
import type { CrossModuleItem } from "@/types/traits"
import PathwayCard from "@/components/traits/PathwayCard"
import PathwayDetailPanel from "@/components/traits/PathwayDetailPanel"
import TraitsPRSGaugeCard from "@/components/traits/TraitsPRSGaugeCard"
import TraitArchitectureCard from "@/components/ui/TraitArchitectureCard"
import BigFiveRadarChart from "@/components/traits/BigFiveRadarChart"
import EvidenceStars from "@/components/ui/EvidenceStars"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import SectionError from "@/components/ui/SectionError"

/** Cross-module finding card. */
function CrossModuleCard({
  item,
  sampleId,
}: {
  item: CrossModuleItem
  sampleId: number
}) {
  // Route from the shared registry (the sidebar/router source of truth), not a
  // hand-duplicated local map — null for panel-only modules → non-navigable (#838).
  const targetRoute = getModuleMeta(item.to_module).route
  // Canonical display name from the shared registry (matches the sidebar /
  // Command Palette), not an ad-hoc capitalize of the raw key (#699).
  const moduleName = getModuleMeta(item.to_module).label

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-mono font-medium">{item.gene}</span>
          {item.rsid && (
            <span className="text-muted-foreground">({item.rsid})</span>
          )}
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            {item.from_trait}
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

/** Inner component to load Big Five radar data conditionally. */
function BigFiveSection({ sampleId }: { sampleId: number }) {
  const detailQuery = useTraitsPathwayDetail("personality_big_five", sampleId)

  if (detailQuery.isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
      </div>
    )
  }

  if (!detailQuery.data || detailQuery.data.snp_details.length === 0) {
    return null
  }

  return (
    <section className="mb-6" aria-label="Big Five personality radar chart">
      <h2 className="text-lg font-semibold mb-3">Big Five Personality Associations</h2>
      <div className="rounded-lg border bg-card p-6">
        <BigFiveRadarChart snpDetails={detailQuery.data.snp_details} />
      </div>
    </section>
  )
}

export default function TraitsPersonalityView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedPathway, setSelectedPathway] = useState<{
    id: string
    name: string
  } | null>(null)

  const pathwaysQuery = useTraitsPathways(sampleId)
  const prsQuery = useTraitsPRS(sampleId)
  const disclaimerQuery = useTraitsDisclaimer()

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Traits & Personality</h1>
        <PageEmpty icon={Fingerprint} title="Select a sample to view traits & personality results." />
      </div>
    )
  }

  // Gate the page on the PRIMARY query (pathways) only. The PRS query is a
  // secondary "Research Use Only" feature that degrades to a localized inline
  // error/spinner in its own section, so a PRS 500 no longer blanks the
  // successfully-computed pathway results (#642).
  const isLoading = pathwaysQuery.isLoading
  const isError = pathwaysQuery.isError

  return (
    <div className="p-6">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-4">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <Fingerprint className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Traits & Personality</h1>
          <p className="text-sm text-muted-foreground">
            Evidence-gated trait associations from published GWAS research
          </p>
        </div>
      </div>

      {/* Module disclaimer header */}
      {disclaimerQuery.data && (
        <div
          className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 mb-6"
          data-testid="module-disclaimer"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold text-sm text-amber-800 dark:text-amber-300">
                  Module Disclaimer
                </span>
                <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-300 px-2 py-0.5 text-xs font-medium">
                  <FlaskConical className="h-3 w-3" aria-hidden="true" />
                  Research Use Only
                </span>
                <span className="text-xs text-muted-foreground">
                  Evidence cap: {disclaimerQuery.data.evidence_cap} stars
                </span>
              </div>
              <p className="text-sm text-amber-900 dark:text-amber-200">
                {disclaimerQuery.data.disclaimer}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <PageLoading message="Loading traits data..." />
      )}

      {/* Error state — only when the PRIMARY pathways query fails. */}
      {isError && !isLoading && (
        <PageError
          message={pathwaysQuery.error instanceof Error ? pathwaysQuery.error.message : "An unexpected error occurred."}
          onRetry={() => pathwaysQuery.refetch()}
        />
      )}

      {/* Main content */}
      {!isLoading && !isError && (
        <>
          {/* PRS gauge charts — secondary section: render its own error/spinner
              so a PRS failure or slow load never hides the pathway results
              below (#642). Hidden entirely only when it resolved with no items. */}
          {(prsQuery.isError ||
            prsQuery.isLoading ||
            (prsQuery.data && prsQuery.data.items.length > 0)) && (
            <section className="mb-6" aria-label="Polygenic risk scores">
              <h2 className="text-lg font-semibold mb-3">Polygenic Risk Scores</h2>
              {prsQuery.isError ? (
                <SectionError
                  label="polygenic risk scores"
                  message={prsQuery.error instanceof Error ? prsQuery.error.message : undefined}
                  onRetry={() => prsQuery.refetch()}
                />
              ) : prsQuery.isLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                    {prsQuery.data?.items.map((prs) => (
                      <TraitsPRSGaugeCard key={prs.trait} prs={prs} />
                    ))}
                  </div>
                  <TraitArchitectureCard />
                </>
              )}
            </section>
          )}

          {/* Big Five radar chart */}
          <BigFiveSection sampleId={sampleId} />

          {/* Pathway cards */}
          {pathwaysQuery.data && pathwaysQuery.data.items.length > 0 && (
            <>
              <section aria-label="Trait pathway results">
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

              {/* Cross-module findings */}
              {pathwaysQuery.data.cross_module.length > 0 && (
                <section className="mt-6" aria-label="Cross-module findings">
                  <h2 className="text-lg font-semibold mb-3">Related Findings in Other Modules</h2>
                  <div className="space-y-3">
                    {pathwaysQuery.data.cross_module.map((item, idx) => (
                      <CrossModuleCard
                        key={`${item.rsid ?? item.gene}-${item.to_module}-${idx}`}
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
          {pathwaysQuery.data &&
            pathwaysQuery.data.items.length === 0 &&
            prsQuery.data &&
            prsQuery.data.items.length === 0 && (
              <PageEmpty icon={Fingerprint} title="No traits results yet." description="Run annotation to generate trait associations." />
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
