/**
 * Command palette (P4-26e).
 *
 * cmdk-based palette triggered by Cmd+K / Ctrl+K.
 * Supports:
 *   - Page navigation: jump to any sidebar page
 *   - IGV navigation: gene symbol, rsid, or genomic coordinates
 *   - Variant search: prefix search on rsid or gene symbol (API-backed)
 *   - Sample switching: switch between loaded samples
 *
 * Search + navigation only — no destructive or state-changing actions.
 */
import { useState, useCallback, useDeferredValue } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { Command } from "cmdk"
import * as Dialog from "@radix-ui/react-dialog"
import { Dna, MapPin, ArrowRightLeft } from "lucide-react"
import { cn } from "@/lib/utils"
import { isGenomicQuery } from "@/lib/genomic-query"
import { navRoutes } from "@/lib/nav-routes"
import { useVariantSearch } from "@/api/variants"
import { useSamples } from "@/api/samples"
import { parseSampleId } from "@/lib/format"

export interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export default function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState("")
  const deferredSearch = useDeferredValue(search)

  const activeSampleId = parseSampleId(searchParams.get("sample_id"))
  const { data: samples } = useSamples()
  const { data: variantResults } = useVariantSearch(activeSampleId, deferredSearch)

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) setSearch("")
      onOpenChange(next)
    },
    [onOpenChange],
  )

  const navigateToLocus = useCallback(
    (locus: string) => {
      navigate(`/genome-browser?locus=${encodeURIComponent(locus.trim())}`)
      handleOpenChange(false)
    },
    [navigate, handleOpenChange],
  )

  const navigateToPage = useCallback(
    (path: string) => {
      navigate(path)
      handleOpenChange(false)
    },
    [navigate, handleOpenChange],
  )

  const navigateToVariant = useCallback(
    (rsid: string) => {
      const params = new URLSearchParams(searchParams)
      navigate(`/variants/${rsid}?${params}`)
      handleOpenChange(false)
    },
    [navigate, searchParams, handleOpenChange],
  )

  const switchSample = useCallback(
    (sampleId: number) => {
      const params = new URLSearchParams(searchParams)
      params.set("sample_id", String(sampleId))
      setSearchParams(params)
      handleOpenChange(false)
    },
    [searchParams, setSearchParams, handleOpenChange],
  )

  const showGenomicAction = search.trim().length > 0 && isGenomicQuery(search)
  const hasVariantResults = variantResults && variantResults.length > 0
  const otherSamples = samples?.filter((s) => s.id !== activeSampleId) ?? []

  return (
    <Command.Dialog
      open={open}
      onOpenChange={handleOpenChange}
      label="Command Menu"
      className={cn(
        "fixed inset-0 z-50",
        "[&_[cmdk-overlay]]:fixed [&_[cmdk-overlay]]:inset-0 [&_[cmdk-overlay]]:bg-black/50",
      )}
    >
      <Dialog.Title className="sr-only">Command Menu</Dialog.Title>
      <Dialog.Description className="sr-only">
        Search pages, variants, genes, or switch samples
      </Dialog.Description>
      <div
        data-cmdk-overlay=""
        className="fixed inset-0 bg-black/50"
        aria-hidden="true"
        onClick={() => handleOpenChange(false)}
      />
      <div className="fixed top-[20%] left-1/2 -translate-x-1/2 w-full max-w-lg z-50">
        <div className="bg-background border border-border rounded-lg shadow-lg overflow-hidden">
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Search pages, variants, genes, or samples..."
            className="w-full px-4 py-3 text-sm bg-transparent border-b border-border outline-none placeholder:text-muted-foreground"
            data-testid="command-palette-input"
          />
          <Command.List className="max-h-72 overflow-y-auto p-2">
            <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
              No results found.
            </Command.Empty>

            {showGenomicAction && (
              <Command.Group heading="Genome Browser">
                <Command.Item
                  value={`navigate-igv-${search.trim()}`}
                  onSelect={() => navigateToLocus(search)}
                  className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground"
                  data-testid="command-palette-igv-item"
                >
                  <MapPin className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span>
                    Jump to <strong>{search.trim()}</strong> in Genome Browser
                  </span>
                </Command.Item>
              </Command.Group>
            )}

            {hasVariantResults && (
              <Command.Group heading="Variants">
                {variantResults.map((v) => (
                  <Command.Item
                    key={v.rsid}
                    value={`variant-${v.rsid}`}
                    onSelect={() => navigateToVariant(v.rsid)}
                    className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground"
                    data-testid="command-palette-variant-item"
                  >
                    <Dna className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="font-mono">{v.rsid}</span>
                    {v.gene_symbol && (
                      <span className="text-muted-foreground text-xs">{v.gene_symbol}</span>
                    )}
                    {v.clinvar_significance && (
                      <span className="ml-auto text-xs text-muted-foreground truncate max-w-[140px]">
                        {v.clinvar_significance}
                      </span>
                    )}
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            <Command.Group heading="Pages">
              {navRoutes.map(({ to, icon: Icon, label }) => (
                <Command.Item
                  key={to}
                  value={label}
                  onSelect={() => navigateToPage(to)}
                  className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground"
                >
                  <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span>{label}</span>
                </Command.Item>
              ))}
            </Command.Group>

            {otherSamples.length > 0 && (
              <Command.Group heading="Switch Sample">
                {otherSamples.map((sample) => (
                  <Command.Item
                    key={sample.id}
                    value={`sample-${sample.name}`}
                    onSelect={() => switchSample(sample.id)}
                    className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground"
                    data-testid="command-palette-sample-item"
                  >
                    <ArrowRightLeft className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span>{sample.name}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}
          </Command.List>
        </div>
      </div>
    </Command.Dialog>
  )
}
