/**
 * Nightingale protein domain viewer (P3-42).
 *
 * Mounts EMBL-EBI Nightingale Web Components via useEffect + ref.
 * Shows protein domains, features, and variant positions mapped
 * from genomic coordinates to protein sequence space via HGVS
 * protein notation (e.g., p.Arg175His → position 175).
 */

import { useRef, useEffect, useMemo } from "react"

// Side-effect imports register custom elements globally
import "@nightingale-elements/nightingale-manager"
import "@nightingale-elements/nightingale-navigation"
import "@nightingale-elements/nightingale-sequence"
import "@nightingale-elements/nightingale-track"

import type { ProteinDomain, ProteinFeature, GeneVariantSummary } from "@/types/gene-detail"
import { getClinvarSignificanceHexColor } from "@/lib/clinvar-significance"

/* ── Domain color palette (teal/blue medical theme) ─────────────── */

const DOMAIN_COLORS = [
  "#0D9488", // teal-600
  "#2563EB", // blue-600
  "#7C3AED", // violet-600
  "#059669", // emerald-600
  "#D97706", // amber-600
  "#DC2626", // red-600
  "#4F46E5", // indigo-600
  "#0891B2", // cyan-600
]

function getDomainColor(index: number): string {
  return DOMAIN_COLORS[index % DOMAIN_COLORS.length]
}

/* ── Variant color by ClinVar significance ─────────────────────── */

// Variant dot colour via the shared ClinVar tone→hex classifier, so multi-word
// vocabulary (notably "Conflicting classifications of pathogenicity", which a raw
// `.includes("pathogenic")` mis-coloured red — #799) maps through `uncertain` to
// this viewer's amber "VUS" legend swatch (#D97706), never the pathogenic red.

/* ── Parse protein position from HGVS notation ─────────────────── */

function parseProteinPosition(hgvsProtein: string | null): number | null {
  if (!hgvsProtein) return null
  // Match patterns like p.Arg175His, p.Val600Glu, p.Gln1756Profs*74
  const match = hgvsProtein.match(/p\.[A-Z][a-z]{2}(\d+)/)
  if (match) return parseInt(match[1], 10)
  // Try single-letter notation: p.R175H
  const singleMatch = hgvsProtein.match(/p\.[A-Z](\d+)/)
  if (singleMatch) return parseInt(singleMatch[1], 10)
  return null
}

/* ── Props ──────────────────────────────────────────────────────── */

interface NightingaleViewerProps {
  sequenceLength: number
  domains: ProteinDomain[]
  features: ProteinFeature[]
  variants: GeneVariantSummary[]
  accession: string
  onVariantClick?: (variant: GeneVariantSummary) => void
}

export default function NightingaleViewer({
  sequenceLength,
  domains,
  features,
  variants,
  accession,
  onVariantClick,
}: NightingaleViewerProps) {
  const domainTrackRef = useRef<HTMLElement>(null)
  const featureTrackRef = useRef<HTMLElement>(null)
  const variantTrackRef = useRef<HTMLElement>(null)
  const seqLength = String(sequenceLength)

  // Set domain track data via JS property
  useEffect(() => {
    if (!domainTrackRef.current || domains.length === 0) return
    const domainData = domains.map((d, i) => ({
      accession: `${accession}-domain-${i}`,
      type: d.type,
      tooltipContent: `${d.type}: ${d.description} (${d.start}–${d.end})`,
      color: getDomainColor(i),
      shape: "roundRectangle" as const,
      start: d.start,
      end: d.end,
    }))
    ;(domainTrackRef.current as unknown as { data: unknown[] }).data = domainData
  }, [domains, accession])

  // Set feature track data via JS property
  useEffect(() => {
    if (!featureTrackRef.current || features.length === 0) return
    const featureData = features
      .filter((f) => f.start != null || f.position != null)
      .map((f, i) => ({
        accession: `${accession}-feature-${i}`,
        type: f.type,
        tooltipContent: `${f.type}: ${f.description}`,
        color: "#6B7280",
        shape: "diamond" as const,
        start: f.position ?? f.start ?? 0,
        end: f.position ?? f.end ?? f.start ?? 0,
      }))
    ;(featureTrackRef.current as unknown as { data: unknown[] }).data = featureData
  }, [features, accession])

  // Set variant track data via JS property
  useEffect(() => {
    if (!variantTrackRef.current) return
    const variantData = variants
      .map((v) => {
        const pos = parseProteinPosition(v.hgvs_protein)
        if (pos == null || pos > sequenceLength) return null
        return {
          accession: v.rsid,
          type: "variant",
          tooltipContent: `${v.rsid}: ${v.hgvs_protein ?? "unknown"} (${v.clinvar_significance ?? "no ClinVar"})`,
          color: getClinvarSignificanceHexColor(v.clinvar_significance),
          shape: "circle" as const,
          start: pos,
          end: pos,
        }
      })
      .filter(Boolean)
    ;(variantTrackRef.current as unknown as { data: unknown[] }).data = variantData
  }, [variants, sequenceLength])

  // Memoize variants with valid protein positions
  const mappedVariants = useMemo(
    () => variants.filter((v) => parseProteinPosition(v.hgvs_protein) != null),
    [variants],
  )

  // No protein data
  if (sequenceLength === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
        No protein sequence data available for this gene.
      </div>
    )
  }

  return (
    <div className="space-y-1" data-testid="nightingale-viewer">
      {/* Legend */}
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground mb-2">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: "#DC2626" }} />
          Pathogenic
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: "#EA580C" }} />
          Likely Pathogenic
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: "#D97706" }} />
          VUS
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: "#16A34A" }} />
          Benign
        </span>
      </div>

      {/* Nightingale component stack */}
      <nightingale-manager
        reflected-attributes="length display-start display-end highlight"
        style={{ display: "flex", flexDirection: "column", width: "100%" }}
      >
        {/* Navigation ruler */}
        <div className="mb-1">
          <span className="text-xs text-muted-foreground block mb-0.5">
            Position (aa)
          </span>
          <nightingale-navigation
            length={seqLength}
            height="40"
            display-start="1"
            display-end={seqLength}
          />
        </div>

        {/* Domain track */}
        {domains.length > 0 && (
          <div className="mb-1">
            <span className="text-xs text-muted-foreground block mb-0.5">
              Domains
            </span>
            <nightingale-track
              ref={domainTrackRef}
              length={seqLength}
              height="60"
              display-start="1"
              display-end={seqLength}
              layout="non-overlapping"
            />
          </div>
        )}

        {/* Feature track */}
        {features.length > 0 && (
          <div className="mb-1">
            <span className="text-xs text-muted-foreground block mb-0.5">
              Features
            </span>
            <nightingale-track
              ref={featureTrackRef}
              length={seqLength}
              height="30"
              display-start="1"
              display-end={seqLength}
            />
          </div>
        )}

        {/* Variant track */}
        <div>
          <span className="text-xs text-muted-foreground block mb-0.5">
            Your Variants
          </span>
          <nightingale-track
            ref={variantTrackRef}
            length={seqLength}
            height="40"
            display-start="1"
            display-end={seqLength}
          />
        </div>
      </nightingale-manager>

      {/* Variant list below the viewer */}
      {mappedVariants.length > 0 && (
        <div className="mt-3 space-y-1">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Mapped Variants
          </h4>
          <div className="divide-y divide-border rounded-lg border bg-card">
            {mappedVariants.map((v) => (
                <button
                  key={v.rsid}
                  type="button"
                  className="flex items-center gap-3 w-full px-3 py-2 text-left text-sm hover:bg-accent/50 transition-colors"
                  onClick={() => onVariantClick?.(v)}
                >
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: getClinvarSignificanceHexColor(v.clinvar_significance) }}
                  />
                  <span className="font-mono text-xs">{v.rsid}</span>
                  <span className="text-muted-foreground text-xs truncate">
                    {v.hgvs_protein ?? "—"}
                  </span>
                  <span className="text-muted-foreground text-xs ml-auto">
                    {v.clinvar_significance ?? "—"}
                  </span>
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}
