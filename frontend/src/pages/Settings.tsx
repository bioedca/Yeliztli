/** Settings page with sub-navigation (P4-18, P4-21b).
 *
 * Sub-routes: /settings → redirects to /settings/updates
 *   - /settings/general   (placeholder)
 *   - /settings/updates   (Update Manager — P4-18)
 *   - /settings/health    (placeholder)
 *   - /settings/about     (placeholder)
 */

import { NavLink, Routes, Route, Navigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { Settings2, RefreshCw, Activity, Info, ArrowUpCircle, CheckCircle2, AlertCircle } from 'lucide-react'
import UpdateManager from '@/components/settings/UpdateManager'
import ExportBackup from '@/components/settings/ExportBackup'
import NuclearDelete from '@/components/settings/NuclearDelete'
import SystemHealth from '@/components/settings/SystemHealth'
import SampleMetadataEditor from '@/components/settings/SampleMetadataEditor'
import { useAppUpdate } from '@/api/updates'

const NAV_ITEMS = [
  { to: '/settings/general', label: 'General', icon: Settings2 },
  { to: '/settings/updates', label: 'Update Manager', icon: RefreshCw },
  { to: '/settings/health', label: 'System Health', icon: Activity },
  { to: '/settings/about', label: 'About', icon: Info },
] as const

function SettingsNav() {
  return (
    <nav aria-label="Settings sections" className="flex flex-col gap-0.5">
      {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
              'hover:bg-muted/50',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              isActive
                ? 'bg-muted font-medium text-foreground'
                : 'text-muted-foreground',
            )
          }
        >
          <Icon className="h-4 w-4 shrink-0" />
          {label}
        </NavLink>
      ))}
    </nav>
  )
}

function GeneralSettings() {
  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-semibold text-foreground mb-1">General Settings</h2>
        <p className="text-sm text-muted-foreground">
          Application preferences and data management.
        </p>
      </div>

      <SampleMetadataEditor />

      <hr className="border-border" />

      <ExportBackup />

      <hr className="border-border" />

      {/* Danger zone */}
      <div>
        <h3 className="text-base font-semibold text-red-700 dark:text-red-400 mb-4">
          Danger Zone
        </h3>
        <NuclearDelete />
      </div>
    </div>
  )
}

function HealthPage() {
  return <SystemHealth />
}

function AboutPage() {
  const { data: appUpdate, isLoading, refetch, isFetching } = useAppUpdate()

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-foreground mb-1">About Yeliztli</h2>
        <p className="text-sm text-muted-foreground">
          Version information and update notifications.
        </p>
      </div>

      {/* Current version */}
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-foreground">Current Version</p>
            <p className="text-2xl font-bold text-foreground">
              {appUpdate?.current_version ? `v${appUpdate.current_version}` : '...'}
            </p>
          </div>

          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className={cn(
              'rounded-md border px-3 py-1.5 text-sm transition-colors',
              'hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              isFetching && 'opacity-50 cursor-not-allowed',
            )}
          >
            {isFetching ? 'Checking...' : 'Check for updates'}
          </button>
        </div>

        {/* Update status */}
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Checking for updates...</p>
        ) : appUpdate?.update_available ? (
          <div className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30 p-3">
            <ArrowUpCircle className="h-5 w-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
                Update available: v{appUpdate.latest_version}
              </p>
              <p className="text-sm text-amber-700 dark:text-amber-300">
                Upgrade via:{' '}
                <code className="rounded bg-amber-100 dark:bg-amber-900/50 px-1.5 py-0.5 text-xs font-mono">
                  pip install --upgrade yeliztli
                </code>
              </p>
              {appUpdate.release_url && (
                <a
                  href={appUpdate.release_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-amber-700 dark:text-amber-300 underline hover:no-underline"
                >
                  View release notes
                </a>
              )}
            </div>
          </div>
        ) : appUpdate?.error ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>Could not check for updates: {appUpdate.error}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-green-700 dark:text-green-400">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            <span>You are running the latest version.</span>
          </div>
        )}
      </div>

      {/* License */}
      <div className="rounded-lg border bg-card p-4">
        <p className="text-sm font-medium text-foreground mb-1">License</p>
        <p className="text-sm text-muted-foreground">MIT License</p>
      </div>

      {/* Links */}
      <div className="rounded-lg border bg-card p-4 space-y-2">
        <p className="text-sm font-medium text-foreground mb-1">Resources</p>
        <ul className="text-sm text-muted-foreground space-y-1">
          <li>
            <a
              href="https://github.com/bioedca/Yeliztli"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:no-underline hover:text-foreground transition-colors"
            >
              GitHub Repository
            </a>
          </li>
          <li>
            <a
              href="https://github.com/bioedca/Yeliztli/issues"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:no-underline hover:text-foreground transition-colors"
            >
              Report an Issue
            </a>
          </li>
        </ul>
      </div>
    </div>
  )
}

export default function Settings() {
  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>
      <div className="flex gap-6">
        {/* Mini sidebar */}
        <div className="w-48 shrink-0">
          <SettingsNav />
        </div>

        {/* Content area */}
        <div className="flex-1 min-w-0">
          <Routes>
            <Route index element={<Navigate to="updates" replace />} />
            <Route path="general" element={<GeneralSettings />} />
            <Route path="updates" element={<UpdateManager />} />
            <Route path="health" element={<HealthPage />} />
            <Route path="about" element={<AboutPage />} />
          </Routes>
        </div>
      </div>
    </div>
  )
}
