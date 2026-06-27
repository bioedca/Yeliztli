/** File upload component with drag-and-drop, progress, and parse status (P1-16). */

import { useState, useRef, useCallback } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import {
  Upload,
  FileText,
  CheckCircle2,
  AlertCircle,
  AlertTriangle,
  Download,
  Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { formatFileFormat } from "@/lib/format"
import { useIngestFile } from "@/api/samples"
import { BundleGateError } from "@/api/setup"
import { useTriggerUpdate } from "@/api/updates"
import type { IngestResult } from "@/types/samples"
import type { BundleGatePayload } from "@/types/setup"

type UploadState = "idle" | "dragging" | "uploading" | "complete" | "error"

const ZIP_ARCHIVE_ERROR =
  "This looks like a ZIP archive. Extract the raw 23andMe/AncestryDNA .txt file first."

function hasZipMagic(bytes: Uint8Array): boolean {
  return (
    bytes.length >= 4 &&
    bytes[0] === 0x50 &&
    bytes[1] === 0x4b &&
    ((bytes[2] === 0x03 && bytes[3] === 0x04) ||
      (bytes[2] === 0x05 && bytes[3] === 0x06) ||
      (bytes[2] === 0x07 && bytes[3] === 0x08))
  )
}

async function isZipArchiveFile(file: File): Promise<boolean> {
  const name = file.name.trim().toLowerCase()
  const declaredZip =
    name.endsWith(".zip") ||
    file.type === "application/zip" ||
    file.type === "application/x-zip-compressed"

  try {
    const head = new Uint8Array(await file.slice(0, 4).arrayBuffer())
    return declaredZip || hasZipMagic(head)
  } catch {
    return declaredZip
  }
}

export default function FileUpload() {
  const [state, setState] = useState<UploadState>("idle")
  const [fileName, setFileName] = useState<string | null>(null)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bundleGate, setBundleGate] = useState<BundleGatePayload | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const [, setSearchParams] = useSearchParams()

  const ingestMutation = useIngestFile()
  const triggerUpdate = useTriggerUpdate()

  const handleFile = useCallback(
    async (file: File) => {
      setFileName(file.name)
      setError(null)
      setBundleGate(null)

      if (await isZipArchiveFile(file)) {
        setError(ZIP_ARCHIVE_ERROR)
        setState("error")
        return
      }

      setState("uploading")

      try {
        const res = await ingestMutation.mutateAsync(file)
        setResult(res)
        setState("complete")

        // Update URL with the new sample_id so other components pick it up
        setSearchParams({ sample_id: String(res.sample_id) })
      } catch (err) {
        // AncestryDNA + pre-v2.0.0 bundle → show the one-click update gate
        // rather than a generic error.
        if (err instanceof BundleGateError) {
          setBundleGate(err.payload)
        } else {
          setError(err instanceof Error ? err.message : "Upload failed")
        }
        setState("error")
      }
    },
    [ingestMutation, setSearchParams],
  )

  const handleBundleUpdate = useCallback(async () => {
    try {
      await triggerUpdate.mutateAsync({ dbName: "vep_bundle" })
      setBundleGate(null)
      setState("idle")
      setFileName(null)
      if (fileInputRef.current) fileInputRef.current.value = ""
    } catch {
      // Error surfaced via triggerUpdate.isError below.
    }
  }, [triggerUpdate])

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setState((prev) => (prev === "idle" ? "dragging" : prev))
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setState((prev) => (prev === "dragging" ? "idle" : prev))
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile],
  )

  const onFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFile(file)
    },
    [handleFile],
  )

  const resetUpload = useCallback(() => {
    setState("idle")
    setFileName(null)
    setResult(null)
    setError(null)
    setBundleGate(null)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }, [])

  // Idle / dragging — show drop zone
  if (state === "idle" || state === "dragging") {
    return (
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") fileInputRef.current?.click()
        }}
        className={cn(
          "border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors",
          state === "dragging"
            ? "border-primary bg-primary/5"
            : "border-border hover:border-primary/50 hover:bg-accent/50",
        )}
      >
        <Upload className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
        <p className="text-sm font-medium text-foreground">
          Drop your 23andMe or AncestryDNA file here or click to browse
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Supports 23andMe raw data files (v3, v4, v5) and AncestryDNA raw data
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.csv,.tsv"
          onChange={onFileSelect}
          className="hidden"
          aria-label="Upload 23andMe or AncestryDNA file"
        />
      </div>
    )
  }

  // Uploading — show progress
  if (state === "uploading") {
    return (
      <div className="border rounded-lg p-6 text-center">
        <Loader2 className="h-8 w-8 mx-auto mb-3 text-primary animate-spin" />
        <p className="text-sm font-medium text-foreground flex items-center justify-center gap-2">
          <FileText className="h-4 w-4" />
          {fileName}
        </p>
        <p className="text-xs text-muted-foreground mt-2">
          Uploading and parsing variants...
        </p>
        <div className="mt-3 h-1.5 bg-muted rounded-full overflow-hidden max-w-xs mx-auto">
          <div
            className="h-full bg-primary rounded-full transition-all duration-500 animate-pulse"
            style={{ width: "50%" }}
          />
        </div>
      </div>
    )
  }

  // Complete — show result
  if (state === "complete" && result) {
    return (
      <div className="border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30 rounded-lg p-6">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground flex items-center gap-2">
              <FileText className="h-4 w-4 shrink-0" />
              <span className="truncate">{fileName}</span>
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {result.variant_count.toLocaleString()} variants parsed
              {result.nocall_count > 0 && ` · ${result.nocall_count.toLocaleString()} no-calls`}
              {" · "}
              {formatFileFormat(result.file_format)}
            </p>
            <div className="flex gap-2 mt-3">
              <button
                type="button"
                onClick={() =>
                  navigate(`/variants?sample_id=${result.sample_id}`)
                }
                className="text-xs font-medium text-primary hover:text-primary/80 transition-colors"
              >
                View variants &rarr;
              </button>
              <button
                type="button"
                onClick={resetUpload}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Upload another
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Bundle-version gate (HTTP 409 on AncestryDNA + pre-v2.0.0 bundle).
  // Plan §5.4 / ADNA-00d — banner with one-click update CTA.
  if (bundleGate) {
    return (
      <div
        data-testid="bundle-gate-banner"
        role="alert"
        aria-live="polite"
        className="rounded-lg border border-amber-500/50 bg-amber-50 dark:bg-amber-950/20 p-4 space-y-3"
      >
        <div className="flex items-start gap-3">
          <AlertTriangle
            className="h-5 w-5 text-amber-600 dark:text-amber-500 flex-shrink-0 mt-0.5"
            aria-hidden="true"
          />
          <div className="flex-1 min-w-0 space-y-1">
            <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
              Update VEP bundle (~
              {Math.round(bundleGate.size_bytes / 1_000_000)} MB) to enable
              AncestryDNA
            </p>
            <p className="text-xs text-amber-700 dark:text-amber-300">
              AncestryDNA uploads need VEP bundle{" "}
              <code className="rounded bg-amber-100 dark:bg-amber-900/40 px-1 py-0.5 font-mono">
                {bundleGate.required_version}
              </code>{" "}
              or newer. Installed:{" "}
              <code className="rounded bg-amber-100 dark:bg-amber-900/40 px-1 py-0.5 font-mono">
                {bundleGate.installed_version}
              </code>
              .
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleBundleUpdate}
          disabled={triggerUpdate.isPending}
          data-testid="bundle-gate-update-cta"
          className={cn(
            "w-full rounded-lg px-4 py-2.5 text-sm font-medium transition-all",
            "bg-amber-600 text-white hover:bg-amber-700 shadow-sm",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600",
            "disabled:opacity-70 disabled:cursor-not-allowed",
          )}
        >
          {triggerUpdate.isPending ? (
            <span className="flex items-center justify-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Updating bundle…
            </span>
          ) : (
            <span className="flex items-center justify-center gap-2">
              <Download className="h-4 w-4" />
              Update VEP bundle to {bundleGate.required_version}
            </span>
          )}
        </button>
        {triggerUpdate.isError && (
          <p className="text-xs text-destructive">
            {triggerUpdate.error instanceof Error
              ? triggerUpdate.error.message
              : "Bundle update failed. Please try again."}
          </p>
        )}
        <button
          type="button"
          onClick={resetUpload}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          Cancel
        </button>
      </div>
    )
  }

  // Error state
  return (
    <div className="border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 rounded-lg p-6">
      <div className="flex items-start gap-3">
        <AlertCircle className="h-5 w-5 text-red-600 dark:text-red-400 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground">Upload failed</p>
          <p className="text-xs text-muted-foreground mt-1">{error}</p>
          <button
            type="button"
            onClick={resetUpload}
            className="text-xs font-medium text-primary hover:text-primary/80 mt-3 transition-colors"
          >
            Try again
          </button>
        </div>
      </div>
    </div>
  )
}
