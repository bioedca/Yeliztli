/** Subtle dashboard banner announcing a new Yeliztli release.
 *
 * Reads `useAppUpdate` (GitHub Releases check). Dismissal is per-version —
 * the dismissed version string lives in `localStorage["appUpdateDismissed"]`,
 * so a newer release re-shows the banner.
 *
 * Also renders the Step-23 LAI degraded-coverage advisory (Plan §6.7) when
 * the install holds at least one AncestryDNA sample annotated against a
 * pre-v2.0.0 `lai_bundle`. The advisory is dismissible via its own
 * `localStorage` key (`laiDegradedCoverageDismissed`) keyed by the
 * installed bundle version, so a future bundle upgrade re-arms the banner
 * if the install regresses.
 *
 * Mounted on the Dashboard only (per setup-update-plan §5.1).
 */

import { useState } from 'react'
import { AlertTriangle, ArrowUpCircle, X } from 'lucide-react'
import { useAppUpdate } from '@/api/updates'
import { useLAIStatus } from '@/api/ancestry'
import { cn } from '@/lib/utils'

const DISMISSED_STORAGE_KEY = 'appUpdateDismissed'
const LAI_DEGRADED_STORAGE_KEY = 'laiDegradedCoverageDismissed'

function readStorageValue(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorageValue(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // localStorage may be unavailable (private mode, quota); fail silently.
  }
}

function AppReleaseBanner() {
  const { data: appUpdate } = useAppUpdate()
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(() =>
    readStorageValue(DISMISSED_STORAGE_KEY),
  )

  if (!appUpdate?.update_available || !appUpdate.latest_version) {
    return null
  }
  if (dismissedVersion === appUpdate.latest_version) {
    return null
  }

  const handleDismiss = () => {
    if (!appUpdate.latest_version) return
    writeStorageValue(DISMISSED_STORAGE_KEY, appUpdate.latest_version)
    setDismissedVersion(appUpdate.latest_version)
  }

  return (
    <div
      data-testid="app-update-banner"
      role="status"
      aria-label={`Yeliztli v${appUpdate.latest_version} is available`}
      className={cn(
        'flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm',
        'border-amber-200 bg-amber-50 text-amber-800',
        'dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200',
      )}
    >
      <div className="flex items-center gap-2 min-w-0">
        <ArrowUpCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="truncate">
          Yeliztli v{appUpdate.latest_version} is available
          {appUpdate.release_url ? (
            <>
              {' — '}
              <a
                href={appUpdate.release_url}
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded"
              >
                view release notes
              </a>
              .
            </>
          ) : (
            '.'
          )}
        </span>
      </div>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss update notification"
        className={cn(
          'inline-flex h-6 w-6 shrink-0 items-center justify-center rounded',
          'hover:bg-amber-100 dark:hover:bg-amber-900/40 transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
        )}
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </div>
  )
}

function LAIDegradedCoverageBanner() {
  const { data: laiStatus } = useLAIStatus()
  const [dismissed, setDismissed] = useState<boolean>(() =>
    readStorageValue(LAI_DEGRADED_STORAGE_KEY) === '1',
  )

  if (
    !laiStatus?.degraded_coverage ||
    laiStatus.coverage_policy_available === false ||
    laiStatus.insufficient_data_reason
  ) {
    return null
  }
  if (dismissed) {
    return null
  }

  const handleDismiss = () => {
    writeStorageValue(LAI_DEGRADED_STORAGE_KEY, '1')
    setDismissed(true)
  }

  return (
    <div
      data-testid="lai-degraded-coverage-banner"
      role="status"
      aria-label="LAI coverage degraded for AncestryDNA"
      className={cn(
        'flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm',
        'border-amber-200 bg-amber-50 text-amber-800',
        'dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200',
      )}
    >
      <div className="flex items-center gap-2 min-w-0">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="truncate">
          LAI coverage degraded for AncestryDNA — update bundle to v2.0.0 for
          full chromosome painting.
        </span>
      </div>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss LAI coverage notification"
        className={cn(
          'inline-flex h-6 w-6 shrink-0 items-center justify-center rounded',
          'hover:bg-amber-100 dark:hover:bg-amber-900/40 transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
        )}
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </div>
  )
}

export default function AppUpdateBanner() {
  return (
    <>
      <AppReleaseBanner />
      <LAIDegradedCoverageBanner />
    </>
  )
}
