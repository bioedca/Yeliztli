/** Cross-module finding card with a registry-driven navigation link (#1621).
 *
 * Extracted from the near-identical local `CrossModuleCard` definitions that had
 * accumulated in the Sleep / Allergy / Skin / GeneHealth / TraitsPersonality
 * views. The target module's label and route come from the shared registry
 * (`getModuleMeta`) — the sidebar/router source of truth — not a hand-duplicated
 * local map, so labels and routes can't drift per-view (#699 / #838):
 *   - `route === null` → panel-only module → render the canonical label but NO
 *     "View in …" link.
 *
 * The `sourceLabel` is passed by the caller (the module you're viewing — e.g.
 * "Sleep", "Gene Health" — or the originating trait name for Traits) so each
 * module keeps its own domain copy while sharing the markup.
 */

import { ArrowRight } from "lucide-react"
import { Link } from "react-router-dom"

import { getModuleMeta } from "@/lib/modules"
import EvidenceStars from "@/components/ui/EvidenceStars"

/** The subset of every module's `CrossModuleItem` this card renders. */
interface CrossModuleCardItem {
  gene: string
  rsid?: string
  finding_text: string
  evidence_level: number
}

interface CrossModuleCardProps {
  item: CrossModuleCardItem
  /** Label for the source side of the chip (the current module, or a trait). */
  sourceLabel: string
  /** Registry key of the target module the finding links into. */
  targetModule: string
  sampleId: number
}

export default function CrossModuleCard({
  item,
  sourceLabel,
  targetModule,
  sampleId,
}: CrossModuleCardProps) {
  // Canonical label + route from the shared registry (matches the sidebar /
  // Command Palette), not an ad-hoc capitalize of the raw key. `route` is null
  // for panel-only modules → render the label but no navigation link.
  const { label: moduleName, route: targetRoute } = getModuleMeta(targetModule)

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-mono font-medium">{item.gene}</span>
          {item.rsid && <span className="text-muted-foreground">({item.rsid})</span>}
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            {sourceLabel}
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
