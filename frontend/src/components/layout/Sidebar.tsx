import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Table2,
  Pill,
  Apple,
  ShieldAlert,
  HeartPulse,
  Droplet,
  Heart,
  Bone,
  Brain,
  Baby,
  Dumbbell,
  Moon,
  FlaskConical,
  Sun,
  Flower2,
  Fingerprint,
  Activity,
  Globe,
  Dna,
  SlidersHorizontal,
  FileText,
  Settings,
  SearchCheck,
  ClipboardList,
  PanelLeftClose,
  PanelLeft,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const navItems = [
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
  { to: '/reports', icon: FileText, label: 'Reports' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside
      className={cn(
        'border-r border-sidebar-border bg-sidebar-background flex flex-col shrink-0 transition-all duration-200',
        collapsed ? 'w-12' : 'w-56'
      )}
    >
      <div className="flex-1 py-2 overflow-y-auto">
        <nav aria-label="Main navigation" className="flex flex-col gap-0.5 px-2">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-md px-2 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                    : 'text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
                  collapsed && 'justify-center px-0'
                )
              }
              title={label}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {!collapsed && <span>{label}</span>}
              {collapsed && <span className="sr-only">{label}</span>}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="border-t border-sidebar-border p-2">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center justify-center w-full rounded-md p-2 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <PanelLeft className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </button>
      </div>
    </aside>
  )
}
