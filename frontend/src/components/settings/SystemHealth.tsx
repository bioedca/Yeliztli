/** System Health page for Settings > System Health (P4-21b).
 *
 * Displays: system status, disk usage, database stats, log explorer.
 */

import { useState } from 'react'
import {
  useLogs,
  useDbStats,
  useSampleStats,
  useDiskUsage,
  useSystemStatus,
  type LogFilters,
  type LogEntry,
  type DatabaseStat,
} from '@/api/admin'
import { cn } from '@/lib/utils'
import DatabaseHealthPanel from '@/components/settings/DatabaseHealthPanel'
import {
  Activity,
  Database,
  HardDrive,
  ScrollText,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Clock,
  Server,
  Shield,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Search,
} from 'lucide-react'

// ── Helpers ──────────────────────────────────────────────────────────

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || bytes === 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

function formatDateTime(isoStr: string | null): string {
  if (!isoStr) return '\u2014'
  try {
    return new Date(isoStr).toLocaleString(undefined, {
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

const LOG_LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const

function levelColor(level: string): string {
  switch (level.toUpperCase()) {
    case 'ERROR':
    case 'CRITICAL':
      return 'text-red-700 dark:text-red-400'
    case 'WARNING':
      return 'text-amber-700 dark:text-amber-400'
    case 'INFO':
      return 'text-blue-600 dark:text-blue-400'
    case 'DEBUG':
      return 'text-gray-500 dark:text-gray-400'
    default:
      return 'text-foreground'
  }
}

// ── Status Overview ─────────────────────────────────────────────────

function StatusOverview() {
  const { data: status, isLoading, error } = useSystemStatus()

  if (isLoading) return <LoadingSkeleton label="System Status" />
  if (error || !status) return <ErrorCard label="System Status" error={error} />

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="flex items-center gap-2 text-base font-semibold text-foreground mb-3">
        <Server className="h-4 w-4 text-teal-600 dark:text-teal-400" />
        System Status
      </h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard icon={Activity} label="Version" value={`v${status.version}`} />
        <StatCard icon={Clock} label="Uptime" value={formatUptime(status.uptime_seconds)} />
        <StatCard
          icon={Shield}
          label="Auth"
          value={status.auth_enabled ? 'Enabled' : 'Disabled'}
        />
        <StatCard icon={Database} label="Samples" value={String(status.total_samples)} />
      </div>
      {status.active_jobs.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium text-muted-foreground mb-1">Active Jobs</p>
          <div className="space-y-1">
            {status.active_jobs.map((job) => (
              <div
                key={job.job_id}
                className="flex items-center justify-between rounded bg-muted/50 px-2 py-1 text-xs"
              >
                <span className="font-medium">{job.job_type}</span>
                <span className="text-muted-foreground">
                  {job.status} {job.progress_pct != null && `(${Math.round(job.progress_pct)}%)`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
}) {
  return (
    <div className="rounded-md bg-muted/30 p-2.5">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-0.5">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <p className="text-sm font-semibold text-foreground">{value}</p>
    </div>
  )
}

// ── Disk Usage ──────────────────────────────────────────────────────

function DiskUsageSection() {
  const { data: disk, isLoading, error } = useDiskUsage()

  if (isLoading) return <LoadingSkeleton label="Disk Usage" />
  if (error || !disk) return <ErrorCard label="Disk Usage" error={error} />

  const totalData = disk.reference_dbs_bytes + disk.sample_dbs_bytes + disk.logs_bytes + disk.other_bytes
  const segments = [
    { label: 'Reference DBs', bytes: disk.reference_dbs_bytes, color: 'bg-teal-500' },
    { label: 'Sample DBs', bytes: disk.sample_dbs_bytes, color: 'bg-blue-500' },
    { label: 'Logs', bytes: disk.logs_bytes, color: 'bg-amber-500' },
    { label: 'Other', bytes: disk.other_bytes, color: 'bg-gray-400' },
  ]

  const freePercent = disk.total_bytes > 0 ? (disk.free_bytes / disk.total_bytes) * 100 : 0
  const isLow = freePercent < 10

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="flex items-center gap-2 text-base font-semibold text-foreground mb-3">
        <HardDrive className="h-4 w-4 text-teal-600 dark:text-teal-400" />
        Disk Usage
      </h3>

      <div className="mb-3">
        <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
          <span>Data directory: {disk.data_dir}</span>
          <span className={cn(isLow && 'text-red-700 dark:text-red-400 font-medium')}>
            {formatBytes(disk.free_bytes)} free of {formatBytes(disk.total_bytes)}
          </span>
        </div>
        {isLow && (
          <div className="flex items-center gap-1 text-xs text-red-700 dark:text-red-400 mb-2">
            <AlertTriangle className="h-3 w-3" />
            Low disk space warning
          </div>
        )}
        <div className="h-3 w-full rounded-full bg-muted overflow-hidden flex">
          {segments.map(
            (seg) =>
              seg.bytes > 0 &&
              totalData > 0 && (
                <div
                  key={seg.label}
                  className={cn('h-full', seg.color)}
                  style={{ width: `${(seg.bytes / disk.total_bytes) * 100}%` }}
                  title={`${seg.label}: ${formatBytes(seg.bytes)}`}
                />
              ),
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {segments.map((seg) => (
          <div key={seg.label} className="flex items-center gap-2 text-xs">
            <div className={cn('h-2.5 w-2.5 rounded-sm', seg.color)} />
            <span className="text-muted-foreground">{seg.label}</span>
            <span className="font-medium text-foreground ml-auto">{formatBytes(seg.bytes)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Database Stats ──────────────────────────────────────────────────

function DatabaseStats() {
  const { data: stats, isLoading, error } = useDbStats()
  const { data: sampleStats } = useSampleStats()

  if (isLoading) return <LoadingSkeleton label="Database Stats" />
  if (error || !stats) return <ErrorCard label="Database Stats" error={error} />

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="flex items-center gap-2 text-base font-semibold text-foreground mb-3">
        <Database className="h-4 w-4 text-teal-600 dark:text-teal-400" />
        Database Stats
      </h3>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th className="pb-2 pr-4">Database</th>
              <th className="pb-2 pr-4">Status</th>
              <th className="pb-2 pr-4 text-right">Size</th>
              <th className="pb-2 pr-4 text-right">Rows</th>
              <th className="pb-2 pr-4">Version</th>
              <th className="pb-2">Last Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {stats.map((db) => (
              <DbRow key={db.name} db={db} />
            ))}
          </tbody>
        </table>
      </div>

      {sampleStats && sampleStats.length > 0 && (
        <div className="mt-4 pt-3 border-t border-border">
          <p className="text-xs font-medium text-muted-foreground mb-2">
            Sample Databases ({sampleStats.length})
          </p>
          <div className="space-y-1">
            {sampleStats.map((s) => (
              <div
                key={s.sample_id}
                className="flex items-center justify-between text-xs py-1"
              >
                <span className="text-foreground">{s.name}</span>
                <span className="text-muted-foreground">
                  {s.exists ? formatBytes(s.file_size_bytes) : 'Missing'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function DbRow({ db }: { db: DatabaseStat }) {
  return (
    <tr className="text-foreground">
      <td className="py-2 pr-4 font-medium">{db.display_name}</td>
      <td className="py-2 pr-4">
        {db.exists ? (
          <span className="inline-flex items-center gap-1 text-xs text-green-700 dark:text-green-400">
            <CheckCircle2 className="h-3 w-3" /> Available
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs text-red-700 dark:text-red-400">
            <XCircle className="h-3 w-3" /> Missing
          </span>
        )}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {formatBytes(db.file_size_bytes)}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {db.row_count != null ? db.row_count.toLocaleString() : '\u2014'}
      </td>
      <td className="py-2 pr-4 text-xs">{db.version || '\u2014'}</td>
      <td className="py-2 text-xs">{formatDateTime(db.last_updated)}</td>
    </tr>
  )
}

// ── Log Explorer ────────────────────────────────────────────────────

function LogExplorer() {
  const [filters, setFilters] = useState<LogFilters>({ page: 1, page_size: 50 })
  const { data, isLoading, error, refetch } = useLogs(filters)

  function setFilter(key: keyof LogFilters, value: string | number | undefined) {
    setFilters((prev) => ({ ...prev, [key]: value || undefined, page: 1 }))
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="flex items-center gap-2 text-base font-semibold text-foreground">
          <ScrollText className="h-4 w-4 text-teal-600 dark:text-teal-400" />
          Log Explorer
        </h3>
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted transition-colors"
        >
          <RefreshCw className="h-3 w-3" />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select
          value={filters.level || ''}
          onChange={(e) => setFilter('level', e.target.value)}
          className="rounded-md border border-border bg-background px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
          aria-label="Filter by log level"
        >
          {LOG_LEVELS.map((l) => (
            <option key={l} value={l}>
              {l || 'All Levels'}
            </option>
          ))}
        </select>

        <div className="relative">
          <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search messages..."
            value={filters.search || ''}
            onChange={(e) => setFilter('search', e.target.value)}
            className="rounded-md border border-border bg-background pl-7 pr-2 py-1 text-xs w-48 focus:outline-none focus:ring-1 focus:ring-primary"
            aria-label="Search log messages"
          />
        </div>

        <input
          type="text"
          placeholder="Component filter..."
          value={filters.component || ''}
          onChange={(e) => setFilter('component', e.target.value)}
          className="rounded-md border border-border bg-background px-2 py-1 text-xs w-36 focus:outline-none focus:ring-1 focus:ring-primary"
          aria-label="Filter by component"
        />

        {data && (
          <span className="ml-auto text-xs text-muted-foreground">
            {data.total.toLocaleString()} entries
          </span>
        )}
      </div>

      {/* Log table */}
      {isLoading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Loading logs...</div>
      ) : error ? (
        <div className="py-8 text-center text-sm text-red-700 dark:text-red-400">
          Failed to load logs: {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      ) : !data || data.entries.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          No log entries found.
        </div>
      ) : (
        <>
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="pb-1.5 pr-3 w-36">Timestamp</th>
                  <th className="pb-1.5 pr-3 w-16">Level</th>
                  <th className="pb-1.5 pr-3 w-40">Component</th>
                  <th className="pb-1.5">Message</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {data.entries.map((entry) => (
                  <LogRow key={entry.id} entry={entry} />
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between mt-3 pt-2 border-t border-border">
            <span className="text-xs text-muted-foreground">
              Page {data.page} of {Math.max(1, Math.ceil(data.total / data.page_size))}
            </span>
            <div className="flex gap-1">
              <button
                disabled={data.page <= 1}
                onClick={() => setFilters((prev) => ({ ...prev, page: (prev.page || 1) - 1 }))}
                className="inline-flex items-center rounded border border-border px-2 py-0.5 text-xs disabled:opacity-40 hover:bg-muted transition-colors"
                aria-label="Previous page"
              >
                <ChevronLeft className="h-3 w-3" />
              </button>
              <button
                disabled={!data.has_more}
                onClick={() => setFilters((prev) => ({ ...prev, page: (prev.page || 1) + 1 }))}
                className="inline-flex items-center rounded border border-border px-2 py-0.5 text-xs disabled:opacity-40 hover:bg-muted transition-colors"
                aria-label="Next page"
              >
                <ChevronRight className="h-3 w-3" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function LogRow({ entry }: { entry: LogEntry }) {
  const [expanded, setExpanded] = useState(false)
  const hasExtra = entry.event_data && entry.event_data !== '{}'

  return (
    <>
      <tr
        className={cn(
          'hover:bg-muted/30 cursor-pointer transition-colors',
          hasExtra && 'cursor-pointer',
        )}
        onClick={() => hasExtra && setExpanded(!expanded)}
      >
        <td className="py-1 pr-3 text-muted-foreground whitespace-nowrap">
          {formatDateTime(entry.timestamp)}
        </td>
        <td className={cn('py-1 pr-3 font-medium', levelColor(entry.level))}>
          {entry.level}
        </td>
        <td className="py-1 pr-3 text-muted-foreground truncate max-w-[160px]" title={entry.logger || ''}>
          {entry.logger?.split('.').pop() || '\u2014'}
        </td>
        <td className="py-1 truncate max-w-[400px] text-foreground" title={entry.message || ''}>
          {entry.message || '\u2014'}
        </td>
      </tr>
      {expanded && hasExtra && (
        <tr>
          <td colSpan={4} className="bg-muted/20 px-3 py-2">
            <pre className="text-xs text-muted-foreground whitespace-pre-wrap break-all">
              {(() => {
                try {
                  return JSON.stringify(JSON.parse(entry.event_data!), null, 2)
                } catch {
                  return entry.event_data
                }
              })()}
            </pre>
          </td>
        </tr>
      )}
    </>
  )
}

// ── Shared components ───────────────────────────────────────────────

function LoadingSkeleton({ label }: { label: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 animate-pulse">
      <div className="h-5 w-32 rounded bg-muted mb-3" />
      <div className="space-y-2">
        <div className="h-3 w-full rounded bg-muted" />
        <div className="h-3 w-3/4 rounded bg-muted" />
      </div>
      <span className="sr-only">Loading {label}...</span>
    </div>
  )
}

function ErrorCard({
  label,
  error,
}: {
  label: string
  error: unknown
}) {
  return (
    <div className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/20 p-4">
      <h3 className="flex items-center gap-2 text-base font-semibold text-red-700 dark:text-red-400">
        <AlertTriangle className="h-4 w-4" />
        {label}
      </h3>
      <p className="text-sm text-red-700 dark:text-red-400 mt-1">
        {error instanceof Error ? error.message : 'Failed to load data.'}
      </p>
    </div>
  )
}

// ── Main export ─────────────────────────────────────────────────────

export default function SystemHealth() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-foreground mb-1">System Health</h2>
        <p className="text-sm text-muted-foreground">
          Monitor service status, database statistics, disk usage, and application logs.
        </p>
      </div>

      <StatusOverview />
      <DiskUsageSection />
      <DatabaseHealthPanel />
      <DatabaseStats />
      <LogExplorer />
    </div>
  )
}
