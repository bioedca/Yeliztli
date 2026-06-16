/** Gene Allergy & Immune Sensitivities module page (P3-61).
 *
 * Displays four allergy pathway cards (Atopic Conditions, Drug
 * Hypersensitivity, Food Sensitivity, Histamine Metabolism) with
 * atopic triad summary cards, HLA proxy confidence display,
 * drug hypersensitivity alerts, celiac DQ2/DQ8 combined assessment,
 * histamine combined assessment, and cross-links to PGx.
 *
 * PRD E2E flow T3-69: Dashboard -> click Allergy card -> allergy page
 * shows HLA proxy confidence display and drug hypersensitivity alerts.
 */

import { useState } from "react"
import { useSearchParams, Link } from "react-router-dom"
import {
  Flower2,
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  Shield,
  Wheat,
  Pill,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { getModuleMeta } from "@/lib/modules"
import { useAllergyPathways } from "@/api/allergy"
import type {
  CeliacCombinedItem,
  HistamineCombinedItem,
  CrossModuleItem,
} from "@/types/allergy"
import PathwayCard from "@/components/allergy/PathwayCard"
import PathwayDetailPanel from "@/components/allergy/PathwayDetailPanel"
import EvidenceStars from "@/components/ui/EvidenceStars"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"

/** Celiac DQ2/DQ8 combined assessment card. */
function CeliacCombinedCard({
  celiac,
}: {
  celiac: CeliacCombinedItem
}) {
  const isPositive = celiac.state !== "neither"
  const isBoth = celiac.state === "both"

  return (
    <div
      className={cn(
        "rounded-lg border p-5",
        isBoth
          ? "bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800"
          : isPositive
            ? "bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800"
            : "bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800",
      )}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Wheat className="h-5 w-5 text-primary shrink-0" aria-hidden="true" />
          <h3 className="font-semibold text-foreground">Celiac Disease Susceptibility</h3>
        </div>
        <EvidenceStars level={celiac.evidence_level} />
      </div>

      <div className="space-y-2 mb-3">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-bold",
              isBoth
                ? "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"
                : isPositive
                  ? "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300"
                  : "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300",
            )}
          >
            {celiac.label}
          </span>
        </div>

        {celiac.dq2_genotype && (
          <p className="text-sm text-muted-foreground">
            DQ2 proxy (rs2187668): <span className="font-mono">{celiac.dq2_genotype}</span>
          </p>
        )}
        {celiac.dq8_genotype && (
          <p className="text-sm text-muted-foreground">
            DQ8 proxy (rs7775228): <span className="font-mono">{celiac.dq8_genotype}</span>
          </p>
        )}
      </div>

      <div
        className={cn(
          "rounded-md px-3 py-2 mb-3",
          isBoth
            ? "bg-amber-100/50 dark:bg-amber-900/20"
            : isPositive
              ? "bg-blue-100/50 dark:bg-blue-900/20"
              : "bg-emerald-100/50 dark:bg-emerald-900/20",
        )}
      >
        <p className="text-sm">
          {celiac.description}
        </p>
        {celiac.state === "neither" && (
          <p className="text-xs text-muted-foreground mt-1 italic">
            Negative predictive value &gt;99% for celiac disease.
          </p>
        )}
      </div>

      {/* PubMed links */}
      {celiac.pmids.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap pt-2 border-t border-border/50">
          {celiac.pmids.map((pmid) => (
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

/** Histamine metabolism combined assessment card (visually de-emphasized per PRD). */
function HistamineCombinedCard({
  histamine,
}: {
  histamine: HistamineCombinedItem
}) {
  return (
    <div
      className={cn(
        "rounded-lg border p-5",
        histamine.de_emphasize
          ? "bg-muted/30 border-border opacity-80"
          : "bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-800",
      )}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-muted-foreground shrink-0" aria-hidden="true" />
          <h3 className="font-semibold text-foreground">Histamine Metabolism</h3>
        </div>
        <EvidenceStars level={histamine.evidence_level} />
      </div>

      <div className="space-y-2 mb-3">
        {histamine.aoc1_genotype && (
          <p className="text-sm text-muted-foreground">
            AOC1 / DAO (rs10156191): <span className="font-mono">{histamine.aoc1_genotype}</span>
            <span className={cn(
              "ml-2 text-xs",
              histamine.aoc1_category === "Elevated" ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground",
            )}>
              {histamine.aoc1_category}
            </span>
          </p>
        )}
        {histamine.hnmt_genotype && (
          <p className="text-sm text-muted-foreground">
            HNMT (rs11558538): <span className="font-mono">{histamine.hnmt_genotype}</span>
            <span className={cn(
              "ml-2 text-xs",
              histamine.hnmt_category === "Elevated" ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground",
            )}>
              {histamine.hnmt_category}
            </span>
          </p>
        )}
      </div>

      <div className="rounded-md px-3 py-2 mb-3 bg-muted/50">
        <p className="text-sm text-muted-foreground">{histamine.combined_text}</p>
      </div>

      {histamine.de_emphasize && (
        <p className="text-xs text-muted-foreground italic mb-2">
          Low evidence level — interpret with caution.
        </p>
      )}

      {histamine.pmids.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap pt-2 border-t border-border/50">
          {histamine.pmids.map((pmid) => (
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

/** Drug hypersensitivity alert card with HLA proxy confidence. */
function DrugHypersensitivityAlert({
  item,
  sampleId,
}: {
  item: CrossModuleItem
  sampleId: number
}) {
  return (
    <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50/50 dark:bg-red-950/20 p-4">
      <div className="flex items-start gap-3">
        <Shield className="h-5 w-5 text-red-600 dark:text-red-400 mt-0.5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-mono text-sm font-medium">{item.gene}</span>
            {item.rsid && (
              <span className="text-xs text-muted-foreground">({item.rsid})</span>
            )}
            <span className="inline-flex items-center gap-1 text-xs font-medium text-red-700 dark:text-red-400">
              <Pill className="h-3 w-3" aria-hidden="true" />
              Drug Alert
            </span>
            <EvidenceStars level={item.evidence_level} />
          </div>
          <p className="text-sm text-muted-foreground mb-2">{item.finding_text}</p>
          <div className="flex items-center gap-3">
            <Link
              to={`/pharmacogenomics?sample_id=${sampleId}`}
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              View in Pharmacogenomics
              <ArrowRight className="h-3 w-3" aria-hidden="true" />
            </Link>
            <span className="text-xs text-muted-foreground italic">
              Confirmatory HLA typing recommended
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

/** General cross-module finding card with navigation link. */
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
            Allergy
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

export default function AllergyView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedPathway, setSelectedPathway] = useState<{
    id: string
    name: string
  } | null>(null)

  const pathwaysQuery = useAllergyPathways(sampleId)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Gene Allergy & Immune Sensitivities</h1>
        <PageEmpty icon={Flower2} title="Select a sample to view allergy & immune sensitivity results." />
      </div>
    )
  }

  // Separate drug hypersensitivity cross-links from other cross-module findings
  const drugAlerts = pathwaysQuery.data?.cross_module.filter(
    (cm) => cm.target_module === "pharmacogenomics",
  ) ?? []
  const otherCrossModule = pathwaysQuery.data?.cross_module.filter(
    (cm) => cm.target_module !== "pharmacogenomics",
  ) ?? []

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
          <Flower2 className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Gene Allergy & Immune Sensitivities</h1>
          <p className="text-sm text-muted-foreground">
            Atopic conditions, drug hypersensitivity reactions, food sensitivities, and histamine metabolism
          </p>
        </div>
      </div>

      {/* Loading state */}
      {pathwaysQuery.isLoading && (
        <PageLoading message="Loading allergy data..." />
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
              {/* Drug hypersensitivity alerts (top priority) */}
              {drugAlerts.length > 0 && (
                <section className="mb-6" aria-label="Drug hypersensitivity alerts">
                  <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                    <Shield className="h-5 w-5 text-red-600 dark:text-red-400" aria-hidden="true" />
                    Drug Hypersensitivity Alerts
                  </h2>
                  <div className="space-y-3">
                    {drugAlerts.map((item, idx) => (
                      <DrugHypersensitivityAlert
                        key={`${item.rsid ?? item.gene}-${item.target_module}-${idx}`}
                        item={item}
                        sampleId={sampleId}
                      />
                    ))}
                  </div>
                </section>
              )}

              {/* Celiac DQ2/DQ8 combined assessment */}
              {pathwaysQuery.data.celiac_combined && (
                <section className="mb-6" aria-label="Celiac disease susceptibility">
                  <h2 className="text-lg font-semibold mb-3">Celiac Disease Susceptibility</h2>
                  <CeliacCombinedCard celiac={pathwaysQuery.data.celiac_combined} />
                </section>
              )}

              {/* Pathway cards */}
              <section aria-label="Allergy pathway results">
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

              {/* Histamine combined assessment (de-emphasized per PRD) */}
              {pathwaysQuery.data.histamine_combined && (
                <section className="mt-6" aria-label="Histamine metabolism assessment">
                  <h2 className="text-lg font-semibold mb-3">Histamine Metabolism</h2>
                  <HistamineCombinedCard histamine={pathwaysQuery.data.histamine_combined} />
                </section>
              )}

              {/* Other cross-module findings */}
              {otherCrossModule.length > 0 && (
                <section className="mt-6" aria-label="Cross-module findings">
                  <h2 className="text-lg font-semibold mb-3">Related Findings in Other Modules</h2>
                  <div className="space-y-3">
                    {otherCrossModule.map((item, idx) => (
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
            <PageEmpty icon={Flower2} title="No allergy results yet." description="Run annotation to generate pathway scores." />
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
