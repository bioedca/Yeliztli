/** Searchable drug table for pharmacogenomics (P3-06). */

import { useState, useMemo } from "react"
import { cn } from "@/lib/utils"
import { Search, ChevronRight } from "lucide-react"
import type { DrugListItem } from "@/types/pharmacogenomics"

interface DrugTableProps {
  drugs: DrugListItem[]
  onSelectDrug: (drugName: string) => void
  selectedDrug: string | null
}

function ClassificationBadge({
  classification,
  prescribingGuidanceWithheld = false,
}: {
  classification: string | null
  prescribingGuidanceWithheld?: boolean
}) {
  if (prescribingGuidanceWithheld) {
    return (
      <span
        className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
        title="Clinical recommendation withheld"
      >
        Guidance withheld
      </span>
    )
  }
  if (!classification) return null
  const colors: Record<string, string> = {
    A: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    B: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
    C: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    D: "bg-gray-100 text-gray-800 dark:bg-gray-900/40 dark:text-gray-300",
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        colors[classification] ?? colors.D,
      )}
      title={`CPIC Level ${classification}`}
    >
      {classification}
    </span>
  )
}

export default function DrugTable({ drugs, onSelectDrug, selectedDrug }: DrugTableProps) {
  const [search, setSearch] = useState("")

  const filtered = useMemo(() => {
    if (!search.trim()) return drugs
    const q = search.toLowerCase()
    return drugs.filter(
      (d) =>
        d.drug.toLowerCase().includes(q) ||
        d.genes.some((g) => g.toLowerCase().includes(q)),
    )
  }, [drugs, search])

  return (
    <div className="flex flex-col">
      {/* Search bar */}
      <div className="relative mb-3">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search drugs or genes..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className={cn(
            "w-full rounded-md border bg-background pl-9 pr-3 py-2 text-sm",
            "placeholder:text-muted-foreground",
            "focus:outline-none focus:ring-2 focus:ring-ring",
          )}
          aria-label="Search drugs or genes"
        />
      </div>

      {/* Table */}
      <div className="rounded-lg border bg-card overflow-hidden">
        <table className="w-full text-sm" role="grid" aria-label="Drug interactions table">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Drug</th>
              <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Gene(s)</th>
              <th className="text-center px-4 py-2.5 font-medium text-muted-foreground">CPIC</th>
              <th className="w-8 px-2 py-2.5" aria-hidden="true" />
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                  {search ? "No drugs match your search." : "No drug data available."}
                </td>
              </tr>
            ) : (
              filtered.map((drug) => (
                <tr
                  key={drug.drug}
                  onClick={() => onSelectDrug(drug.drug)}
                  className={cn(
                    "border-b last:border-b-0 cursor-pointer transition-colors",
                    "hover:bg-muted/50",
                    selectedDrug === drug.drug && "bg-primary/5",
                  )}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      onSelectDrug(drug.drug)
                    }
                  }}
                  aria-selected={selectedDrug === drug.drug}
                >
                  <td className="px-4 py-2.5 font-medium text-foreground">{drug.drug}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {drug.genes.join(", ")}
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    <ClassificationBadge
                      classification={drug.classification}
                      prescribingGuidanceWithheld={drug.prescribing_guidance_withheld}
                    />
                  </td>
                  <td className="px-2 py-2.5">
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Count */}
      <p className="text-xs text-muted-foreground mt-2">
        {filtered.length} of {drugs.length} drug{drugs.length !== 1 ? "s" : ""}
      </p>
    </div>
  )
}
