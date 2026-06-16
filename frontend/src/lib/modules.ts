/**
 * Single source of truth mapping a backend `module` string → its display
 * metadata (label, icon, route, color). Both the All Findings page
 * (`FindingsExplorer`) and any other view that renders a finding's module use
 * this registry, so route/label/icon cannot drift between surfaces (#544/#620).
 *
 * Every module that persists into the unified `findings` table must have an entry
 * here. That invariant is locked by a cross-stack drift-guard:
 *   - the backend enumerates its findings-producing module set into the fixture
 *     `tests/fixtures/findings_modules.json` (test_findings_module_registry.py);
 *   - `src/test/modules.test.ts` asserts this registry covers every fixture key.
 * So a new analysis module that writes findings without a registry entry fails CI
 * (the regression class #544 hit three review passes to fix by hand).
 *
 * Page-backed modules carry their real route (verified against the router in
 * App.tsx and the sidebar nav); panel-only risk / gated-disclosure modules with
 * no dedicated page carry `route: null`, which renders a non-navigable label
 * instead of a link so a finding can never silently send the user to the
 * Dashboard. Acronym labels (FH, eBMD, AMD, LHON, ROH, MT-RNR1, APOL1) are
 * spelled correctly rather than auto-title-cased.
 */

import {
  Activity,
  Apple,
  Baby,
  Bone,
  Brain,
  ClipboardList,
  Dna,
  Droplet,
  Dumbbell,
  Eye,
  Fingerprint,
  FlaskConical,
  Flower2,
  Globe,
  Heart,
  HeartPulse,
  Moon,
  Pill,
  SearchCheck,
  ShieldAlert,
  Sun,
  Users,
  type LucideIcon,
} from "lucide-react"

export interface ModuleMeta {
  label: string
  icon: LucideIcon
  /**
   * The module's dedicated page route, or null when the module has no page of
   * its own (panel-only risk modules, gated disclosures, or any module not yet
   * mapped). A null route renders a non-navigable label instead of a link.
   */
  route: string | null
  color: string
}

export const MODULE_META: Record<string, ModuleMeta> = {
  pharmacogenomics: {
    label: "Pharmacogenomics",
    icon: Pill,
    route: "/pharmacogenomics",
    color: "text-violet-600 dark:text-violet-400",
  },
  nutrigenomics: {
    label: "Nutrigenomics",
    icon: Apple,
    route: "/nutrigenomics",
    color: "text-green-700 dark:text-green-400",
  },
  cancer: {
    label: "Cancer",
    icon: ShieldAlert,
    route: "/cancer",
    color: "text-red-700 dark:text-red-400",
  },
  cardiovascular: {
    label: "Cardiovascular",
    icon: HeartPulse,
    route: "/cardiovascular",
    color: "text-rose-600 dark:text-rose-400",
  },
  metabolic: {
    label: "Metabolic",
    icon: Droplet,
    route: "/metabolic",
    color: "text-cyan-600 dark:text-cyan-400",
  },
  fh: {
    label: "FH",
    icon: Heart,
    route: "/fh",
    color: "text-rose-600 dark:text-rose-400",
  },
  ebmd: {
    label: "eBMD",
    icon: Bone,
    route: "/ebmd",
    color: "text-stone-600 dark:text-stone-400",
  },
  apoe: {
    label: "APOE",
    icon: Brain,
    route: "/apoe",
    color: "text-amber-700 dark:text-amber-400",
  },
  carrier: {
    label: "Carrier Status",
    icon: Baby,
    route: "/carrier-status",
    color: "text-pink-600 dark:text-pink-400",
  },
  fitness: {
    label: "Fitness",
    icon: Dumbbell,
    route: "/fitness",
    color: "text-lime-600 dark:text-lime-400",
  },
  sleep: {
    label: "Sleep",
    icon: Moon,
    route: "/sleep",
    color: "text-indigo-600 dark:text-indigo-400",
  },
  methylation: {
    label: "Methylation",
    icon: FlaskConical,
    route: "/methylation",
    color: "text-teal-600 dark:text-teal-400",
  },
  skin: {
    label: "Skin",
    icon: Sun,
    route: "/skin",
    color: "text-orange-700 dark:text-orange-400",
  },
  allergy: {
    label: "Allergy",
    icon: Flower2,
    route: "/allergy",
    color: "text-fuchsia-600 dark:text-fuchsia-400",
  },
  traits: {
    label: "Traits",
    icon: Fingerprint,
    route: "/traits",
    color: "text-purple-600 dark:text-purple-400",
  },
  gene_health: {
    label: "Gene Health",
    icon: Activity,
    route: "/gene-health",
    color: "text-emerald-700 dark:text-emerald-400",
  },
  ancestry: {
    label: "Ancestry",
    icon: Globe,
    route: "/ancestry",
    color: "text-blue-600 dark:text-blue-400",
  },
  rare_variants: {
    label: "Rare Variants",
    icon: SearchCheck,
    route: "/rare-variants",
    color: "text-orange-700 dark:text-orange-400",
  },
  // Panel-only risk modules — no dedicated page, so non-navigable (route: null).
  amd: {
    label: "AMD",
    icon: Eye,
    route: null,
    color: "text-yellow-700 dark:text-yellow-400",
  },
  lhon: {
    label: "LHON",
    icon: Eye,
    route: null,
    color: "text-amber-700 dark:text-amber-400",
  },
  parkinsons: {
    label: "Parkinson's",
    icon: Brain,
    route: null,
    color: "text-slate-600 dark:text-slate-400",
  },
  gout: {
    label: "Gout",
    icon: Droplet,
    route: null,
    color: "text-red-700 dark:text-red-400",
  },
  hemochromatosis: {
    label: "Hemochromatosis",
    icon: Droplet,
    route: null,
    color: "text-amber-700 dark:text-amber-500",
  },
  thrombophilia: {
    label: "Thrombophilia",
    icon: Droplet,
    route: null,
    color: "text-rose-600 dark:text-rose-400",
  },
  roh: {
    label: "ROH",
    icon: Globe,
    route: null,
    color: "text-blue-600 dark:text-blue-400",
  },
  mt_rnr1: {
    label: "MT-RNR1",
    icon: Pill,
    route: null,
    color: "text-sky-600 dark:text-sky-400",
  },
  alpha1: {
    label: "Alpha-1",
    icon: Activity,
    route: null,
    color: "text-teal-600 dark:text-teal-400",
  },
  apol1: {
    label: "APOL1",
    icon: Droplet,
    route: null,
    color: "text-red-700 dark:text-red-400",
  },
  // Gated disclosure modules: surface in the findings list after the user
  // acknowledges the gate. No dedicated page → non-navigable.
  sex_aneuploidy: {
    label: "Sex Aneuploidy",
    icon: Dna,
    route: null,
    color: "text-violet-700 dark:text-violet-500",
  },
  kinship: {
    label: "Kinship",
    icon: Users,
    route: null,
    color: "text-blue-700 dark:text-blue-500",
  },
}

/**
 * Metadata for a module, with a safe fallback for an unmapped module string: a
 * title-cased label, a generic icon, and a null route (non-navigable) so a
 * finding never silently links to the Dashboard. The drift-guard keeps the
 * fallback from being hit for any real findings-producing module.
 */
export function getModuleMeta(module: string): ModuleMeta {
  return (
    MODULE_META[module] ?? {
      label: module.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      icon: ClipboardList,
      route: null,
      color: "text-muted-foreground",
    }
  )
}
