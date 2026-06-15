/**
 * Single source of truth for the app's primary navigation routes (#638).
 *
 * Both navigation surfaces render from this one list:
 *   - the Sidebar (full vertical nav, `components/layout/Sidebar.tsx`)
 *   - the Command Palette (⌘/Ctrl-K quick-jump "Pages" group, `components/CommandPalette.tsx`)
 *
 * They used to be two independent hand-maintained arrays that drifted silently —
 * the Command Palette was missing 5 routes the Sidebar had (/findings,
 * /metabolic, /fh, /ebmd, /rare-variants), so ⌘K couldn't reach them. Deriving
 * both from this registry makes that drift structurally impossible; the
 * `test/nav-routes.test.tsx` drift-guard locks that both surfaces cover every
 * route here.
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
  FileText,
  FlaskConical,
  Flower2,
  Fingerprint,
  Globe,
  Heart,
  HeartPulse,
  Layers,
  LayoutDashboard,
  type LucideIcon,
  Moon,
  Pill,
  SearchCheck,
  Settings,
  ShieldAlert,
  SlidersHorizontal,
  Sun,
  Table2,
} from 'lucide-react'

export interface NavRoute {
  /** Router path, e.g. `/findings`. */
  to: string
  /** Lucide icon component for the entry. */
  icon: LucideIcon
  /** Human-readable label shown in both surfaces. */
  label: string
}

export const navRoutes: NavRoute[] = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/findings', icon: ClipboardList, label: 'All Findings' },
  { to: '/variants', icon: Table2, label: 'Variant Explorer' },
  { to: '/pharmacogenomics', icon: Pill, label: 'Pharmacogenomics' },
  { to: '/nutrigenomics', icon: Apple, label: 'Nutrigenomics' },
  { to: '/cancer', icon: ShieldAlert, label: 'Cancer' },
  { to: '/cardiovascular', icon: HeartPulse, label: 'Cardiovascular' },
  { to: '/metabolic', icon: Droplet, label: 'Metabolic (T2D & Obesity)' },
  { to: '/fh', icon: Heart, label: 'Familial Hypercholesterolemia' },
  { to: '/ebmd', icon: Bone, label: 'Bone Density (eBMD)' },
  { to: '/apoe', icon: Brain, label: 'APOE' },
  { to: '/carrier-status', icon: Baby, label: 'Carrier Status' },
  { to: '/fitness', icon: Dumbbell, label: 'Gene Fitness' },
  { to: '/sleep', icon: Moon, label: 'Gene Sleep' },
  { to: '/methylation', icon: FlaskConical, label: 'Methylation' },
  { to: '/skin', icon: Sun, label: 'Gene Skin' },
  { to: '/allergy', icon: Flower2, label: 'Gene Allergy' },
  { to: '/traits', icon: Fingerprint, label: 'Traits & Personality' },
  { to: '/gene-health', icon: Activity, label: 'Gene Health' },
  { to: '/ancestry', icon: Globe, label: 'Ancestry' },
  { to: '/rare-variants', icon: SearchCheck, label: 'Rare Variants' },
  { to: '/genome-browser', icon: Dna, label: 'Genome Browser' },
  { to: '/query-builder', icon: SlidersHorizontal, label: 'Query Builder' },
  { to: '/overlays', icon: Layers, label: 'Annotation Overlays' },
  { to: '/reports', icon: FileText, label: 'Reports' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]
