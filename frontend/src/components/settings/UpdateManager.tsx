/* eslint-disable react-refresh/only-export-components */
/** Update Manager Settings sub-page (P4-18).
 *
 * Per-database table with version info, auto-update toggles,
 * "Update now" buttons, collapsible history log, and
 * re-annotation prompt banner.
 */

import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  DB_STATUS_KEY,
  useAppUpdate,
  useDatabaseStatuses,
  useUpdateCheck,
  useUpdateHistory,
  useReannotationPrompts,
  useTriggerUpdate,
  useDismissPrompt,
  useToggleAutoUpdate,
  useFindingChanges,
  useDismissFindingChanges,
  type DatabaseStatus,
  type UpdateAvailable,
  type UpdateHistoryEntry,
  type ReannotationPrompt,
  type TriggerUpdateVariables,
} from '@/api/updates'
import {
  busyLabel,
  isDatabaseBusy,
  useDatabaseHealth,
  type DatabaseHealth,
} from '@/api/db-health'
import { cn } from '@/lib/utils'
import {
  RefreshCw,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Download,
  ExternalLink,
  GitCompareArrows,
  MinusCircle,
  PlusCircle,
  XCircle,
  Zap,
} from 'lucide-react'

// ── Helpers ──────────────────────────────────────────────────────────

function formatBytes(bytes: number | null): string {
  if (bytes == null) return '—'
  if (bytes === 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
}

function formatDateTime(isoStr: string | null): string {
  if (!isoStr) return '—'
  try {
    return new Date(isoStr).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return isoStr
  }
}

function getUpdateCadence(dbName: string, autoUpdate: boolean): string {
  if (!autoUpdate) return 'Manual'
  // ClinVar and GWAS update frequently; others are less frequent
  if (dbName === 'clinvar') return 'Weekly'
  if (dbName === 'gwas_catalog') return 'Monthly'
  if (dbName === 'cpic') return 'Monthly'
  return 'Auto'
}

// ── Bandwidth window helpers ─────────────────────────────────────────

/** Format ``"02:00-06:00"`` as ``"02:00–06:00"`` (en-dash) for display. */
function formatWindow(window: string): string {
  return window.replace('-', '–')
}

/**
 * Return true when `now` falls outside the configured bandwidth window.
 * Windows are ``"HH:MM-HH:MM"``; ranges may wrap past midnight
 * (e.g. ``"22:00-06:00"``). Returns false for missing/malformed windows
 * so a misconfigured value never blocks an update.
 */
export function isOutsideBandwidthWindow(
  window: string | null | undefined,
  now: Date = new Date(),
): boolean {
  if (!window) return false
  const match = window.match(/^(\d{2}):(\d{2})-(\d{2}):(\d{2})$/)
  if (!match) return false
  const startMin = Number(match[1]) * 60 + Number(match[2])
  const endMin = Number(match[3]) * 60 + Number(match[4])
  const nowMin = now.getHours() * 60 + now.getMinutes()
  if (startMin <= endMin) {
    return !(startMin <= nowMin && nowMin <= endMin)
  }
  // Window wraps midnight
  return !(nowMin >= startMin || nowMin <= endMin)
}

// ── Re-annotation Banner ─────────────────────────────────────────────

function ReannotationBanner({ prompts }: { prompts: ReannotationPrompt[] }) {
  const dismissMutation = useDismissPrompt()

  if (prompts.length === 0) return null

  const totalCandidates = prompts.reduce((sum, p) => sum + p.candidate_count, 0)
  const dbNames = [...new Set(prompts.map((p) => p.db_name))]

  return (
    <div
      className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30 p-4"
      role="alert"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
            Re-annotation recommended
          </p>
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
            {dbNames.join(' + ')} updated with {totalCandidates} potential
            reclassification{totalCandidates !== 1 ? 's' : ''} across{' '}
            {prompts.length} sample{prompts.length !== 1 ? 's' : ''}.
            Re-annotate to update findings.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {prompts.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => dismissMutation.mutate(p.id)}
                disabled={dismissMutation.isPending && dismissMutation.variables === p.id}
                className={cn(
                  'inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium',
                  'border border-amber-400 dark:border-amber-600',
                  'text-amber-700 dark:text-amber-300',
                  'hover:bg-amber-100 dark:hover:bg-amber-900/50',
                  'disabled:opacity-50',
                )}
              >
                <XCircle className="h-3 w-3" />
                Dismiss ({p.db_name})
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Finding-level change diff (SW-A4b) ───────────────────────────────

const FINDING_FIELD_LABELS: Record<string, string> = {
  clinvar_significance: 'ClinVar significance',
  evidence_level: 'Evidence level',
  metabolizer_status: 'Metabolizer status',
  pathway_level: 'Pathway level',
}

/** Short label for a finding: gene → variant → drug → module. */
function findingLabel(f: {
  gene_symbol: string | null
  rsid: string | null
  drug: string | null
  module: string
}): string {
  return f.gene_symbol || f.rsid || f.drug || f.module
}

/**
 * "What changed since your last analysis" panel for one sample (SW-A4b).
 *
 * Disclosure only: it reports which findings were added / removed / reclassified
 * because the upstream source releases advanced — never a change in the user's
 * DNA. Renders nothing when there is no undismissed diff with changes. Mirrors
 * the backend ``FindingChangesResponse`` (backend/api/routes/updates.py).
 */
export function FindingChangesPanel({ sampleId }: { sampleId: number }) {
  const { data } = useFindingChanges(sampleId)
  const dismissMutation = useDismissFindingChanges()

  if (!data || !data.available) return null

  const { release_deltas, changed, added, removed } = data
  const releaseSummary = release_deltas
    .map((d) => `${d.db_name} ${d.before ?? '—'} → ${d.after ?? '—'}`)
    .join(', ')

  return (
    <div
      className="rounded-lg border border-sky-300 bg-sky-50 dark:border-sky-700 dark:bg-sky-950/30 p-4"
      role="status"
      data-testid={`finding-changes-${sampleId}`}
    >
      <div className="flex items-start gap-3">
        <GitCompareArrows className="h-5 w-5 shrink-0 text-sky-600 dark:text-sky-400 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-sky-800 dark:text-sky-200">
            What changed since your last analysis
          </p>
          <p className="mt-1 text-xs text-sky-700/90 dark:text-sky-300/90">
            These reflect updated reference databases
            {releaseSummary ? ` (${releaseSummary})` : ''} — changes in the data
            sources, not in your DNA.
          </p>

          {changed.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300">
                Reclassified ({changed.length})
              </p>
              <ul className="mt-1 space-y-1">
                {changed.map((f, i) => (
                  <li
                    key={`changed-${i}`}
                    className="text-sm text-sky-800 dark:text-sky-200"
                  >
                    <span className="font-medium">{findingLabel(f)}</span>
                    {f.changes.map((c, j) => (
                      <span
                        key={`change-${j}`}
                        className="ml-1 inline-flex items-center gap-1 text-sky-700 dark:text-sky-300"
                      >
                        — {FINDING_FIELD_LABELS[c.field] ?? c.field}:{' '}
                        <span>{c.before ?? '—'}</span>
                        <ArrowRight className="h-3 w-3" />
                        <span className="font-medium">{c.after ?? '—'}</span>
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {added.length > 0 && (
            <div className="mt-3">
              <p className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300">
                <PlusCircle className="h-3 w-3" /> New ({added.length})
              </p>
              <ul className="mt-1 space-y-0.5">
                {added.map((f, i) => (
                  <li key={`added-${i}`} className="text-sm text-sky-800 dark:text-sky-200">
                    <span className="font-medium">{findingLabel(f)}</span>
                    {f.finding_text ? ` — ${f.finding_text}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {removed.length > 0 && (
            <div className="mt-3">
              <p className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300">
                <MinusCircle className="h-3 w-3" /> No longer reported ({removed.length})
              </p>
              <ul className="mt-1 space-y-0.5">
                {removed.map((f, i) => (
                  <li key={`removed-${i}`} className="text-sm text-sky-800 dark:text-sky-200">
                    <span className="font-medium">{findingLabel(f)}</span>
                    {f.finding_text ? ` — ${f.finding_text}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-3">
            <button
              type="button"
              onClick={() => dismissMutation.mutate(sampleId)}
              disabled={dismissMutation.isPending}
              className={cn(
                'inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium',
                'border border-sky-400 dark:border-sky-600',
                'text-sky-700 dark:text-sky-300',
                'hover:bg-sky-100 dark:hover:bg-sky-900/50',
                'disabled:opacity-50',
              )}
            >
              <XCircle className="h-3 w-3" />
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Database Table ───────────────────────────────────────────────────

interface DatabaseRowProps {
  status: DatabaseStatus
  updateInfo: UpdateAvailable | undefined
  dbHealth: DatabaseHealth | undefined
  checkedAt: string | null
  onTriggerUpdate: (dbName: string, force: boolean) => void
  onToggleAutoUpdate: (dbName: string, enabled: boolean) => void
  isUpdating: boolean
  isTogglingAutoUpdate: boolean
}

function DatabaseRow({
  status,
  updateInfo,
  dbHealth,
  checkedAt,
  onTriggerUpdate,
  onToggleAutoUpdate,
  isUpdating,
  isTogglingAutoUpdate,
}: DatabaseRowProps) {
  const hasUpdate = status.update_available || updateInfo != null
  // Live health, not the (cached) status list, decides whether this DB is
  // already being provisioned — a wizard download, an auto-update, or a manual
  // update already in flight. Offering "Update now" then would race that work.
  const isBusy = isDatabaseBusy(dbHealth)
  // ``isUpdating`` is THIS row's just-clicked update; ``isBusy`` is any in-flight
  // provisioning (possibly from elsewhere). Either disables the controls.
  const disableUpdate = isUpdating || isBusy
  const outsideWindow = isOutsideBandwidthWindow(status.update_download_window)
  const windowTooltip = status.update_download_window
    ? `Outside bandwidth window (${formatWindow(status.update_download_window)}). Update will run in window or click Force update.`
    : ''

  return (
    <tr className="border-b border-border last:border-0">
      {/* Database name */}
      <td className="py-3 px-4">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'inline-block h-2 w-2 rounded-full shrink-0',
              status.current_version != null
                ? hasUpdate
                  ? 'border border-amber-500'
                  : 'bg-primary'
                : 'border border-muted-foreground',
            )}
            aria-hidden="true"
          />
          <span className="font-medium text-sm">{status.display_name}</span>
        </div>
      </td>

      {/* Current version */}
      <td className="py-3 px-4 text-sm text-muted-foreground">
        {status.version_display ?? status.current_version ?? 'Not installed'}
      </td>

      {/* Latest available */}
      <td className="py-3 px-4 text-sm">
        {updateInfo ? (
          <span className="text-amber-700 dark:text-amber-400 font-medium">
            {updateInfo.latest_version}
          </span>
        ) : (
          <span className="text-muted-foreground">
            {status.current_version ? 'Up to date' : '—'}
          </span>
        )}
      </td>

      {/* Size */}
      <td className="py-3 px-4 text-sm text-muted-foreground">
        {updateInfo
          ? formatBytes(updateInfo.download_size_bytes)
          : formatBytes(status.file_size_bytes)}
      </td>

      {/* Last checked */}
      <td className="py-3 px-4 text-sm text-muted-foreground">
        {checkedAt ? formatDateTime(checkedAt) : 'Never'}
      </td>

      {/* Cadence */}
      <td className="py-3 px-4 text-sm">
        <span
          className={cn(
            'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
            status.auto_update
              ? 'bg-primary/10 text-primary'
              : 'bg-muted text-muted-foreground',
          )}
        >
          {getUpdateCadence(status.db_name, status.auto_update)}
        </span>
      </td>

      {/* Auto-update toggle */}
      <td className="py-3 px-4">
        <button
          type="button"
          role="switch"
          aria-checked={status.auto_update}
          aria-label={`Auto-update ${status.display_name}`}
          onClick={() => onToggleAutoUpdate(status.db_name, !status.auto_update)}
          disabled={isTogglingAutoUpdate}
          className={cn(
            'relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            status.auto_update ? 'bg-primary' : 'bg-muted-foreground/30',
          )}
        >
          <span
            className={cn(
              'inline-block h-4 w-4 rounded-full bg-white dark:bg-foreground shadow transition-transform',
              'translate-y-0.5',
              status.auto_update ? 'translate-x-4' : 'translate-x-0.5',
            )}
          />
        </button>
      </td>

      {/* Update now */}
      <td className="py-3 px-4">
        {hasUpdate && (
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => onTriggerUpdate(status.db_name, false)}
              disabled={disableUpdate}
              title={
                isBusy
                  ? 'A download or build is already in progress for this database.'
                  : outsideWindow
                    ? windowTooltip
                    : undefined
              }
              aria-describedby={
                outsideWindow && !isBusy
                  ? `${status.db_name}-window-hint`
                  : undefined
              }
              data-testid={`db-update-${status.db_name}`}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium',
                'bg-primary text-primary-foreground',
                'hover:bg-primary/90',
                'disabled:opacity-50 disabled:cursor-not-allowed',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              )}
            >
              {disableUpdate ? (
                <>
                  <RefreshCw className="h-3 w-3 animate-spin" />
                  {isUpdating ? 'Updating…' : busyLabel(dbHealth)}
                </>
              ) : (
                <>
                  <Download className="h-3 w-3" />
                  Update now
                </>
              )}
            </button>
            {outsideWindow && (
              <>
                <span id={`${status.db_name}-window-hint`} className="sr-only">
                  {windowTooltip}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    const ok = window.confirm(
                      `Force update bypasses the bandwidth window (${formatWindow(
                        status.update_download_window!,
                      )}). Large downloads may impact other network activity. Continue?`,
                    )
                    if (ok) onTriggerUpdate(status.db_name, true)
                  }}
                  disabled={disableUpdate}
                  title="Force update — bypasses the bandwidth window."
                  aria-label={`Force update ${status.display_name}`}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs font-medium',
                    'border-amber-400 text-amber-700 hover:bg-amber-50',
                    'dark:border-amber-600 dark:text-amber-300 dark:hover:bg-amber-950/30',
                    'disabled:opacity-50 disabled:cursor-not-allowed',
                    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
                  )}
                >
                  <Zap className="h-3 w-3" />
                  Force
                </button>
              </>
            )}
          </div>
        )}
      </td>
    </tr>
  )
}

// ── App version row ─────────────────────────────────────────────────

function AppVersionRow() {
  const { data: appUpdate } = useAppUpdate()
  if (!appUpdate) return null

  const hasUpdate = appUpdate.update_available && appUpdate.latest_version

  return (
    <tr
      className="border-b border-border bg-muted/20"
      data-testid="app-version-row"
    >
      {/* App name */}
      <td className="py-3 px-4">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'inline-block h-2 w-2 rounded-full shrink-0',
              hasUpdate ? 'border border-amber-500' : 'bg-primary',
            )}
            aria-hidden="true"
          />
          <span className="font-medium text-sm">Yeliztli</span>
        </div>
      </td>

      {/* Current version */}
      <td className="py-3 px-4 text-sm text-muted-foreground">
        v{appUpdate.current_version}
      </td>

      {/* Latest available */}
      <td className="py-3 px-4 text-sm">
        {hasUpdate ? (
          <span className="text-amber-700 dark:text-amber-400 font-medium">
            v{appUpdate.latest_version}
          </span>
        ) : (
          <span className="text-muted-foreground">Up to date</span>
        )}
      </td>

      {/* Size */}
      <td className="py-3 px-4 text-sm text-muted-foreground">—</td>

      {/* Last checked */}
      <td className="py-3 px-4 text-sm text-muted-foreground">—</td>

      {/* Cadence */}
      <td className="py-3 px-4 text-sm">
        <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
          Manual
        </span>
      </td>

      {/* Auto-update */}
      <td className="py-3 px-4 text-sm text-muted-foreground">—</td>

      {/* Action — release notes link */}
      <td className="py-3 px-4">
        {hasUpdate && appUpdate.release_url ? (
          <a
            href={appUpdate.release_url}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium',
              'hover:bg-muted/50 transition-colors',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
            )}
          >
            <ExternalLink className="h-3 w-3" />
            Release notes
          </a>
        ) : null}
      </td>
    </tr>
  )
}

// ── History Log ─────────────────────────────────────────────────────

function HistoryLog() {
  const [expanded, setExpanded] = useState(false)
  const { data: history, isLoading } = useUpdateHistory()

  // Group history entries by db_name
  const grouped = (history ?? []).reduce<Record<string, UpdateHistoryEntry[]>>(
    (acc, entry) => {
      const key = entry.db_name
      if (!acc[key]) acc[key] = []
      acc[key].push(entry)
      return acc
    },
    {},
  )

  const dbNames = Object.keys(grouped).sort()

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className={cn(
          'flex w-full items-center justify-between px-4 py-3 text-sm font-medium',
          'hover:bg-muted/50 transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded-lg',
        )}
        aria-expanded={expanded}
      >
        <span className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-muted-foreground" />
          Update History
          {history && history.length > 0 && (
            <span className="text-xs text-muted-foreground">
              ({history.length} entries)
            </span>
          )}
        </span>
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {expanded && (
        <div className="border-t border-border px-4 py-3">
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading history…</p>
          ) : dbNames.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No update history yet.
            </p>
          ) : (
            <div className="space-y-4">
              {dbNames.map((dbName) => (
                <HistorySection
                  key={dbName}
                  dbName={dbName}
                  entries={grouped[dbName]}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function HistorySection({
  dbName,
  entries,
}: {
  dbName: string
  entries: UpdateHistoryEntry[]
}) {
  const [open, setOpen] = useState(false)

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-sm font-medium hover:text-primary transition-colors"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        {dbName} ({entries.length})
      </button>

      {open && (
        <div className="ml-4 mt-2 space-y-2">
          {entries.map((entry) => (
            <div
              key={entry.id}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground border-l-2 border-border pl-3 py-1"
            >
              <span>
                {entry.previous_version ?? '(none)'} → {entry.new_version}
              </span>
              {entry.variants_reclassified != null && entry.variants_reclassified > 0 && (
                <span className="text-amber-700 dark:text-amber-400">
                  {entry.variants_reclassified} reclassified
                </span>
              )}
              {entry.download_size_bytes != null && (
                <span>{formatBytes(entry.download_size_bytes)}</span>
              )}
              {entry.duration_seconds != null && (
                <span>{formatDuration(entry.duration_seconds)}</span>
              )}
              <span>{formatDateTime(entry.updated_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main Component ───────────────────────────────────────────────────

export default function UpdateManager() {
  const queryClient = useQueryClient()
  const { data: statuses, isLoading: statusLoading } = useDatabaseStatuses()
  const { data: updateCheck, isLoading: checkLoading, refetch: recheckUpdates } = useUpdateCheck(true)
  const { data: prompts } = useReannotationPrompts()
  const { data: health } = useDatabaseHealth()
  const triggerMutation = useTriggerUpdate()
  const autoUpdateMutation = useToggleAutoUpdate()

  const updatesMap = new Map(
    updateCheck?.available?.map((u) => [u.db_name, u]) ?? [],
  )

  // Live health is the single source of truth for whether a DB is mid-flight.
  const healthMap = new Map(
    (health?.databases ?? []).map((h) => [h.name, h]),
  )

  // When a DB finishes a download/build (its health leaves the busy set), its
  // version stamp has just changed — refetch the (short-cached) status list so
  // the version/"update available" columns can't lag behind reality.
  const prevBusy = useRef<Set<string>>(new Set())
  useEffect(() => {
    const busyNow = new Set(
      (health?.databases ?? [])
        .filter((h) => isDatabaseBusy(h))
        .map((h) => h.name),
    )
    let cleared = false
    for (const name of prevBusy.current) {
      if (!busyNow.has(name)) cleared = true
    }
    if (cleared) {
      queryClient.invalidateQueries({ queryKey: DB_STATUS_KEY })
    }
    prevBusy.current = busyNow
  }, [health, queryClient])

  const isLoading = statusLoading || checkLoading

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Update Manager</h2>
          <p className="text-sm text-muted-foreground">
            Manage reference database versions and auto-update settings.
          </p>
        </div>
        <button
          type="button"
          onClick={() => recheckUpdates()}
          disabled={checkLoading}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm',
            'hover:bg-muted/50 transition-colors',
            'disabled:opacity-50',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          )}
        >
          <RefreshCw className={cn('h-3.5 w-3.5', checkLoading && 'animate-spin')} />
          Check for updates
        </button>
      </div>

      {/* Status summary */}
      {updateCheck && (
        <div className="flex flex-wrap gap-4 text-sm">
          {updateCheck.available.length > 0 ? (
            <span className="flex items-center gap-1.5 text-amber-700 dark:text-amber-400">
              <AlertTriangle className="h-4 w-4" />
              {updateCheck.available.length} update{updateCheck.available.length !== 1 ? 's' : ''} available
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-green-700 dark:text-green-400">
              <CheckCircle2 className="h-4 w-4" />
              All databases up to date
            </span>
          )}
          {updateCheck.errors.length > 0 && (
            <span className="flex items-center gap-1.5 text-red-700 dark:text-red-400">
              <XCircle className="h-4 w-4" />
              {updateCheck.errors.length} check error{updateCheck.errors.length !== 1 ? 's' : ''}
            </span>
          )}
          <span className="text-muted-foreground">
            Last checked: {formatDateTime(updateCheck.checked_at)}
          </span>
        </div>
      )}

      {/* Re-annotation banner */}
      {prompts && <ReannotationBanner prompts={prompts} />}

      {/* Finding-level "what changed" panels (SW-A4b), one per re-annotated
          sample. Each renders only when that sample has an undismissed diff. */}
      {prompts &&
        [...new Set(prompts.map((p) => p.sample_id))].map((sid) => (
          <FindingChangesPanel key={sid} sampleId={sid} />
        ))}

      {/* Database table */}
      <div className="rounded-lg border border-border bg-card overflow-x-auto">
        <table className="w-full text-left" aria-label="Database versions">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Database
              </th>
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Current
              </th>
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Latest
              </th>
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Size
              </th>
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Last Checked
              </th>
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Cadence
              </th>
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Auto
              </th>
              <th className="py-2.5 px-4 text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Action
              </th>
            </tr>
          </thead>
          <tbody>
            <AppVersionRow />
            {isLoading ? (
              <tr>
                <td colSpan={8} className="py-8 text-center text-sm text-muted-foreground">
                  Loading database status…
                </td>
              </tr>
            ) : statuses && statuses.length > 0 ? (
              statuses.map((s) => (
                <DatabaseRow
                  key={s.db_name}
                  status={{
                    ...s,
                    update_available: s.update_available || updatesMap.has(s.db_name),
                  }}
                  updateInfo={updatesMap.get(s.db_name)}
                  dbHealth={healthMap.get(s.db_name)}
                  checkedAt={updateCheck?.checked_at ?? null}
                  onTriggerUpdate={(dbName, force) =>
                    triggerMutation.mutate({ dbName, force })
                  }
                  onToggleAutoUpdate={(dbName, enabled) =>
                    autoUpdateMutation.mutate({ dbName, enabled })
                  }
                  isUpdating={
                    triggerMutation.isPending &&
                    (triggerMutation.variables as TriggerUpdateVariables | undefined)
                      ?.dbName === s.db_name
                  }
                  isTogglingAutoUpdate={
                    autoUpdateMutation.isPending &&
                    autoUpdateMutation.variables?.dbName === s.db_name
                  }
                />
              ))
            ) : (
              <tr>
                <td colSpan={8} className="py-8 text-center text-sm text-muted-foreground">
                  No databases tracked yet. Complete the setup wizard to download reference databases.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Trigger error */}
      {triggerMutation.isError && (
        <div className="rounded-lg border border-red-300 bg-red-50 dark:border-red-700 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-300">
          Update failed: {triggerMutation.error.message}
        </div>
      )}

      {/* Trigger success */}
      {triggerMutation.isSuccess && (
        <div className="rounded-lg border border-green-300 bg-green-50 dark:border-green-700 dark:bg-green-950/30 p-3 text-sm text-green-700 dark:text-green-300">
          Update started (job: {triggerMutation.data.job_id}). Check back shortly for results.
        </div>
      )}

      {/* History log */}
      <HistoryLog />
    </div>
  )
}
