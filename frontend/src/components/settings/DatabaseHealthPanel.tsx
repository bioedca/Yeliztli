/** Database Health panel for Settings → System Health.
 *
 * 100% observability of every reference database: derived state, readability
 * (integrity), resumable partials with live progress, last error, and recovery
 * actions (Resume an interrupted download, Verify integrity, Clean a broken
 * artifact so it can be re-fetched).
 */

import { useState } from 'react'
import {
  useDatabaseHealth,
  useResumeDownload,
  useVerifyDatabase,
  useCleanDatabase,
  type DatabaseHealth,
  type DatabaseHealthState,
} from '@/api/db-health'
import { cn } from '@/lib/utils'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Download,
  HardDrive,
  Loader2,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react'

// ── Helpers ──────────────────────────────────────────────────────────

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  if (bytes === 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

interface StateMeta {
  label: string
  className: string
  icon: React.ComponentType<{ className?: string }>
  spin?: boolean
}

const STATE_META: Record<DatabaseHealthState, StateMeta> = {
  ready: {
    label: 'Ready',
    className: 'text-green-700 dark:text-green-400',
    icon: CheckCircle2,
  },
  downloading: {
    label: 'Downloading',
    className: 'text-blue-600 dark:text-blue-400',
    icon: Loader2,
    spin: true,
  },
  building: {
    label: 'Building',
    className: 'text-blue-600 dark:text-blue-400',
    icon: Loader2,
    spin: true,
  },
  partial: {
    label: 'Partial',
    className: 'text-amber-700 dark:text-amber-400',
    icon: AlertTriangle,
  },
  corrupt: {
    label: 'Corrupt',
    className: 'text-red-700 dark:text-red-400',
    icon: ShieldAlert,
  },
  failed: {
    label: 'Failed',
    className: 'text-red-700 dark:text-red-400',
    icon: XCircle,
  },
  not_installed: {
    label: 'Not installed',
    className: 'text-muted-foreground',
    icon: HardDrive,
  },
}

function StateBadge({ state }: { state: DatabaseHealthState }) {
  const meta = STATE_META[state] ?? STATE_META.not_installed
  const Icon = meta.icon
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs font-medium', meta.className)}>
      <Icon className={cn('h-3.5 w-3.5', meta.spin && 'animate-spin')} />
      {meta.label}
    </span>
  )
}

// ── Row ──────────────────────────────────────────────────────────────

function HealthRow({ db }: { db: DatabaseHealth }) {
  const resume = useResumeDownload()
  const verify = useVerifyDatabase()
  const clean = useCleanDatabase()
  const [verifyResult, setVerifyResult] = useState<string | null>(null)

  const busy = resume.isPending || verify.isPending || clean.isPending

  const integrityNote =
    db.integrity_ok === false
      ? db.integrity_detail
      : db.last_error || (db.integrity_ok ? null : db.integrity_detail)

  return (
    <>
      <tr className="text-foreground align-top">
        {/* Name + state */}
        <td className="py-2.5 pr-4">
          <div className="font-medium text-sm">{db.display_name}</div>
          <div className="mt-0.5">
            <StateBadge state={db.state} />
          </div>
        </td>

        {/* Version */}
        <td className="py-2.5 pr-4 text-xs">{db.version ?? '—'}</td>

        {/* Size / progress */}
        <td className="py-2.5 pr-4 text-xs tabular-nums">
          {db.state === 'partial' && db.resumable && db.total_bytes ? (
            <span>
              {formatBytes(db.downloaded_bytes)} / {formatBytes(db.total_bytes)}
              {db.progress_pct != null && (
                <span className="ml-1 text-muted-foreground">({db.progress_pct}%)</span>
              )}
            </span>
          ) : (
            formatBytes(db.file_size_bytes)
          )}
        </td>

        {/* Integrity */}
        <td className="py-2.5 pr-4 text-xs">
          {db.integrity_ok == null ? (
            <span className="text-muted-foreground">—</span>
          ) : db.integrity_ok ? (
            <span className="inline-flex items-center gap-1 text-green-700 dark:text-green-400">
              <ShieldCheck className="h-3 w-3" /> OK
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-red-700 dark:text-red-400">
              <AlertTriangle className="h-3 w-3" /> Failed
            </span>
          )}
        </td>

        {/* Actions */}
        <td className="py-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            {db.can_resume && (
              <ActionButton
                label="Resume"
                icon={Download}
                pending={resume.isPending}
                disabled={busy}
                onClick={() => resume.mutate(db.name)}
              />
            )}
            {db.can_verify && (
              <ActionButton
                label="Verify"
                icon={ShieldCheck}
                pending={verify.isPending}
                disabled={busy}
                onClick={() =>
                  verify.mutate(db.name, {
                    onSuccess: (r) =>
                      setVerifyResult(r.ok ? 'Integrity OK' : `Failed: ${r.detail}`),
                    onError: (e) =>
                      setVerifyResult(e instanceof Error ? e.message : 'Verify failed'),
                  })
                }
              />
            )}
            {db.can_clean && (
              <ActionButton
                label="Clean"
                icon={Trash2}
                variant="danger"
                pending={clean.isPending}
                disabled={busy}
                onClick={() => {
                  const ok = window.confirm(
                    `Remove the broken ${db.display_name} artifact so it can be re-downloaded? ` +
                      `This does not delete healthy databases.`,
                  )
                  if (ok) clean.mutate(db.name)
                }}
              />
            )}
            {!db.can_resume && !db.can_verify && !db.can_clean && (
              <span className="text-xs text-muted-foreground">—</span>
            )}
          </div>
        </td>
      </tr>

      {/* Detail row: integrity/error note or verify result. aria-live so the
          async Verify outcome is announced to screen readers. */}
      {(integrityNote || verifyResult) && (
        <tr>
          <td colSpan={5} className="pb-2 pr-4" aria-live="polite">
            {integrityNote && (
              <p className="text-[11px] text-red-700 dark:text-red-400 break-words">{integrityNote}</p>
            )}
            {verifyResult && (
              <p
                className={cn(
                  'text-[11px] break-words',
                  verifyResult.startsWith('Integrity OK')
                    ? 'text-green-700 dark:text-green-400'
                    : 'text-red-700 dark:text-red-400',
                )}
              >
                {verifyResult}
              </p>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

function ActionButton({
  label,
  icon: Icon,
  onClick,
  pending,
  disabled,
  variant = 'default',
}: {
  label: string
  icon: React.ComponentType<{ className?: string }>
  onClick: () => void
  pending?: boolean
  disabled?: boolean
  variant?: 'default' | 'danger'
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        variant === 'danger'
          ? 'border-red-300 text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950/30'
          : 'border-border text-foreground hover:bg-muted',
      )}
    >
      {pending ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Icon className="h-3 w-3" />
      )}
      {label}
    </button>
  )
}

// ── Panel ────────────────────────────────────────────────────────────

export default function DatabaseHealthPanel() {
  const { data, isLoading, error, refetch, isFetching } = useDatabaseHealth()

  const needsAttention =
    data?.databases.filter((d) =>
      ['partial', 'corrupt', 'failed'].includes(d.state),
    ) ?? []

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="flex items-center gap-2 text-base font-semibold text-foreground">
          <Activity className="h-4 w-4 text-teal-600 dark:text-teal-400" />
          Database Health
        </h3>
        <button
          type="button"
          onClick={() => refetch()}
          aria-label="Refresh database health"
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted transition-colors"
        >
          <RefreshCw className={cn('h-3 w-3', isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {needsAttention.length > 0 && (
        <div
          className="mb-3 flex items-center gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-300"
          role="status"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {needsAttention.length} database{needsAttention.length !== 1 ? 's' : ''} need
          {needsAttention.length === 1 ? 's' : ''} attention — resume, verify, or clean below.
        </div>
      )}

      {isLoading ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Loading database health…</p>
      ) : error ? (
        <p className="py-6 text-center text-sm text-red-700 dark:text-red-400">
          {error instanceof Error ? error.message : 'Failed to load database health.'}
        </p>
      ) : !data || data.databases.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No databases registered.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="pb-2 pr-4">Database</th>
                <th className="pb-2 pr-4">Version</th>
                <th className="pb-2 pr-4">Size</th>
                <th className="pb-2 pr-4">Integrity</th>
                <th className="pb-2">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {data.databases.map((db) => (
                <HealthRow key={db.name} db={db} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
