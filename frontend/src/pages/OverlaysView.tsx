/** vcfanno overlay management page (P4-12).
 *
 * Layout:
 * - Upload panel (drag-and-drop BED/VCF file)
 * - Saved overlays list (cards with apply/delete actions)
 * - Results table (for applied overlays)
 */

import { useState, useRef } from "react"
import { useSearchParams } from "react-router-dom"
import {
  Upload,
  Trash2,
  Play,
  FileText,
  Loader2,
  CheckCircle2,
  Layers,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId, formatNumber } from "@/lib/format"
import PageEmpty from "@/components/ui/PageEmpty"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import {
  useOverlays,
  useUploadOverlay,
  useDeleteOverlay,
  useApplyOverlay,
  useOverlayResults,
  useParseOverlayPreview,
} from "@/api/overlays"
import type { OverlayConfig, OverlayApplyResponse } from "@/types/overlays"

export default function OverlaysView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedOverlay, setSelectedOverlay] = useState<OverlayConfig | null>(null)
  const [applyResult, setApplyResult] = useState<OverlayApplyResponse | null>(null)

  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Annotation Overlays</h1>
        <PageEmpty icon={Layers} title="Select a sample from the top nav to manage annotation overlays." />
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <Layers className="h-6 w-6 text-teal-600 dark:text-teal-400" />
        <h1 className="text-2xl font-bold">Annotation Overlays</h1>
      </div>
      <p className="text-muted-foreground">
        Upload BED or VCF files to overlay custom annotations onto your variant data.
      </p>

      <UploadPanel />

      <OverlayList
        sampleId={sampleId}
        selectedOverlay={selectedOverlay}
        onSelect={setSelectedOverlay}
        onApplyResult={setApplyResult}
      />

      {applyResult && (
        <ApplyResultBanner result={applyResult} onDismiss={() => setApplyResult(null)} />
      )}

      {selectedOverlay && (
        <OverlayResults overlayId={selectedOverlay.id} sampleId={sampleId} />
      )}
    </div>
  )
}

function UploadPanel() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)

  const uploadMutation = useUploadOverlay()
  const previewMutation = useParseOverlayPreview()

  const handleFileSelect = (file: File) => {
    setSelectedFile(file)
    if (!name) {
      setName(file.name.replace(/\.(bed|vcf|vcf\.gz)$/i, ""))
    }
    previewMutation.mutate(file)
  }

  const handleUpload = () => {
    if (!selectedFile || !name.trim()) return
    uploadMutation.mutate(
      { file: selectedFile, name: name.trim(), description: description.trim() },
      {
        onSuccess: () => {
          setSelectedFile(null)
          setName("")
          setDescription("")
          previewMutation.reset()
        },
      }
    )
  }

  return (
    <div className="rounded-lg border bg-card p-6 space-y-4">
      <h2 className="text-lg font-semibold flex items-center gap-2">
        <Upload className="h-5 w-5" />
        Upload Overlay File
      </h2>

      {/* Drop zone */}
      <button
        type="button"
        className={cn(
          "border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors w-full bg-transparent",
          dragOver
            ? "border-teal-500 bg-teal-50 dark:bg-teal-950/20"
            : "border-muted-foreground/25 hover:border-muted-foreground/50"
        )}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          const file = e.dataTransfer.files[0]
          if (file) handleFileSelect(file)
        }}
      >
        <FileText className="mx-auto h-10 w-10 text-muted-foreground mb-3" />
        <p className="text-sm text-muted-foreground">
          {selectedFile
            ? `Selected: ${selectedFile.name}`
            : "Drop a BED or VCF file here, or click to browse"}
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".bed,.vcf,.vcf.gz"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) handleFileSelect(file)
          }}
        />
      </button>

      {/* Preview error */}
      {previewMutation.isError && (
        <p className="text-sm text-destructive">{previewMutation.error.message}</p>
      )}

      {/* Preview */}
      {previewMutation.data && (
        <div className="rounded-md bg-muted/50 p-4 text-sm space-y-1">
          <p>
            <span className="font-medium">Format:</span>{" "}
            {previewMutation.data.file_type.toUpperCase()}
          </p>
          <p>
            <span className="font-medium">Records:</span>{" "}
            {formatNumber(previewMutation.data.record_count)}
          </p>
          <p>
            <span className="font-medium">Columns:</span>{" "}
            {previewMutation.data.column_names.join(", ") || "None"}
          </p>
          {previewMutation.data.warnings.length > 0 && (
            <div className="mt-2 text-amber-700 dark:text-amber-400">
              {previewMutation.data.warnings.slice(0, 5).map((w, i) => (
                <p key={i}>{w}</p>
              ))}
              {previewMutation.data.warnings.length > 5 && (
                <p>...and {previewMutation.data.warnings.length - 5} more warnings</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Name + description */}
      {selectedFile && (
        <div className="space-y-3">
          <div>
            <label htmlFor="overlay-name" className="text-sm font-medium">
              Name
            </label>
            <input
              id="overlay-name"
              className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Overlay name"
            />
          </div>
          <div>
            <label htmlFor="overlay-desc" className="text-sm font-medium">
              Description (optional)
            </label>
            <input
              id="overlay-desc"
              className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief description"
            />
          </div>
          <button
            className="inline-flex items-center gap-2 rounded-md bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:opacity-50"
            onClick={handleUpload}
            disabled={!name.trim() || uploadMutation.isPending}
          >
            {uploadMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Upload className="h-4 w-4" />
            )}
            Upload & Save
          </button>
          {uploadMutation.isError && (
            <p className="text-sm text-destructive">{uploadMutation.error.message}</p>
          )}
        </div>
      )}
    </div>
  )
}

function OverlayList({
  sampleId,
  selectedOverlay,
  onSelect,
  onApplyResult,
}: {
  sampleId: number
  selectedOverlay: OverlayConfig | null
  onSelect: (o: OverlayConfig | null) => void
  onApplyResult: (r: OverlayApplyResponse) => void
}) {
  const overlaysQuery = useOverlays()
  const deleteMutation = useDeleteOverlay()
  const applyMutation = useApplyOverlay()

  if (overlaysQuery.isLoading) {
    return <PageLoading message="Loading overlays..." />
  }

  const overlays = overlaysQuery.data?.items ?? []

  if (overlays.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center text-muted-foreground">
        No overlays uploaded yet. Upload a BED or VCF file above to get started.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Saved Overlays ({overlays.length})</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {overlays.map((overlay) => (
          <div
            key={overlay.id}
            className={cn(
              "rounded-lg border bg-card p-4 space-y-2 cursor-pointer transition-colors",
              selectedOverlay?.id === overlay.id
                ? "border-teal-500 ring-1 ring-teal-500"
                : "hover:border-muted-foreground/50"
            )}
            role="button"
            tabIndex={0}
            onClick={() =>
              onSelect(selectedOverlay?.id === overlay.id ? null : overlay)
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onSelect(selectedOverlay?.id === overlay.id ? null : overlay)
              }
            }}
          >
            <div className="flex items-center justify-between">
              <h3 className="font-medium truncate">{overlay.name}</h3>
              <span className="text-xs px-2 py-0.5 rounded-full bg-muted font-mono">
                {overlay.file_type.toUpperCase()}
              </span>
            </div>
            {overlay.description && (
              <p className="text-sm text-muted-foreground line-clamp-2">
                {overlay.description}
              </p>
            )}
            <div className="text-xs text-muted-foreground space-y-0.5">
              <p>{formatNumber(overlay.region_count)} regions</p>
              <p>Columns: {overlay.column_names.join(", ") || "None"}</p>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                className="inline-flex items-center gap-1.5 rounded-md bg-teal-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-teal-700 disabled:opacity-50"
                onClick={(e) => {
                  e.stopPropagation()
                  applyMutation.mutate(
                    { overlayId: overlay.id, sampleId },
                    {
                      onSuccess: (result) => {
                        onApplyResult(result)
                        onSelect(overlay)
                      },
                    }
                  )
                }}
                disabled={applyMutation.isPending}
              >
                {applyMutation.isPending ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Play className="h-3 w-3" />
                )}
                Apply
              </button>
              <button
                className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
                onClick={(e) => {
                  e.stopPropagation()
                  if (confirm(`Delete overlay "${overlay.name}"?`)) {
                    const wasSelected = selectedOverlay?.id === overlay.id
                    deleteMutation.mutate(overlay.id, {
                      onSuccess: () => {
                        if (wasSelected) onSelect(null)
                      },
                    })
                  }
                }}
                disabled={deleteMutation.isPending}
              >
                <Trash2 className="h-3 w-3" />
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
      {applyMutation.isError && (
        <p className="text-sm text-destructive mt-2">{applyMutation.error.message}</p>
      )}
      {deleteMutation.isError && (
        <p className="text-sm text-destructive mt-2">{deleteMutation.error.message}</p>
      )}
    </div>
  )
}

function ApplyResultBanner({
  result,
  onDismiss,
}: {
  result: OverlayApplyResponse
  onDismiss: () => void
}) {
  return (
    <div className="rounded-lg border border-teal-200 bg-teal-50 dark:border-teal-800 dark:bg-teal-950/30 p-4 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <CheckCircle2 className="h-5 w-5 text-teal-600 dark:text-teal-400 shrink-0" />
        <p className="text-sm">
          <span className="font-medium">{result.overlay_name}</span> applied:{" "}
          <span className="font-mono">{formatNumber(result.variants_matched)}</span> variants
          matched from {formatNumber(result.records_checked)} overlay records.
        </p>
      </div>
      <button
        className="text-xs text-muted-foreground hover:text-foreground"
        onClick={onDismiss}
      >
        Dismiss
      </button>
    </div>
  )
}

function OverlayResults({
  overlayId,
  sampleId,
}: {
  overlayId: number
  sampleId: number
}) {
  const resultsQuery = useOverlayResults(overlayId, sampleId)

  if (resultsQuery.isLoading) {
    return <PageLoading message="Loading results..." />
  }

  if (resultsQuery.isError) {
    return (
      <PageError
        message={resultsQuery.error instanceof Error ? resultsQuery.error.message : "An unexpected error occurred."}
        onRetry={() => resultsQuery.refetch()}
      />
    )
  }

  const data = resultsQuery.data
  if (!data || data.total === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center text-muted-foreground">
        No overlay results found. Apply the overlay to see matched annotations.
      </div>
    )
  }

  // Determine columns from the first result
  const allKeys = new Set<string>()
  data.results.forEach((r) => {
    Object.keys(r).forEach((k) => {
      if (k !== "overlay_id") allKeys.add(k)
    })
  })
  const columns = Array.from(allKeys).sort((a, b) => {
    if (a === "rsid") return -1
    if (b === "rsid") return 1
    return a.localeCompare(b)
  })

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">
        Overlay Results: {data.overlay_name} ({formatNumber(data.total)} matches)
      </h2>
      <div className="rounded-lg border overflow-auto max-h-[500px]">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 sticky top-0">
            <tr>
              {columns.map((col) => (
                <th key={col} className="px-3 py-2 text-left font-medium whitespace-nowrap">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.results.slice(0, 200).map((row, i) => (
              <tr key={i} className="border-t hover:bg-muted/30">
                {columns.map((col) => (
                  <td key={col} className="px-3 py-1.5 whitespace-nowrap font-mono text-xs">
                    {row[col] != null ? String(row[col]) : ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {data.total > 200 && (
          <div className="p-3 text-center text-xs text-muted-foreground border-t">
            Showing first 200 of {formatNumber(data.total)} results
          </div>
        )}
      </div>
    </div>
  )
}
