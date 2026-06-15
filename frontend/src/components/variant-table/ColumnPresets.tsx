/** Column preset selector dropdown with CRUD for custom presets (P1-15c). */

import { useCallback, useEffect, useRef, useState } from "react"
import { Columns3, Check, Plus, Trash2, X } from "lucide-react"
import { useColumnPresets, useCreatePreset, useDeletePreset } from "@/api/columnPresets"
import type { ColumnPreset } from "@/types/variants"

/** Columns always visible regardless of preset. */
const ALWAYS_VISIBLE = new Set(["evidence_conflict", "rsid", "chrom", "pos"])

/** All toggleable column IDs in display order. */
// GRCh38 columns (chrom_grch38, pos_grch38) are excluded — controlled by the
// showGRCh38 toggle in VariantTable, not by preset selection.
const ALL_COLUMN_IDS = [
  "genotype", "ref", "alt", "zygosity", "gene_symbol", "consequence",
  "clinvar_significance", "clinvar_review_stars", "gnomad_af_global",
  "rare_flag", "cadd_phred", "sift_score", "sift_pred",
  "polyphen2_hsvar_score", "polyphen2_hsvar_pred", "revel",
  "annotation_coverage", "ensemble_pathogenic",
]

/** Human-readable labels for columns. */
const COLUMN_LABELS: Record<string, string> = {
  genotype: "Genotype",
  ref: "Ref",
  alt: "Alt",
  zygosity: "Zygosity",
  gene_symbol: "Gene",
  consequence: "Consequence",
  clinvar_significance: "ClinVar",
  clinvar_review_stars: "Review Stars",
  gnomad_af_global: "gnomAD AF",
  rare_flag: "Rare",
  cadd_phred: "CADD",
  sift_score: "SIFT",
  sift_pred: "SIFT Pred",
  polyphen2_hsvar_score: "PolyPhen2",
  polyphen2_hsvar_pred: "PP2 Pred",
  revel: "REVEL",
  annotation_coverage: "Annotations",
  ensemble_pathogenic: "Ensemble",
}

interface ColumnPresetsProps {
  activePreset: string | null
  onPresetChange: (presetName: string | null, columns: string[] | null) => void
}

export { ALWAYS_VISIBLE }

export default function ColumnPresets({ activePreset, onPresetChange }: ColumnPresetsProps) {
  const [open, setOpen] = useState(false)
  const [showCreateDialog, setShowCreateDialog] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const { data: presets } = useColumnPresets()
  const createPreset = useCreatePreset()
  const deletePreset = useDeletePreset()

  // Close dropdown on click outside
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  const handleSelect = useCallback(
    (preset: ColumnPreset | null) => {
      if (preset === null) {
        onPresetChange(null, null)
      } else {
        onPresetChange(preset.name, preset.columns)
      }
      setOpen(false)
    },
    [onPresetChange],
  )

  const handleDelete = useCallback(
    (name: string) => {
      deletePreset.mutate(name, {
        onSuccess: () => {
          if (activePreset === name) {
            onPresetChange(null, null)
          }
        },
      })
    },
    [deletePreset, activePreset, onPresetChange],
  )

  const predefined = presets?.filter((p) => p.predefined) ?? []
  const custom = presets?.filter((p) => !p.predefined) ?? []

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-input bg-background text-foreground hover:bg-accent transition-colors"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Column presets"
      >
        <Columns3 className="h-4 w-4" />
        <span>{activePreset ?? "All Columns"}</span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute top-full left-0 mt-1 min-w-[200px] bg-card border border-border rounded-md shadow-lg z-50"
        >
          {/* All Columns */}
          <button
            type="button"
            role="menuitem"
            onClick={() => handleSelect(null)}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-accent transition-colors"
          >
            {activePreset === null && <Check className="h-3.5 w-3.5 text-primary" />}
            {activePreset !== null && <span className="w-3.5" />}
            All Columns
          </button>

          {/* Predefined presets */}
          <div className="border-t border-border" />
          {predefined.map((preset) => (
            <button
              key={preset.name}
              type="button"
              role="menuitem"
              onClick={() => handleSelect(preset)}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-accent transition-colors"
            >
              {activePreset === preset.name && <Check className="h-3.5 w-3.5 text-primary" />}
              {activePreset !== preset.name && <span className="w-3.5" />}
              {preset.name}
            </button>
          ))}

          {/* Custom presets */}
          {custom.length > 0 && (
            <>
              <div className="border-t border-border" />
              {custom.map((preset) => (
                <div
                  key={preset.name}
                  className="flex items-center hover:bg-accent transition-colors group"
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => handleSelect(preset)}
                    className="flex-1 flex items-center gap-2 px-3 py-2 text-sm text-left"
                  >
                    {activePreset === preset.name && <Check className="h-3.5 w-3.5 text-primary" />}
                    {activePreset !== preset.name && <span className="w-3.5" />}
                    {preset.name}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(preset.name)}
                    className="p-1.5 mr-1 text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                    aria-label={`Delete preset ${preset.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </>
          )}

          {/* Create custom preset */}
          <div className="border-t border-border" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setShowCreateDialog(true)
              setOpen(false)
            }}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-accent transition-colors text-muted-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
            Create Custom Preset...
          </button>
        </div>
      )}

      {/* Create preset dialog */}
      {showCreateDialog && (
        <CreatePresetDialog
          onClose={() => setShowCreateDialog(false)}
          onCreate={(name, columns) => {
            createPreset.mutate(
              { name, columns },
              {
                onSuccess: (data) => {
                  setShowCreateDialog(false)
                  onPresetChange(data.name, data.columns)
                },
              },
            )
          }}
        />
      )}
    </div>
  )
}

function CreatePresetDialog({
  onClose,
  onCreate,
}: {
  onClose: () => void
  onCreate: (name: string, columns: string[]) => void
}) {
  const [name, setName] = useState("")
  const [selected, setSelected] = useState<Set<string>>(new Set(ALL_COLUMN_IDS))

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [onClose])

  const toggleColumn = (colId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(colId)) {
        next.delete(colId)
      } else {
        next.add(colId)
      }
      return next
    })
  }

  const handleSubmit = () => {
    const trimmed = name.trim()
    if (!trimmed || selected.size === 0) return
    onCreate(trimmed, Array.from(selected))
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div
        className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4"
        role="dialog"
        aria-label="Create custom preset"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h3 className="font-medium text-sm">Create Custom Preset</h3>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-4 py-3 space-y-3">
          <div>
            <label htmlFor="preset-name" className="block text-sm font-medium mb-1">
              Preset Name
            </label>
            <input
              id="preset-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Custom Preset"
              className="w-full px-3 py-1.5 text-sm rounded-md border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          <div>
            <p className="text-sm font-medium mb-2">Columns</p>
            <div className="grid grid-cols-2 gap-1 max-h-48 overflow-auto">
              {ALL_COLUMN_IDS.map((colId) => (
                <label
                  key={colId}
                  className="flex items-center gap-2 px-2 py-1 text-sm rounded hover:bg-accent cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(colId)}
                    onChange={() => toggleColumn(colId)}
                    className="rounded"
                  />
                  {COLUMN_LABELS[colId] ?? colId}
                </label>
              ))}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 px-4 py-3 border-t border-border">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded-md border border-input bg-background hover:bg-accent text-foreground"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!name.trim() || selected.size === 0}
            className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </div>
    </div>
  )
}
