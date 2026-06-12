/** Gene Sleep module page (P3-50).
 *
 * Displays four sleep pathway cards (Caffeine & Sleep, Chronotype &
 * Circadian Rhythm, Sleep Quality, Sleep Disorders) with categorical
 * Elevated/Moderate/Standard scoring, chronotype dial, CYP1A2
 * metabolizer card, disorder risk cards, and cross-module PGx link.
 * Drill-down to individual SNPs and genotypes via slide-in panel.
 *
 * PRD E2E flow: Dashboard -> click Sleep card -> sleep page shows
 * 4 pathway cards -> chronotype dial visible -> CYP1A2 cross-link.
 */

import { useState } from "react"
import { useSearchParams, Link } from "react-router-dom"
import { Moon, ArrowRight, Coffee, ExternalLink } from "lucide-react"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { useSleepPathways } from "@/api/sleep"
import type { CrossModuleItem, MetabolizerState } from "@/types/sleep"
import PathwayCard from "@/components/sleep/PathwayCard"
import PathwayDetailPanel from "@/components/sleep/PathwayDetailPanel"
import ChronotypeDial from "@/components/sleep/ChronotypeDial"
import EvidenceStars from "@/components/ui/EvidenceStars"

const METABOLIZER_LABELS: Record<string, { label: string; color: string; description: string }> = {
  rapid: {
    label: "Rapid Metabolizer",
    color: "text-emerald-700 dark:text-emerald-400",
    description: "Fast caffeine clearance — lower sensitivity to caffeine-induced sleep disruption.",
  },
  intermediate: {
    label: "Intermediate Metabolizer",
    color: "text-blue-700 dark:text-blue-400",
    description: "Moderate caffeine clearance — some sensitivity to late-day caffeine intake.",
  },
  slow: {
    label: "Slow Metabolizer",
    color: "text-amber-700 dark:text-amber-400",
    description: "Reduced caffeine clearance — higher risk of caffeine-induced insomnia with afternoon intake.",
  },
}

export default function SleepView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedPathway, setSelectedPathway] = useState<{
    id: string
    name: string
  } | null>(null)

  const pathwaysQuery = useSleepPathways(sampleId)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Gene Sleep</h1>
        <PageEmpty icon={Moon} title="Select a sample to view sleep results." />
      </div>
    )
  }

  // Find the chronotype pathway for the dial
  const chronotypePathway = pathwaysQuery.data?.items.find(
    (p) => p.pathway_id === "chronotype_circadian",
  )

  // Find the sleep disorders pathway for the risk card
  const disordersPathway = pathwaysQuery.data?.items.find(
    (p) => p.pathway_id === "sleep_disorders",
  )

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
          <Moon className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Gene Sleep</h1>
          <p className="text-sm text-muted-foreground">
            Sleep quality, circadian rhythm, and caffeine metabolism based on your genotype
          </p>
        </div>
      </div>

      {/* Loading state */}
      {pathwaysQuery.isLoading && (
        <PageLoading message="Loading sleep data..." />
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
              {/* Chronotype dial + CYP1A2 metabolizer card */}
              <section className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6" aria-label="Sleep highlights">
                {/* Chronotype dial */}
                {chronotypePathway && (
                  <ChronotypeDial
                    level={chronotypePathway.level}
                    calledSnps={chronotypePathway.called_snps}
                  />
                )}

                {/* CYP1A2 metabolizer card */}
                {pathwaysQuery.data.metabolizer && (
                  <MetabolizerCard metabolizer={pathwaysQuery.data.metabolizer} />
                )}
              </section>

              {/* Sleep disorders risk card */}
              {disordersPathway && disordersPathway.level !== "Standard" && (
                <section className="mb-6" aria-label="Sleep disorder risk">
                  <DisorderRiskCard
                    pathway={disordersPathway}
                    onClick={() =>
                      setSelectedPathway({
                        id: disordersPathway.pathway_id,
                        name: disordersPathway.pathway_name,
                      })
                    }
                  />
                </section>
              )}

              {/* Pathway cards */}
              <section aria-label="Sleep pathway results">
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

              {/* Cross-module PGx reference */}
              {pathwaysQuery.data.cross_module.length > 0 && (
                <section className="mt-6" aria-label="Cross-module references">
                  <h2 className="text-lg font-semibold mb-3">Cross-Module References</h2>
                  <div className="space-y-3">
                    {pathwaysQuery.data.cross_module.map((item: CrossModuleItem) => (
                      <div
                        key={`${item.rsid}-${item.source_module}-${item.target_module}`}
                        className="rounded-lg border bg-card p-4"
                      >
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <div className="flex items-center gap-2 text-sm">
                            <span className="font-mono font-medium">{item.gene}</span>
                            <span className="text-muted-foreground">({item.rsid})</span>
                            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                              Sleep
                              <ArrowRight className="h-3 w-3" aria-hidden="true" />
                              Pharmacogenomics
                            </span>
                          </div>
                          <EvidenceStars level={item.evidence_level} />
                        </div>
                        <p className="text-sm text-muted-foreground mb-3">{item.finding_text}</p>
                        <Link
                          to={`/pharmacogenomics?sample_id=${sampleId}`}
                          className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
                        >
                          View in Pharmacogenomics
                          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                        </Link>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}

          {/* Empty state */}
          {pathwaysQuery.data && pathwaysQuery.data.items.length === 0 && (
            <PageEmpty
              icon={Moon}
              title="No sleep results yet."
              description="Run annotation to generate pathway scores."
            />
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

/** CYP1A2 caffeine metabolizer status card. */
function MetabolizerCard({ metabolizer }: { metabolizer: MetabolizerState }) {
  const state = metabolizer.state?.toLowerCase() ?? ""
  const config = METABOLIZER_LABELS[state] || {
    label: "Unknown",
    color: "text-muted-foreground",
    description: "CYP1A2 metabolizer state could not be determined from available genotype data.",
  }

  return (
    <div className="rounded-lg border bg-card p-5">
      <h3 className="text-sm font-semibold mb-3">Caffeine Metabolizer Status</h3>
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 shrink-0">
          <Coffee className="h-5 w-5 text-primary" aria-hidden="true" />
        </div>
        <div>
          <p className={cn("font-semibold", config.color)}>{config.label}</p>
          <p className="text-xs text-muted-foreground mt-1">{config.description}</p>
          <p className="text-xs text-muted-foreground mt-2">
            <span className="font-mono">{metabolizer.gene}</span>{" "}
            <span className="text-muted-foreground">({metabolizer.rsid})</span>
          </p>
        </div>
      </div>
    </div>
  )
}

/** Sleep disorder risk highlight card. */
function DisorderRiskCard({
  pathway,
  onClick,
}: {
  pathway: { pathway_name: string; level: string; evidence_level: number; called_snps: number; total_snps: number }
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-lg border p-4 text-left transition-all hover:shadow-md",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        pathway.level === "Elevated"
          ? "border-amber-200 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20"
          : "border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/20",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold text-foreground">{pathway.pathway_name}</h3>
          <p className="text-sm text-muted-foreground mt-1">
            {pathway.level === "Elevated"
              ? "Elevated genetic susceptibility detected — review variant details for risk factors."
              : "Moderate genetic signals detected — some sleep disorder risk variants present."}
          </p>
        </div>
        <div className="text-right shrink-0">
          <span
            className={cn(
              "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
              pathway.level === "Elevated"
                ? "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"
                : "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300",
            )}
          >
            {pathway.level}
          </span>
          <div className="mt-1">
            <EvidenceStars level={pathway.evidence_level} />
          </div>
        </div>
      </div>
    </button>
  )
}
