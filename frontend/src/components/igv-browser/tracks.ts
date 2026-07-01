/**
 * IGV.js track configurations (P2-17).
 *
 * Builds track configs for: ClinVar variants, user sample VCF, gnomAD AF,
 * and build-compatible optional annotations — all backed by local API endpoints
 * with region-based queries. RefSeq genes are injected by IgvBrowser only when a
 * local reference bundle is installed; the remote hg19 fallback uses IGV.js's
 * built-in RefSeq track.
 */
import type { IgvTrack } from "./IgvBrowser"
import { IGV_BROWSER_GENOME_BUILD, type GenomeBuild } from "./genome"

export const ENCODE_CCRES_SOURCE_GENOME_BUILD: GenomeBuild = "GRCh38"

export function isTrackGenomeBuildCompatible(
  trackGenomeBuild: GenomeBuild,
  browserGenomeBuild: GenomeBuild,
): boolean {
  return trackGenomeBuild === browserGenomeBuild
}

// ── ClinVar significance colors ────────────────────────────────────

const CLINVAR_COLORS: Record<string, string> = {
  Pathogenic: "#DC2626",
  "Likely pathogenic": "#EF4444",
  "Uncertain significance": "#F59E0B",
  "Likely benign": "#22C55E",
  Benign: "#16A34A",
  "Conflicting classifications of pathogenicity": "#F97316",
  "Conflicting interpretations of pathogenicity": "#F97316",
}

function normalizeClinVarSignificance(significance: string): string {
  return significance.replace(/_/g, " ").trim()
}

/**
 * ClinVar variants track — VCF format via service sourceType.
 * Color-coded by clinical significance.
 */
export function createClinVarTrack(): IgvTrack {
  return {
    name: "ClinVar Variants",
    type: "variant",
    format: "vcf",
    sourceType: "service",
    url: "/api/igv-tracks/clinvar?chr=$CHR&start=$START&end=$END",
    headerURL: "/api/igv-tracks/clinvar/header",
    visibilityWindow: 1_000_000,
    displayMode: "expanded",
    color: (variant: { info?: Record<string, string> }) => {
      const sig = normalizeClinVarSignificance(variant.info?.["CLNSIG"] ?? "")
      return CLINVAR_COLORS[sig] ?? "#6B7280"
    },
  }
}

/**
 * User sample variants track — VCF format via service sourceType.
 */
export function createSampleVariantsTrack(sampleId: number): IgvTrack {
  return {
    name: "Your Variants",
    type: "variant",
    format: "vcf",
    sourceType: "service",
    url: `/api/igv-tracks/sample/${sampleId}/variants?chr=$CHR&start=$START&end=$END`,
    headerURL: `/api/igv-tracks/sample/${sampleId}/header`,
    visibilityWindow: 500_000,
    displayMode: "collapsed",
    color: "#0D9488",
  }
}

/**
 * gnomAD allele frequency track — JSON features via custom sourceType.
 */
export function createGnomadTrack(): IgvTrack {
  return {
    name: "gnomAD AF",
    type: "annotation",
    sourceType: "custom",
    source: {
      url: "/api/igv-tracks/gnomad?chr=$CHR&start=$START&end=$END",
      contentType: "application/json",
    },
    visibilityWindow: 500_000,
    displayMode: "collapsed",
    height: 40,
    color: "#6366F1",
  }
}

/**
 * ENCODE cCREs track — JSON features via custom sourceType.
 * Color-coded by cCRE classification (PLS=red, ELS=yellow, CTCF=blue).
 */
export function createEncodeCcresTrack(): IgvTrack {
  return {
    name: "ENCODE cCREs",
    type: "annotation",
    sourceType: "custom",
    source: {
      url: "/api/igv-tracks/encode-ccres?chr=$CHR&start=$START&end=$END",
      contentType: "application/json",
    },
    visibilityWindow: 1_000_000,
    displayMode: "expanded",
    height: 50,
    colorBy: "color",
    metadata: {
      sourceGenomeBuild: ENCODE_CCRES_SOURCE_GENOME_BUILD,
    },
  }
}

/**
 * Build the default set of IGV tracks for a given sample.
 *
 * RefSeq genes are not part of this list: the named hg19 fallback gets IGV.js's
 * built-in RefSeq track, while the local-reference path appends a local RefSeq
 * BED track after these sample/reference overlays.
 *
 * @param sampleId - The sample ID for user variant track (omit for no user VCF)
 * @param browserGenomeBuild - Genome build used by the IGV browser coordinates
 */
export function buildDefaultTracks(
  sampleId?: number,
  browserGenomeBuild: GenomeBuild = IGV_BROWSER_GENOME_BUILD,
): IgvTrack[] {
  const tracks: IgvTrack[] = [
    createClinVarTrack(),
    createGnomadTrack(),
  ]

  if (
    isTrackGenomeBuildCompatible(
      ENCODE_CCRES_SOURCE_GENOME_BUILD,
      browserGenomeBuild,
    )
  ) {
    tracks.push(createEncodeCcresTrack())
  }

  if (sampleId !== undefined) {
    // Insert user variants first so they appear above reference tracks
    tracks.unshift(createSampleVariantsTrack(sampleId))
  }

  return tracks
}
