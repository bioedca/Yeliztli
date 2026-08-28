/** Grid of analysis module cards for the dashboard (P1-20, wired P3-43a).
 *
 * Twelve modules: Pharmacogenomics, Nutrigenomics, Cancer, Cardiovascular,
 * APOE (gated), Carrier Status, Ancestry, Gene Fitness, Gene Sleep,
 * Gene Allergy, Traits & Personality, Gene Health. Cards show real finding
 * counts, top finding text, and evidence stars from the findings summary API.
 */

import {
  Pill,
  Apple,
  ShieldAlert,
  HeartPulse,
  Brain,
  Baby,
  Globe,
  Dumbbell,
  Moon,
  Flower2,
  Fingerprint,
  Activity,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import ModuleCard from './ModuleCard'
import { useFindingsSummary } from '@/api/findings'
import type { FindingSummaryItem } from '@/types/findings'
import { withActiveSample } from '@/lib/navigation'
import { getModuleMeta } from '@/lib/modules'

interface ModuleCardConfig {
  to: string
  moduleKey: string
  label: string
  icon: LucideIcon
  description: string
  gated?: boolean
  gateText?: string
}

const MODULE_CARDS: ModuleCardConfig[] = [
  {
    to: '/pharmacogenomics',
    moduleKey: 'pharmacogenomics',
    label: 'Pharmacogenomics',
    icon: Pill,
    description: 'Drug-gene interactions and metabolizer status based on CPIC guidelines.',
  },
  {
    to: '/nutrigenomics',
    moduleKey: 'nutrigenomics',
    label: 'Nutrigenomics',
    icon: Apple,
    description: 'Nutrient metabolism pathways including folate, vitamin D, and omega-3.',
  },
  {
    to: '/cancer',
    moduleKey: 'cancer',
    label: 'Cancer',
    icon: ShieldAlert,
    description: 'Cancer predisposition genes and polygenic risk scores.',
  },
  {
    to: '/cardiovascular',
    moduleKey: 'cardiovascular',
    label: 'Cardiovascular',
    icon: HeartPulse,
    description: 'Cardiovascular risk variants including familial hypercholesterolemia.',
  },
  {
    to: '/apoe',
    moduleKey: 'apoe',
    label: 'APOE',
    icon: Brain,
    description: 'APOE genotype and associated health considerations.',
    gated: true,
    gateText: 'Tap to learn more',
  },
  {
    to: '/carrier-status',
    moduleKey: 'carrier',
    label: 'Carrier Status',
    icon: Baby,
    description: 'Recessive carrier screening for conditions like CF, sickle cell, and Tay-Sachs.',
  },
  {
    to: '/ancestry',
    moduleKey: 'ancestry',
    label: 'Ancestry',
    icon: Globe,
    description: 'Ancestry composition, haplogroups, and population-matched frequencies.',
  },
  {
    to: '/fitness',
    moduleKey: 'fitness',
    label: getModuleMeta('fitness').label,
    icon: Dumbbell,
    description: 'Athletic and fitness traits including endurance, power, and recovery.',
  },
  {
    to: '/sleep',
    moduleKey: 'sleep',
    label: getModuleMeta('sleep').label,
    icon: Moon,
    description: 'Sleep quality, circadian rhythm, and caffeine metabolism.',
  },
  {
    to: '/allergy',
    moduleKey: 'allergy',
    label: getModuleMeta('allergy').label,
    icon: Flower2,
    description: 'Atopic conditions, drug hypersensitivity, food sensitivities, and histamine metabolism.',
  },
  {
    to: '/traits',
    moduleKey: 'traits',
    label: 'Traits & Personality',
    icon: Fingerprint,
    description: 'PRS-based trait associations with evidence-gated personality findings.',
  },
  {
    to: '/gene-health',
    moduleKey: 'gene_health',
    label: 'Gene Health',
    icon: Activity,
    description: 'Disease risk associations grouped by body system from ClinVar and GWAS data.',
  },
]

function findSummary(
  modules: FindingSummaryItem[] | undefined,
  key: string,
): FindingSummaryItem | undefined {
  return modules?.find((m) => m.module === key)
}

export default function ModuleCardsGrid({ sampleId }: { sampleId: number | null }) {
  const { data: summary } = useFindingsSummary(sampleId)

  return (
    <section aria-label="Analysis modules">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        {MODULE_CARDS.map((card) => {
          const moduleSummary = findSummary(summary?.modules, card.moduleKey)
          return (
            <ModuleCard
              key={card.to}
              to={withActiveSample(card.to, sampleId)}
              label={card.label}
              icon={card.icon}
              description={card.description}
              gated={card.gated}
              gateText={card.gateText}
              findingCount={moduleSummary?.count}
              maxEvidenceLevel={moduleSummary?.max_evidence_level}
              topFindingText={moduleSummary?.top_finding_text}
            />
          )
        })}
      </div>
    </section>
  )
}
