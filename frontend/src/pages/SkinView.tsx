/** Gene Skin module page (P3-56).
 *
 * Displays four skin pathway cards (Pigmentation & UV Response,
 * Skin Barrier & Inflammation, Oxidative Stress & Aging,
 * Skin Micronutrients) with MC1R allele summary, skin condition
 * cards, cross-links to Cancer and Nutrigenomics, and FLG
 * insufficient data caveats.
 *
 * PRD E2E flow T3-67: Dashboard -> click Skin card -> skin page shows
 * MC1R allele summary and skin condition cards.
 */

import { useState } from "react"
import { useSearchParams, Link } from "react-router-dom"
import {
  Sun,
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  Dna,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { getModuleMeta } from "@/lib/modules"
import { useSkinPathways } from "@/api/skin"
import type { CrossModuleItem, InsufficientDataItem, MC1RAggregateItem } from "@/types/skin"
import PathwayCard from "@/components/skin/PathwayCard"
import PathwayDetailPanel from "@/components/skin/PathwayDetailPanel"
import EvidenceStars from "@/components/ui/EvidenceStars"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"

/** MC1R allele summary card — displays multi-allele aggregate result. */
function MC1RSummaryCard({
  aggregate,
}: {
  aggregate: MC1RAggregateItem
}) {
  const isHighRisk = aggregate.r_allele_count >= 2
  const isModerate = aggregate.r_allele_count === 1

  return (
    <div
      className={cn(
        "rounded-lg border p-5",
        isHighRisk
          ? "bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800"
          : isModerate
            ? "bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800"
            : "bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800",
      )}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Dna className="h-5 w-5 text-primary shrink-0" aria-hidden="true" />
          <h3 className="font-semibold text-foreground">MC1R Allele Summary</h3>
        </div>
        <EvidenceStars level={aggregate.evidence_level} />
      </div>

      <div className="space-y-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">R alleles detected:</span>
          <span
            className={cn(
              "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-bold",
              isHighRisk
                ? "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"
                : isModerate
                  ? "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300"
                  : "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300",
            )}
          >
            {aggregate.r_allele_count}
          </span>
          <span className="text-xs text-muted-foreground">
            of {aggregate.total_mc1r_called} MC1R variants called
          </span>
        </div>

        {aggregate.r_allele_rsids.length > 0 && (
          <p className="text-sm text-muted-foreground">
            R alleles:{" "}
            <span className="font-mono">{aggregate.r_allele_rsids.join(", ")}</span>
          </p>
        )}
      </div>

      <div
        className={cn(
          "rounded-md px-3 py-2 mb-3",
          isHighRisk
            ? "bg-amber-100/50 dark:bg-amber-900/20"
            : isModerate
              ? "bg-blue-100/50 dark:bg-blue-900/20"
              : "bg-emerald-100/50 dark:bg-emerald-900/20",
        )}
      >
        <p className="text-sm font-medium">
          {aggregate.risk_label}
        </p>
        <p className="text-sm text-muted-foreground mt-1">
          {aggregate.risk_description}
        </p>
      </div>

      {/* PubMed links */}
      {aggregate.pmids.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap pt-2 border-t border-border/50">
          {aggregate.pmids.map((pmid) => (
            <a
              key={pmid}
              href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              aria-label={`PubMed article ${pmid}`}
            >
              PMID:{pmid}
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

/** Insufficient data caveat card (e.g. FLG 2282del4). */
function InsufficientDataCard({ item }: { item: InsufficientDataItem }) {
  return (
    <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" aria-hidden="true" />
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-mono text-sm font-medium">{item.gene}</span>
            {item.rsid && (
              <span className="text-xs text-muted-foreground">({item.rsid})</span>
            )}
            <EvidenceStars level={item.evidence_level} />
          </div>
          <p className="text-sm text-muted-foreground">{item.finding_text}</p>
          {item.pmids.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap mt-2">
              {item.pmids.map((pmid) => (
                <a
                  key={pmid}
                  href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  aria-label={`PubMed article ${pmid}`}
                >
                  PMID:{pmid}
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** Cross-module finding card with navigation link. */
function CrossModuleCard({
  item,
  sampleId,
}: {
  item: CrossModuleItem
  sampleId: number
}) {
  // Route from the shared registry (sidebar/router source of truth); null →
  // non-navigable for panel-only modules (#838).
  const targetRoute = getModuleMeta(item.target_module).route

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-mono font-medium">{item.gene}</span>
          {item.rsid && (
            <span className="text-muted-foreground">({item.rsid})</span>
          )}
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            Skin
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
            {getModuleMeta(item.target_module).label}
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
          View in {getModuleMeta(item.target_module).label}
          <ArrowRight className="h-3 w-3" aria-hidden="true" />
        </Link>
      )}
    </div>
  )
}

export default function SkinView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedPathway, setSelectedPathway] = useState<{
    id: string
    name: string
  } | null>(null)

  const pathwaysQuery = useSkinPathways(sampleId)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Gene Skin</h1>
        <PageEmpty icon={Sun} title="Select a sample to view skin results." />
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
          <Sun className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Gene Skin</h1>
          <p className="text-sm text-muted-foreground">
            Skin health traits including pigmentation, UV response, barrier function, and micronutrients
          </p>
        </div>
      </div>

      {/* Loading state */}
      {pathwaysQuery.isLoading && (
        <PageLoading message="Loading skin data..." />
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
              {/* MC1R allele summary (highlighted above pathway cards) */}
              {pathwaysQuery.data.mc1r_aggregate && (
                <section className="mb-6" aria-label="MC1R allele summary">
                  <h2 className="text-lg font-semibold mb-3">MC1R Allele Summary</h2>
                  <MC1RSummaryCard
                    aggregate={pathwaysQuery.data.mc1r_aggregate}
                  />
                </section>
              )}

              {/* Pathway cards */}
              <section aria-label="Skin pathway results">
                <h2 className="text-lg font-semibold mb-3">Pathway Results</h2>
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

              {/* Insufficient data caveats */}
              {pathwaysQuery.data.insufficient_data.length > 0 && (
                <section className="mt-6" aria-label="Insufficient data caveats">
                  <h2 className="text-lg font-semibold mb-3">Data Caveats</h2>
                  <div className="space-y-3">
                    {pathwaysQuery.data.insufficient_data.map((item) => (
                      <InsufficientDataCard key={`${item.gene}-${item.rsid}`} item={item} />
                    ))}
                  </div>
                </section>
              )}

              {/* Cross-module findings */}
              {pathwaysQuery.data.cross_module.length > 0 && (
                <section className="mt-6" aria-label="Cross-module findings">
                  <h2 className="text-lg font-semibold mb-3">Related Findings in Other Modules</h2>
                  <div className="space-y-3">
                    {pathwaysQuery.data.cross_module.map((item) => (
                      <CrossModuleCard
                        key={`${item.rsid}-${item.source_module}-${item.target_module}`}
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
            <PageEmpty icon={Sun} title="No skin results yet." description="Run annotation to generate pathway scores." />
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
