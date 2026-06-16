/** Dashboard status bar — sample info + database version indicators (P1-20, P4-17).
 *
 * Shows current sample name, variant count, and database version stamps.
 * Database dots: filled = current (no update), hollow = update available.
 * Click navigates to Settings / Update Manager.
 */

import { useNavigate } from 'react-router-dom'
import { useAppUpdate, useDatabaseStatuses, useUpdateCheck } from '@/api/updates'
import { cn } from '@/lib/utils'
import { formatNumber } from '@/lib/format'
import { ArrowUpCircle, Database, User } from 'lucide-react'
import type { Sample } from '@/types/samples'

interface StatusBarProps {
  sample: Sample
  variantCount: number | null
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays === 1) return 'yesterday'
  return `${diffDays}d ago`
}

export default function StatusBar({ sample, variantCount }: StatusBarProps) {
  const navigate = useNavigate()
  const { data: statuses } = useDatabaseStatuses()
  const { data: updateCheck } = useUpdateCheck(true)
  const { data: appUpdate } = useAppUpdate()

  // Build a set of db_names that have updates available
  const updatesAvailable = new Set(
    updateCheck?.available?.map((u) => u.db_name) ?? [],
  )

  // Databases with a version stamp (installed)
  const installed = statuses?.filter((s) => s.current_version != null) ?? []

  // Pick the top 2 DBs to show version strings (ClinVar + one more)
  const versionLabels = installed
    .filter((s) => s.version_display)
    .slice(0, 2)
    .map((s) => `${s.display_name} ${s.version_display}`)

  // Count current vs update-available
  const currentCount = statuses?.filter(
    (s) => s.current_version != null && !updatesAvailable.has(s.db_name),
  ).length ?? 0
  const updateCount = updatesAvailable.size

  return (
    <div
      className={cn(
        'flex items-center justify-between gap-4 rounded-lg border bg-card px-4',
        'h-10 text-sm',
      )}
      role="status"
      aria-label="Sample and database status"
    >
      {/* Left: Sample info */}
      <div className="flex items-center gap-2 min-w-0">
        <User className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="font-medium text-foreground truncate">
          {sample.name}
        </span>
        {variantCount != null && (
          <span className="text-muted-foreground">
            &middot; {formatNumber(variantCount)} SNPs
          </span>
        )}
        {sample.created_at && (
          <span className="text-muted-foreground hidden sm:inline">
            &middot; Uploaded {formatRelativeTime(sample.created_at)}
          </span>
        )}
      </div>

      {/* App update indicator */}
      {appUpdate?.update_available && (
        <button
          type="button"
          onClick={() => navigate('/settings/about')}
          className={cn(
            'flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400',
            'hover:text-amber-800 dark:hover:text-amber-300 transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded',
          )}
          aria-label={`App update available: v${appUpdate.latest_version}`}
          title={`Yeliztli v${appUpdate.latest_version} available`}
        >
          <ArrowUpCircle className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">v{appUpdate.latest_version}</span>
        </button>
      )}

      {/* Right: Database version stamps + dots */}
      <button
        type="button"
        onClick={() => navigate('/settings/updates')}
        className={cn(
          'flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded',
        )}
        aria-label={`Databases: ${currentCount} current, ${updateCount} update${updateCount !== 1 ? 's' : ''} available`}
        title="View database status in Settings"
      >
        <Database className="h-3.5 w-3.5 shrink-0" />

        {/* Version labels (e.g., "ClinVar Mar 2026 · gnomAD 2.1.1") */}
        {versionLabels.length > 0 && (
          <span className="hidden md:inline text-xs">
            {versionLabels.join(' \u00b7 ')}
          </span>
        )}

        {/* Dot indicators: ●●●○ */}
        <span className="flex items-center gap-0.5" aria-hidden="true">
          {statuses && statuses.length > 0 ? (
            statuses.map((s) => {
              const hasVersion = s.current_version != null
              const hasUpdate = updatesAvailable.has(s.db_name)
              // Filled = installed & current, Hollow = update available or not installed
              const isCurrent = hasVersion && !hasUpdate
              return (
                <span
                  key={s.db_name}
                  className={cn(
                    'inline-block h-2 w-2 rounded-full',
                    isCurrent
                      ? 'bg-primary'
                      : hasUpdate
                        ? 'border border-amber-500'
                        : 'border border-muted-foreground',
                  )}
                  title={`${s.display_name}: ${
                    isCurrent
                      ? `v${s.current_version}`
                      : hasUpdate
                        ? 'Update available'
                        : 'Not installed'
                  }`}
                />
              )
            })
          ) : (
            <span className="text-xs">&mdash;</span>
          )}
        </span>
      </button>
    </div>
  )
}
