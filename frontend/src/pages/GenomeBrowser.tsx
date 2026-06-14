/**
 * Genome Browser page (P2-16, P2-17, P2-18).
 *
 * Full-page IGV.js browser with GRCh37 reference and default tracks.
 * Includes a search bar for gene/rsid/coordinate navigation and
 * wires variant click events for future side panel integration (P2-21).
 *
 * Supports navigation via URL search params:
 *   ?locus=BRCA1  — jump to a gene/region
 *   ?sample_id=1  — load user variant track for the given sample
 */
import { useRef, useMemo, useState, useCallback, type FormEvent } from "react"
import { useSearchParams } from "react-router-dom"
import { Search } from "lucide-react"
import { IgvBrowser } from "@/components/igv-browser"
import type { IgvBrowserHandle, IgvVariantClickEvent } from "@/components/igv-browser"
import { buildDefaultTracks } from "@/components/igv-browser/tracks"
import { parseSampleId } from "@/lib/format"

export default function GenomeBrowser() {
  const [searchParams, setSearchParams] = useSearchParams()
  const locusParam = searchParams.get("locus") ?? "all"
  // The app writes the selected sample as ?sample_id (snake_case) on every page
  // (IndividualSelector); read the same param — via the shared parseSampleId the
  // other 30 views use — so the user's variant track loads from the normal flow,
  // not only from the variant-detail link (#621).
  const sampleId = parseSampleId(searchParams.get("sample_id")) ?? undefined

  const browserRef = useRef<IgvBrowserHandle>(null)
  const [searchValue, setSearchValue] = useState("")
  const [lastClickedVariant, setLastClickedVariant] = useState<IgvVariantClickEvent | null>(null)

  const tracks = useMemo(() => buildDefaultTracks(sampleId), [sampleId])

  const handleSearch = useCallback(
    (e: FormEvent) => {
      e.preventDefault()
      const query = searchValue.trim()
      if (!query) return
      browserRef.current?.search(query)
      // Update URL so the locus is shareable/bookmarkable
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set("locus", query)
        return next
      })
    },
    [searchValue, setSearchParams],
  )

  const handleVariantClick = useCallback((variant: IgvVariantClickEvent) => {
    setLastClickedVariant(variant)
  }, [])

  return (
    <div className="flex flex-col h-[calc(100vh-40px)]">
      <div className="px-4 py-3 border-b border-border flex flex-col sm:flex-row sm:items-center gap-2">
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-semibold">Genome Browser</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Interactive genome visualization — GRCh37/hg19
          </p>
        </div>
        {/* IGV search bar (P2-18) */}
        <form
          onSubmit={handleSearch}
          className="flex items-center gap-2"
          role="search"
          aria-label="Navigate to genomic locus"
        >
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
            <input
              type="text"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              placeholder="Gene, rsid, or coordinates..."
              className="h-8 w-56 pl-8 pr-3 text-sm rounded-md border border-input bg-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
              aria-label="Genomic locus search"
              data-testid="igv-search-input"
            />
          </div>
          <button
            type="submit"
            className="h-8 px-3 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            data-testid="igv-search-button"
          >
            Go
          </button>
        </form>
      </div>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard-accessible (axe: scrollable-region-focusable) */}
      <div className="flex-1 overflow-auto p-4" role="region" aria-label="Genome browser" tabIndex={0}>
        <IgvBrowser
          ref={browserRef}
          locus={locusParam}
          tracks={tracks}
          onVariantClick={handleVariantClick}
          className="rounded-md border border-border overflow-hidden"
          minHeight={600}
        />
      </div>
      {/* Variant click indicator — will be replaced by side panel in P2-21 */}
      {lastClickedVariant && (
        <div
          className="px-4 py-2 border-t border-border bg-muted/50 text-sm flex items-center gap-4"
          data-testid="variant-click-indicator"
        >
          <span className="font-medium">Selected variant:</span>
          <span>
            {lastClickedVariant.chr}:{lastClickedVariant.pos}
          </span>
          {lastClickedVariant.id && (
            <span className="text-muted-foreground">{lastClickedVariant.id}</span>
          )}
          <span className="text-muted-foreground">
            {lastClickedVariant.ref} &rarr; {lastClickedVariant.alt}
          </span>
          <button
            type="button"
            onClick={() => setLastClickedVariant(null)}
            className="ml-auto text-xs text-muted-foreground hover:text-foreground"
            aria-label="Dismiss variant selection"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  )
}
