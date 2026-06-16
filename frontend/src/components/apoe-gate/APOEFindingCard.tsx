/** APOE finding card (P3-22d).
 *
 * Displays a single APOE finding (cardiovascular risk, Alzheimer's risk,
 * or lipid/dietary context) with evidence stars, finding text, conditions,
 * and PubMed citations.
 */

import { cn } from "@/lib/utils"
import { HeartPulse, Brain, Apple } from "lucide-react"
import EvidenceStars from "@/components/ui/EvidenceStars"
import type { APOEFinding } from "@/types/apoe"

interface APOEFindingCardProps {
  finding: APOEFinding
}

const CATEGORY_CONFIG: Record<string, {
  label: string
  icon: typeof HeartPulse
  borderClass: string
  bgClass: string
  iconBgClass: string
  iconClass: string
}> = {
  cardiovascular_risk: {
    label: "Cardiovascular Risk",
    icon: HeartPulse,
    borderClass: "border-red-200 dark:border-red-800",
    bgClass: "bg-red-50 dark:bg-red-950/20",
    iconBgClass: "bg-red-100 dark:bg-red-900/50",
    iconClass: "text-red-600 dark:text-red-400",
  },
  alzheimers_risk: {
    label: "Alzheimer's Risk",
    icon: Brain,
    borderClass: "border-purple-200 dark:border-purple-800",
    bgClass: "bg-purple-50 dark:bg-purple-950/20",
    iconBgClass: "bg-purple-100 dark:bg-purple-900/50",
    iconClass: "text-purple-600 dark:text-purple-400",
  },
  lipid_dietary: {
    label: "Lipid & Dietary Response",
    icon: Apple,
    borderClass: "border-emerald-200 dark:border-emerald-800",
    bgClass: "bg-emerald-50 dark:bg-emerald-950/20",
    iconBgClass: "bg-emerald-100 dark:bg-emerald-900/50",
    iconClass: "text-emerald-600 dark:text-emerald-400",
  },
}

const DEFAULT_CONFIG = {
  label: "Finding",
  icon: HeartPulse,
  borderClass: "border-gray-200 dark:border-gray-800",
  bgClass: "bg-gray-50 dark:bg-gray-950/20",
  iconBgClass: "bg-gray-100 dark:bg-gray-900/50",
  iconClass: "text-gray-600 dark:text-gray-400",
}

export default function APOEFindingCard({ finding }: APOEFindingCardProps) {
  const config = CATEGORY_CONFIG[finding.category] ?? DEFAULT_CONFIG
  const Icon = config.icon
  const isNonActionable = finding.detail_json?.non_actionable === true

  return (
    <div
      className={cn("rounded-lg border p-5", config.borderClass, config.bgClass)}
      data-testid={`apoe-finding-${finding.category}`}
    >
      <div className="flex items-start gap-4">
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg shrink-0", config.iconBgClass, config.iconClass)}>
          <Icon className="h-5 w-5" />
        </div>

        <div className="flex-1 min-w-0">
          {/* Header */}
          <div className="flex items-center gap-3 mb-2">
            <h3 className="text-base font-semibold text-foreground">
              {config.label}
            </h3>
            <EvidenceStars level={finding.evidence_level} />
            {isNonActionable && (
              <span className="inline-flex items-center rounded-full bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-300 px-2 py-0.5 text-xs font-medium">
                Non-Actionable
              </span>
            )}
          </div>

          {/* Diplotype context */}
          {finding.diplotype && (
            <p className="text-xs text-muted-foreground mb-2">
              Based on {finding.diplotype.replace(/e(\d)/g, "ε$1")} genotype
            </p>
          )}

          {/* Finding text */}
          <p className="text-sm text-foreground leading-relaxed mb-3">
            {finding.finding_text}
          </p>

          {/* Phenotype */}
          {finding.phenotype && (
            <div className="mb-3">
              <span className="text-xs text-muted-foreground">Phenotype: </span>
              <span className="text-xs text-foreground font-medium">{finding.phenotype}</span>
            </div>
          )}

          {/* Conditions */}
          {finding.conditions && (
            <div className="mb-3">
              <span className="text-xs text-muted-foreground">Conditions: </span>
              <span className="text-xs text-foreground">{finding.conditions}</span>
            </div>
          )}

          {/* Risk level from detail_json */}
          {typeof finding.detail_json?.risk_level === "string" && (
            <div className="mb-3">
              <span className="text-xs text-muted-foreground">Risk level: </span>
              <span className={cn(
                "text-xs font-medium text-foreground",
                String(finding.detail_json.risk_level).includes("elevated") && "text-amber-700 dark:text-amber-400",
                String(finding.detail_json.risk_level).includes("markedly") && "text-red-700 dark:text-red-400",
                String(finding.detail_json.risk_level).includes("enhanced") && "text-amber-700 dark:text-amber-400",
                String(finding.detail_json.risk_level).includes("reference") && "text-green-700 dark:text-green-400",
                String(finding.detail_json.risk_level).includes("typical") && "text-green-700 dark:text-green-400",
                String(finding.detail_json.risk_level).includes("reduced") && "text-blue-600 dark:text-blue-400",
                String(finding.detail_json.risk_level).includes("atypical") && "text-violet-600 dark:text-violet-400",
              )}>
                {String(finding.detail_json.risk_level)}
              </span>
            </div>
          )}

          {/* PubMed citations */}
          {finding.pmid_citations.length > 0 && (
            <div className="pt-3 border-t border-current/10">
              <p className="text-xs text-muted-foreground mb-1.5">References</p>
              <div className="flex flex-wrap gap-1.5">
                {finding.pmid_citations.map((pmid) => (
                  <a
                    key={pmid}
                    href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/80 transition-colors"
                  >
                    PMID:{pmid}
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
