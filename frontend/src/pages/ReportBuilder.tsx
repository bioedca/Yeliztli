/** Report builder UI — module selection, preview, download (P4-10).
 *
 * Module-level selection only: include/exclude entire modules.
 * All findings within selected modules are included, sorted by evidence level.
 * Uses the existing /api/reports/generate and /api/reports/preview endpoints.
 *
 * PRD E2E flow F7: Open report builder → select modules → preview →
 * generate PDF → download → PDF is valid
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
  FileText,
  Loader2,
  Download,
  Eye,
  X,
  CheckSquare,
  Square,
  AlertCircle,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { useFindingsSummary } from "@/api/findings"
import { useGenerateReport, useExportFhir, fetchReportPreview } from "@/api/reports"
import EvidenceStars from "@/components/ui/EvidenceStars"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import type { FindingSummaryItem } from "@/types/findings"

/** Human-readable labels for finding module keys. Note the key is "carrier"
 * (the value `carrier_status.py` writes), not "carrier_status". This map is for
 * *labels only* — it never gates which modules appear (see `availableModules`),
 * so a module missing here still renders with a humanized fallback. */
const MODULE_DISPLAY_NAMES: Record<string, string> = {
  cancer: "Cancer Predisposition",
  cardiovascular: "Cardiovascular Genetics",
  metabolic: "Metabolic (T2D & Obesity)",
  fh: "Familial Hypercholesterolemia",
  ebmd: "Bone Density (eBMD)",
  apoe: "APOE Genotype",
  pharmacogenomics: "Pharmacogenomics",
  nutrigenomics: "Nutrigenomics",
  carrier: "Carrier Status",
  ancestry: "Ancestry & Haplogroups",
  gene_health: "Gene Health",
  fitness: "Gene Fitness",
  sleep: "Gene Sleep",
  methylation: "MTHFR & Methylation",
  skin: "Gene Skin",
  allergy: "Gene Allergy",
  traits: "Traits & Personality",
  rare_variants: "Rare Variant Finder",
}

/** Preferred display order. Only orders the cards — modules absent here are
 * still shown (sorted after the known ones), so this list can never silently
 * drop a finding module (#596). */
const MODULE_ORDER = [
  "cancer",
  "cardiovascular",
  "metabolic",
  "fh",
  "ebmd",
  "apoe",
  "pharmacogenomics",
  "nutrigenomics",
  "carrier",
  "ancestry",
  "gene_health",
  "fitness",
  "sleep",
  "methylation",
  "skin",
  "allergy",
  "traits",
  "rare_variants",
]

/** Title-case an unknown module key as a last-resort label, e.g.
 * "thrombophilia" → "Thrombophilia". */
function humanizeModule(key: string): string {
  return key
    .split("_")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ")
}

export default function ReportBuilder() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedModules, setSelectedModules] = useState<Set<string>>(new Set())
  const [reportTitle, setReportTitle] = useState("Yeliztli Genomic Report")
  const [previewHtml, setPreviewHtml] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [initialized, setInitialized] = useState(false)

  const iframeRef = useRef<HTMLIFrameElement>(null)

  const summaryQuery = useFindingsSummary(sampleId)
  const generateMutation = useGenerateReport()
  const fhirMutation = useExportFhir()

  // Every module that actually has findings, ordered by MODULE_ORDER where known
  // and appended (alphabetically) otherwise. Driving this off the summary data —
  // not off MODULE_ORDER membership — is what keeps a new or mis-keyed finding
  // module from being silently dropped from the report (#596).
  const availableModules: FindingSummaryItem[] = useMemo(() => {
    const modules = summaryQuery.data?.modules
    if (!modules) return []
    const orderIndex = new Map(MODULE_ORDER.map((key, i) => [key, i]))
    return [...modules].sort((a, b) => {
      const ai = orderIndex.get(a.module) ?? Number.MAX_SAFE_INTEGER
      const bi = orderIndex.get(b.module) ?? Number.MAX_SAFE_INTEGER
      return ai !== bi ? ai - bi : a.module.localeCompare(b.module)
    })
  }, [summaryQuery.data])

  // Auto-select all modules with findings on first load
  useEffect(() => {
    if (!initialized && availableModules.length > 0) {
      setSelectedModules(new Set(availableModules.map((m) => m.module)))
      setInitialized(true)
    }
  }, [initialized, availableModules])

  const toggleModule = useCallback((mod: string) => {
    setSelectedModules((prev) => {
      const next = new Set(prev)
      if (next.has(mod)) {
        next.delete(mod)
      } else {
        next.add(mod)
      }
      return next
    })
  }, [])

  const selectAll = useCallback(() => {
    setSelectedModules(new Set(availableModules.map((m) => m.module)))
  }, [availableModules])

  const clearAll = useCallback(() => {
    setSelectedModules(new Set())
  }, [])

  const selectedCount = selectedModules.size
  const totalFindings = useMemo(() => {
    return availableModules
      .filter((m) => selectedModules.has(m.module))
      .reduce((sum, m) => sum + m.count, 0)
  }, [availableModules, selectedModules])

  const handlePreview = useCallback(async () => {
    if (!sampleId || selectedCount === 0) return
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const html = await fetchReportPreview({
        sample_id: sampleId,
        modules: Array.from(selectedModules),
        title: reportTitle,
      })
      setPreviewHtml(html)
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Preview failed")
    } finally {
      setPreviewLoading(false)
    }
  }, [sampleId, selectedModules, selectedCount, reportTitle])

  const handleDownload = useCallback(() => {
    if (!sampleId || selectedCount === 0) return
    generateMutation.mutate(
      {
        sample_id: sampleId,
        modules: Array.from(selectedModules),
        title: reportTitle,
      },
      {
        onSuccess: (blob) => {
          const url = URL.createObjectURL(blob)
          const a = document.createElement("a")
          a.href = url
          a.download = `yeliztli_report_${sampleId}.pdf`
          document.body.appendChild(a)
          a.click()
          document.body.removeChild(a)
          URL.revokeObjectURL(url)
        },
      },
    )
  }, [sampleId, selectedModules, selectedCount, reportTitle, generateMutation])

  const handleFhirExport = useCallback(() => {
    if (!sampleId) return
    fhirMutation.mutate(
      { sample_id: sampleId, include_all: true },
      {
        onSuccess: (blob) => {
          const url = URL.createObjectURL(blob)
          const a = document.createElement("a")
          a.href = url
          a.download = `yeliztli_${sampleId}.fhir.json`
          document.body.appendChild(a)
          a.click()
          document.body.removeChild(a)
          URL.revokeObjectURL(url)
        },
      },
    )
  }, [sampleId, fhirMutation])

  const closePreview = useCallback(() => {
    setPreviewHtml(null)
    setPreviewError(null)
  }, [])

  // Close preview modal on Escape key
  useEffect(() => {
    if (!previewHtml) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePreview()
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [previewHtml, closePreview])

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Report Builder</h1>
        <PageEmpty icon={FileText} title="Select a sample to build a report." />
      </div>
    )
  }

  // Loading state
  if (summaryQuery.isLoading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Report Builder</h1>
        <PageLoading message="Loading findings..." />
      </div>
    )
  }

  // Error state
  if (summaryQuery.isError) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Report Builder</h1>
        <PageError
          message={summaryQuery.error instanceof Error ? summaryQuery.error.message : "Failed to load findings."}
          onRetry={() => summaryQuery.refetch()}
        />
      </div>
    )
  }

  // No findings available
  if (availableModules.length === 0) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Report Builder</h1>
        <PageEmpty
          icon={FileText}
          title="No analysis findings available."
          description="Run annotation and analysis modules first."
        />
      </div>
    )
  }

  return (
    <div className="p-6">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <FileText className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Report Builder</h1>
          <p className="text-sm text-muted-foreground">
            Select modules to include in your PDF report
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Module selection */}
        <div className="lg:col-span-2 space-y-4">
          {/* Report title input */}
          <div className="rounded-lg border bg-card p-4">
            <label
              htmlFor="report-title"
              className="block text-sm font-medium mb-2"
            >
              Report Title
            </label>
            <input
              id="report-title"
              type="text"
              value={reportTitle}
              onChange={(e) => setReportTitle(e.target.value)}
              className={cn(
                "w-full rounded-md border bg-background px-3 py-2 text-sm",
                "focus:outline-none focus:ring-2 focus:ring-primary/50",
              )}
              placeholder="Yeliztli Genomic Report"
            />
          </div>

          {/* Module selection header */}
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">
              Modules ({selectedCount} of {availableModules.length} selected)
            </h2>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={selectAll}
                className="text-sm text-primary hover:underline"
                aria-label="Select all modules"
              >
                Select all
              </button>
              <span className="text-muted-foreground">·</span>
              <button
                type="button"
                onClick={clearAll}
                className="text-sm text-primary hover:underline"
                aria-label="Clear all modules"
              >
                Clear all
              </button>
            </div>
          </div>

          {/* Module cards grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3" role="group" aria-label="Module selection">
            {availableModules.map((mod) => {
              const isSelected = selectedModules.has(mod.module)
              const displayName = MODULE_DISPLAY_NAMES[mod.module] || humanizeModule(mod.module)
              return (
                <button
                  key={mod.module}
                  type="button"
                  onClick={() => toggleModule(mod.module)}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border p-4 text-left transition-colors",
                    "hover:bg-accent/50",
                    isSelected
                      ? "border-primary bg-primary/5 dark:bg-primary/10"
                      : "border-border bg-card",
                  )}
                  aria-pressed={isSelected}
                  aria-label={`${displayName}: ${mod.count} findings`}
                >
                  {isSelected ? (
                    <CheckSquare className="h-5 w-5 text-primary mt-0.5 flex-shrink-0" />
                  ) : (
                    <Square className="h-5 w-5 text-muted-foreground mt-0.5 flex-shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm">{displayName}</div>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-xs text-muted-foreground">
                        {mod.count} finding{mod.count !== 1 ? "s" : ""}
                      </span>
                      {mod.max_evidence_level != null && mod.max_evidence_level > 0 && (
                        <>
                          <span className="text-xs text-muted-foreground">·</span>
                          <EvidenceStars level={mod.max_evidence_level} />
                        </>
                      )}
                    </div>
                    {mod.top_finding_text && (
                      <p className="text-xs text-muted-foreground mt-1 truncate">
                        {mod.top_finding_text}
                      </p>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* Right column: Actions panel */}
        <div className="space-y-4">
          <div className="rounded-lg border bg-card p-4 sticky top-4">
            <h3 className="font-semibold mb-3">Report Summary</h3>

            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Selected modules</span>
                <span className="font-medium">{selectedCount}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Total findings</span>
                <span className="font-medium">{totalFindings}</span>
              </div>
            </div>

            <div className="border-t mt-4 pt-4 space-y-3">
              {/* Preview button */}
              <button
                type="button"
                onClick={handlePreview}
                disabled={selectedCount === 0 || previewLoading}
                className={cn(
                  "flex w-full items-center justify-center gap-2 rounded-md border px-4 py-2.5 text-sm font-medium transition-colors",
                  selectedCount === 0
                    ? "cursor-not-allowed opacity-50"
                    : "hover:bg-accent",
                )}
                aria-label="Preview report"
              >
                {previewLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
                Preview
              </button>

              {/* Download button */}
              <button
                type="button"
                onClick={handleDownload}
                disabled={selectedCount === 0 || generateMutation.isPending}
                className={cn(
                  "flex w-full items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm font-medium transition-colors",
                  selectedCount === 0
                    ? "cursor-not-allowed bg-primary/50 text-primary-foreground/50"
                    : "bg-primary text-primary-foreground hover:bg-primary/90",
                )}
                aria-label="Download PDF report"
              >
                {generateMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                {generateMutation.isPending ? "Generating…" : "Download PDF"}
              </button>

              {/* FHIR export button */}
              <button
                type="button"
                onClick={handleFhirExport}
                disabled={fhirMutation.isPending}
                className={cn(
                  "flex w-full items-center justify-center gap-2 rounded-md border px-4 py-2.5 text-sm font-medium transition-colors",
                  fhirMutation.isPending
                    ? "cursor-not-allowed opacity-50"
                    : "hover:bg-accent",
                )}
                aria-label="Export FHIR R4 Bundle"
              >
                {fhirMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                {fhirMutation.isPending ? "Exporting…" : "Export FHIR R4"}
              </button>

              {/* Error messages */}
              {fhirMutation.isError && (
                <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertCircle className="h-3 w-3 inline-block mr-1" />
                  {fhirMutation.error instanceof Error
                    ? fhirMutation.error.message
                    : "FHIR export failed"}
                </div>
              )}

              {generateMutation.isError && (
                <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertCircle className="h-3 w-3 inline-block mr-1" />
                  {generateMutation.error instanceof Error
                    ? generateMutation.error.message
                    : "PDF generation failed"}
                </div>
              )}

              {previewError && (
                <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertCircle className="h-3 w-3 inline-block mr-1" />
                  {previewError}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Preview modal */}
      {previewHtml && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          role="dialog"
          aria-label="Report preview"
          aria-modal="true"
        >
          <div className="relative flex h-[90vh] w-[90vw] max-w-5xl flex-col rounded-lg bg-background shadow-xl">
            {/* Modal header */}
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h3 className="font-semibold">Report Preview</h3>
              <button
                type="button"
                onClick={closePreview}
                className="rounded-md p-1 hover:bg-accent"
                aria-label="Close preview"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Preview content */}
            <div className="flex-1 overflow-hidden">
              <iframe
                ref={iframeRef}
                srcDoc={previewHtml}
                title="Report preview"
                className="h-full w-full border-0"
                sandbox="allow-same-origin"
              />
            </div>

            {/* Modal footer */}
            <div className="flex items-center justify-end gap-3 border-t px-4 py-3">
              <button
                type="button"
                onClick={closePreview}
                className="rounded-md border px-4 py-2 text-sm hover:bg-accent"
              >
                Close
              </button>
              <button
                type="button"
                onClick={() => {
                  closePreview()
                  handleDownload()
                }}
                disabled={generateMutation.isPending}
                className={cn(
                  "flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
                  "bg-primary text-primary-foreground hover:bg-primary/90",
                )}
              >
                <Download className="h-4 w-4" />
                Download PDF
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
