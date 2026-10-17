/** Rare variant finder filter controls (P3-30 + P4-11).
 *
 * Gene panel textarea, AF threshold slider, consequence multi-select,
 * ClinVar significance multi-select, novel toggle, zygosity select.
 *
 * P4-11 additions: saved panel selector, BED file upload, save panel button.
 */

import { useState } from "react"
import { Search, Upload, RotateCcw, Save, Trash2, FolderOpen, FileText } from "lucide-react"
import type { RareVariantFilterRequest } from "@/types/rare-variants"
import type { BedRegion } from "@/types/custom-panels"
import { useCustomPanels, useUploadPanel, useDeletePanel } from "@/api/custom-panels"

/** Default AF threshold (1%) matching backend DEFAULT_AF_THRESHOLD. */
const DEFAULT_AF_THRESHOLD = 0.01

/** Region limit, matching the backend's MAX_PANEL_GENES. */
const MAX_BED_REGIONS = 5000

/** True for a chromosome a sample row can carry (1-22, X, Y, MT; optional "chr"). */
function isSampleChromosome(chrom: string): boolean {
  const token = chrom.trim().toUpperCase().replace(/^CHR/, "")
  if (/^\d+$/.test(token)) {
    const n = Number(token)
    return n >= 1 && n <= 22
  }
  return ["X", "Y", "M", "MT"].includes(token)
}

/** Parse BED3+ text into intervals, splitting fields the way the backend does. */
function parseBedRegions(text: string): { regions: BedRegion[]; skipped: number } {
  const regions: BedRegion[] = []
  let skipped = 0
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith("#") || line.startsWith("track") || line.startsWith("browser")) {
      continue
    }
    let fields = line.split("\t")
    if (fields.length < 3) fields = line.split(/\s+/)
    // Plain digits only, as the backend's int() accepts: Number("") is 0.
    const [startText, endText] = [fields[1]?.trim() ?? "", fields[2]?.trim() ?? ""]
    const start = Number(startText)
    const end = Number(endText)
    if (
      fields.length < 3 ||
      !isSampleChromosome(fields[0]) ||
      !/^\d+$/.test(startText) ||
      !/^\d+$/.test(endText) ||
      end <= start
    ) {
      skipped += 1
      continue
    }
    const name = fields.length > 3 ? fields[3].trim() || null : null
    regions.push({ chrom: fields[0].trim(), start, end, name })
  }
  return { regions, skipped }
}

const CONSEQUENCE_OPTIONS = [
  "frameshift_variant",
  "stop_gained",
  "splice_donor_variant",
  "splice_acceptor_variant",
  "missense_variant",
  "start_lost",
  "stop_lost",
  "inframe_insertion",
  "inframe_deletion",
] as const

const CLINVAR_OPTIONS = [
  "Pathogenic",
  "Likely pathogenic",
  "Uncertain significance",
  "Likely benign",
  "Benign",
] as const

interface FilterPanelProps {
  onSearch: (filters: RareVariantFilterRequest) => void
  isSearching: boolean
}

export default function FilterPanel({ onSearch, isSearching }: FilterPanelProps) {
  const [geneText, setGeneText] = useState("")
  const [afThreshold, setAfThreshold] = useState(DEFAULT_AF_THRESHOLD)
  const [selectedConsequences, setSelectedConsequences] = useState<string[]>([])
  const [selectedClinvar, setSelectedClinvar] = useState<string[]>([])
  const [includeNovel, setIncludeNovel] = useState(true)
  const [zygosity, setZygosity] = useState<string | null>(null)
  const [panelName, setPanelName] = useState("")
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  // BED intervals scope the search by position; the name column is only a label,
  // so it is never copied into the gene list (#2166).
  const [bedRegions, setBedRegions] = useState<BedRegion[] | null>(null)
  const [bedSource, setBedSource] = useState<string | null>(null)
  const [bedNotice, setBedNotice] = useState<string | null>(null)

  // Custom panels hooks (P4-11)
  const panelsQuery = useCustomPanels()
  const uploadMutation = useUploadPanel()
  const deleteMutation = useDeletePanel()

  function handleGeneFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setPendingFile(file)
    const reader = new FileReader()
    reader.onload = () => {
      const text = reader.result as string
      if (file.name.toLowerCase().endsWith(".bed")) {
        const { regions, skipped } = parseBedRegions(text)
        if (regions.length === 0) {
          setBedNotice(`No usable BED regions in ${file.name}.`)
          return
        }
        if (regions.length > MAX_BED_REGIONS) {
          setBedNotice(
            `${file.name} has ${regions.length} regions; the limit is ${MAX_BED_REGIONS}.`,
          )
          return
        }
        setBedRegions(regions)
        setBedSource(file.name)
        setBedNotice(skipped > 0 ? `${skipped} line(s) skipped as unusable.` : null)
      } else {
        // Parse gene symbols: one per line or comma-separated
        const genes = text
          .split(/[\n,]+/)
          .map((g) => g.trim().toUpperCase())
          .filter(Boolean)
        setGeneText(genes.join("\n"))
      }
    }
    reader.onerror = () => {
      console.error("Failed to read gene panel file:", reader.error?.message)
    }
    reader.readAsText(file)
    // Reset input so re-uploading same file works
    e.target.value = ""
  }

  function handleLoadSavedPanel(panelId: number) {
    const panel = panelsQuery.data?.items.find((p) => p.id === panelId)
    if (!panel) return
    setBedNotice(null)
    if (panel.bed_regions && panel.bed_regions.length > 0) {
      setBedRegions(panel.bed_regions)
      setBedSource(panel.name)
      setGeneText("")
    } else {
      setBedRegions(null)
      setBedSource(null)
      setGeneText(panel.gene_symbols.join("\n"))
    }
  }

  function clearBedRegions() {
    setBedRegions(null)
    setBedSource(null)
    setBedNotice(null)
  }

  function handleSavePanel() {
    if (!panelName.trim()) return

    let file: File
    if (bedRegions) {
      // Save the intervals themselves, so the panel is searched by region.
      const bed = bedRegions
        .map((r) => [r.chrom, r.start, r.end, ...(r.name ? [r.name] : [])].join("\t"))
        .join("\n")
      file = new File([bed], "custom_panel.bed", { type: "text/plain" })
    } else {
      // Always use current geneText to ensure edits are captured
      const genes = geneText
        .split(/[\n,]+/)
        .map((g) => g.trim().toUpperCase())
        .filter(Boolean)
      if (genes.length === 0) return
      // A gene list must not keep a .bed name, or the backend parses it as BED.
      const listName =
        pendingFile && !pendingFile.name.toLowerCase().endsWith(".bed")
          ? pendingFile.name
          : "custom_panel.txt"
      file = new File([genes.join("\n")], listName, { type: "text/plain" })
    }
    uploadMutation.mutate(
      { file, name: panelName.trim() },
      {
        onSuccess: () => {
          setPanelName("")
          setShowSaveDialog(false)
          setPendingFile(null)
        },
      },
    )
  }

  function handleDeletePanel(panelId: number) {
    const panel = panelsQuery.data?.items.find((p) => p.id === panelId)
    if (!window.confirm(`Delete panel "${panel?.name ?? panelId}"? This cannot be undone.`)) {
      return
    }
    deleteMutation.mutate(panelId)
  }

  function handleReset() {
    setGeneText("")
    setAfThreshold(DEFAULT_AF_THRESHOLD)
    setSelectedConsequences([])
    setSelectedClinvar([])
    setIncludeNovel(true)
    setZygosity(null)
    setPendingFile(null)
    clearBedRegions()
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const geneSymbols = geneText
      .split(/[\n,]+/)
      .map((g) => g.trim().toUpperCase())
      .filter(Boolean)

    onSearch({
      gene_symbols: geneSymbols.length > 0 ? geneSymbols : null,
      regions: bedRegions ? bedRegions.map(({ chrom, start, end }) => ({ chrom, start, end })) : null,
      af_threshold: afThreshold,
      consequences: selectedConsequences.length > 0 ? selectedConsequences : null,
      clinvar_significance: selectedClinvar.length > 0 ? selectedClinvar : null,
      include_novel: includeNovel,
      zygosity,
    })
  }

  function toggleConsequence(value: string) {
    setSelectedConsequences((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    )
  }

  function toggleClinvar(value: string) {
    setSelectedClinvar((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    )
  }

  const savedPanels = panelsQuery.data?.items ?? []
  const hasGeneText = geneText.trim().length > 0
  const canSavePanel = hasGeneText || bedRegions !== null

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border bg-card p-5 space-y-5"
      data-testid="rare-variant-filter-panel"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Search Filters</h2>
        <button
          type="button"
          onClick={handleReset}
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Reset filters"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset
        </button>
      </div>

      {/* Gene panel */}
      <div>
        <label htmlFor="gene-panel" className="block text-sm font-medium mb-1.5">
          Gene Panel
          <span className="text-muted-foreground font-normal ml-1">(optional)</span>
        </label>

        {/* Saved panels selector (P4-11) */}
        {savedPanels.length > 0 && (
          <div className="mb-2" data-testid="saved-panels">
            <div className="flex items-center gap-1.5 mb-1">
              <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs text-muted-foreground">Saved Panels</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {savedPanels.map((panel) => (
                <div key={panel.id} className="inline-flex items-center gap-1 group">
                  <button
                    type="button"
                    onClick={() => handleLoadSavedPanel(panel.id)}
                    className="inline-flex items-center gap-1.5 rounded-l-md border px-2.5 py-1 text-xs font-medium hover:bg-muted transition-colors"
                    title={`Load ${panel.name} (${panel.gene_count} genes, ${panel.source_type})`}
                    data-testid={`load-panel-${panel.id}`}
                  >
                    <FileText className="h-3 w-3" />
                    {panel.name}
                    <span className="text-muted-foreground">({panel.gene_count})</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeletePanel(panel.id)}
                    className="inline-flex items-center rounded-r-md border border-l-0 px-1.5 py-1 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                    title={`Delete ${panel.name}`}
                    aria-label={`Delete panel ${panel.name}`}
                    data-testid={`delete-panel-${panel.id}`}
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex gap-2">
          <textarea
            id="gene-panel"
            value={geneText}
            onChange={(e) => setGeneText(e.target.value)}
            placeholder="Enter gene symbols (one per line or comma-separated)&#10;e.g. BRCA1, BRCA2, TP53"
            rows={3}
            className="flex-1 rounded-md border bg-background px-3 py-2 text-sm font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-y"
            data-testid="gene-panel-input"
          />
          <div className="flex flex-col gap-1">
            <label
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-xs cursor-pointer hover:bg-muted transition-colors"
              title="Upload gene list (.txt, .csv) or BED file (.bed)"
            >
              <Upload className="h-3.5 w-3.5" />
              Upload
              <input
                type="file"
                accept=".txt,.csv,.tsv,.bed"
                onChange={handleGeneFileUpload}
                className="sr-only"
                data-testid="gene-panel-upload"
              />
            </label>
            {canSavePanel && (
              <button
                type="button"
                onClick={() => setShowSaveDialog(!showSaveDialog)}
                className="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-xs hover:bg-muted transition-colors"
                title={bedRegions ? "Save current BED regions as a panel" : "Save current gene list as a panel"}
                data-testid="save-panel-toggle"
              >
                <Save className="h-3.5 w-3.5" />
                Save
              </button>
            )}
          </div>
        </div>

        {/* Save panel dialog (P4-11) */}
        {showSaveDialog && (
          <div className="mt-2 p-3 rounded-md border bg-muted/30 space-y-2" data-testid="save-panel-dialog">
            <label htmlFor="panel-name" className="block text-xs font-medium">
              Panel Name
            </label>
            <div className="flex gap-2">
              <input
                id="panel-name"
                type="text"
                value={panelName}
                onChange={(e) => setPanelName(e.target.value)}
                placeholder="e.g. My Cancer Panel"
                maxLength={200}
                className="flex-1 rounded-md border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
                data-testid="panel-name-input"
              />
              <button
                type="button"
                onClick={handleSavePanel}
                disabled={!panelName.trim() || uploadMutation.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                data-testid="save-panel-confirm"
              >
                <Save className="h-3 w-3" />
                {uploadMutation.isPending ? "Saving..." : "Save Panel"}
              </button>
            </div>
            {uploadMutation.isError && (
              <p className="text-xs text-destructive">
                {uploadMutation.error instanceof Error
                  ? uploadMutation.error.message
                  : "Failed to save panel."}
              </p>
            )}
          </div>
        )}

        {bedRegions && (
          <div
            className="mt-2 flex items-center justify-between gap-2 rounded-md border bg-muted/30 px-2.5 py-1.5 text-xs"
            data-testid="bed-region-summary"
          >
            <span>
              Searching only {bedRegions.length} BED region{bedRegions.length === 1 ? "" : "s"}
              {bedSource ? ` from ${bedSource}` : ""}; variants outside them are excluded.
            </span>
            <button
              type="button"
              onClick={clearBedRegions}
              className="shrink-0 text-muted-foreground hover:text-foreground"
              data-testid="bed-region-clear"
            >
              Clear regions
            </button>
          </div>
        )}
        {bedNotice && (
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-400" role="status" data-testid="bed-region-notice">
            {bedNotice}
          </p>
        )}

        <p className="text-xs text-muted-foreground mt-1">
          Leave empty to search all genes. Supports .txt, .csv, .tsv, and .bed files; a .bed file
          limits the search to its regions.
        </p>
      </div>

      {/* AF threshold */}
      <div>
        <label htmlFor="af-threshold" className="block text-sm font-medium mb-1.5">
          Max Allele Frequency
        </label>
        <div className="flex items-center gap-3">
          <input
            id="af-threshold"
            type="range"
            min={0}
            max={0.05}
            step={0.001}
            value={afThreshold}
            onChange={(e) => setAfThreshold(Number(e.target.value))}
            className="flex-1"
            data-testid="af-threshold-slider"
          />
          <span className="text-sm font-mono w-16 text-right" data-testid="af-threshold-value">
            {(afThreshold * 100).toFixed(1)}%
          </span>
        </div>
      </div>

      {/* Consequence filter */}
      <div>
        <span className="block text-sm font-medium mb-1.5">Consequence Types</span>
        <div className="flex flex-wrap gap-1.5" data-testid="consequence-filter">
          {CONSEQUENCE_OPTIONS.map((csq) => (
            <button
              key={csq}
              type="button"
              onClick={() => toggleConsequence(csq)}
              className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ${
                selectedConsequences.includes(csq)
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:bg-muted/80"
              }`}
              aria-pressed={selectedConsequences.includes(csq)}
            >
              {csq.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      </div>

      {/* ClinVar significance filter */}
      <div>
        <span className="block text-sm font-medium mb-1.5">ClinVar Significance</span>
        <div className="flex flex-wrap gap-1.5" data-testid="clinvar-filter">
          {CLINVAR_OPTIONS.map((sig) => (
            <button
              key={sig}
              type="button"
              onClick={() => toggleClinvar(sig)}
              className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ${
                selectedClinvar.includes(sig)
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:bg-muted/80"
              }`}
              aria-pressed={selectedClinvar.includes(sig)}
            >
              {sig}
            </button>
          ))}
        </div>
      </div>

      {/* Bottom row: novel toggle + zygosity + search button */}
      <div className="flex flex-wrap items-end gap-4 pt-2">
        {/* Include novel */}
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={includeNovel}
            onChange={(e) => setIncludeNovel(e.target.checked)}
            className="rounded border-input"
            data-testid="include-novel-checkbox"
          />
          Include novel variants
        </label>

        {/* Zygosity */}
        <div className="flex items-center gap-2">
          <label htmlFor="zygosity-select" className="text-sm">
            Zygosity
          </label>
          <select
            id="zygosity-select"
            value={zygosity ?? ""}
            onChange={(e) => setZygosity(e.target.value || null)}
            className="rounded-md border bg-background px-2 py-1 text-sm"
            data-testid="zygosity-select"
          >
            <option value="">Any</option>
            <option value="het">Heterozygous</option>
            <option value="hom_alt">Homozygous</option>
          </select>
        </div>

        {/* Search button */}
        <button
          type="submit"
          disabled={isSearching}
          className="ml-auto inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          data-testid="search-button"
        >
          <Search className="h-4 w-4" />
          {isSearching ? "Searching..." : "Search"}
        </button>
      </div>
    </form>
  )
}
