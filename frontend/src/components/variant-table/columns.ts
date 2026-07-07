/** Column definitions for the variant table (P1-15a, P2-22).
 *  Source / Concordance columns for merged samples (AncestryDNA Plan §10.7 / Step 71). */

import { createElement } from "react"
import { createColumnHelper } from "@tanstack/react-table"
import {
  CONCORDANCE_LABELS,
  SOURCE_LABELS,
  type ConcordanceTag,
  type SourceTag,
  type VariantRow,
} from "@/types/variants"
import { isGnomadSourceUncovered } from "@/lib/gnomad-status"
import { coverageTooltip, decodeCoverageSources } from "./annotation-coverage"

const col = createColumnHelper<VariantRow>()

interface VariantTableMeta {
  tagColors?: ReadonlyMap<string, string>
}

const GRCH37_COORDINATE_TOOLTIP =
  "Native GRCh37/hg19 coordinate stored for this sample; use these columns with GRCh37/hg19 tools."
const GRCH38_COORDINATE_TOOLTIP =
  "Computational GRCh38/hg38 liftover from the native GRCh37 coordinate; blank means the position could not be lifted over, including MT/mitochondrial variants, which are never lifted."

function coordinateHeader(label: string, title: string) {
  return () =>
    createElement(
      "span",
      {
        title,
        "aria-label": `${label}: ${title}`,
        className: "cursor-help",
      },
      label,
    )
}

/** Pinned conflict flag column — non-hideable per PRD (P2-07, P2-22).
 *  Amber indicator when ClinVar vs in-silico disagreement fires. */
const conflictColumn = col.accessor("evidence_conflict", {
  id: "evidence_conflict",
  header: "",
  size: 36,
  minSize: 36,
  maxSize: 36,
  enableHiding: false,
  cell: (info) => {
    const val = info.getValue()
    if (val === true) {
      return createElement(
        "span",
        {
          className: "text-amber-500 dark:text-amber-400",
          title: "Evidence conflict: ClinVar disagrees with in-silico predictions",
          "aria-label": "Evidence conflict",
          role: "img",
        },
        "\u26A0",
      )
    }
    return ""
  },
})

/** Tag color map for consistent pill rendering. Falls back to gray. */
const TAG_DEFAULT_COLOR = "#6b7280"

export const allColumns = [
  conflictColumn,
  col.accessor("rsid", {
    header: "rsID",
    size: 120,
    cell: (info) => info.getValue(),
  }),
  col.accessor("tags", {
    id: "tags",
    header: "Tags",
    size: 160,
    cell: (info) => {
      const tags = info.getValue()
      if (!tags || tags.length === 0) return ""
      const tagColors = (info.table.options.meta as VariantTableMeta | undefined)?.tagColors
      return createElement(
        "div",
        { className: "flex items-center gap-1 overflow-hidden" },
        ...tags.map((tag) =>
          createElement(
            "span",
            {
              key: tag,
              className:
                "inline-flex items-center px-1.5 py-0.5 text-[11px] font-medium rounded-full text-white truncate max-w-[80px]",
              style: {
                backgroundColor: tagColors?.get(tag) ?? TAG_DEFAULT_COLOR,
              },
              title: tag,
            },
            tag,
          ),
        ),
      )
    },
  }),
  col.accessor("chrom", {
    header: coordinateHeader("Chr (GRCh37)", GRCH37_COORDINATE_TOOLTIP),
    size: 110,
    cell: (info) => info.getValue(),
  }),
  col.accessor("pos", {
    header: coordinateHeader("Position (GRCh37)", GRCH37_COORDINATE_TOOLTIP),
    size: 150,
    cell: (info) => info.getValue()?.toLocaleString() ?? "",
  }),
  col.accessor("genotype", {
    header: "Genotype",
    size: 90,
    cell: (info) => info.getValue(),
  }),
  col.accessor("ref", {
    header: "Ref",
    size: 60,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("alt", {
    header: "Alt",
    size: 60,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("zygosity", {
    header: "Zygosity",
    size: 90,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("gene_symbol", {
    header: "Gene",
    size: 100,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("consequence", {
    header: "Consequence",
    size: 150,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("clinvar_significance", {
    header: "ClinVar",
    size: 140,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("clinvar_review_stars", {
    header: "Review",
    size: 70,
    cell: (info) => {
      const stars = info.getValue()
      if (stars == null) return ""
      const clamped = Math.max(0, Math.min(4, stars))
      return "\u2605".repeat(clamped) + "\u2606".repeat(4 - clamped)
    },
  }),
  col.accessor("gnomad_af_global", {
    header: "gnomAD AF",
    size: 100,
    cell: (info) => {
      const val = info.getValue()
      if (val == null) {
        return isGnomadSourceUncovered(info.row.original.gnomad_source_status)
          ? "Not assessed"
          : ""
      }
      return val < 0.0001 ? val.toExponential(2) : val.toFixed(4)
    },
  }),
  col.accessor("rare_flag", {
    header: "Rare",
    size: 60,
    cell: (info) => (info.getValue() === true ? "Yes" : ""),
  }),
  col.accessor("cadd_phred", {
    header: "CADD",
    size: 70,
    cell: (info) => info.getValue()?.toFixed(1) ?? "",
  }),
  col.accessor("sift_score", {
    header: "SIFT",
    size: 70,
    cell: (info) => info.getValue()?.toFixed(3) ?? "",
  }),
  col.accessor("sift_pred", {
    header: "SIFT Pred",
    size: 90,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("polyphen2_hsvar_score", {
    header: "PolyPhen2",
    size: 90,
    cell: (info) => info.getValue()?.toFixed(3) ?? "",
  }),
  col.accessor("polyphen2_hsvar_pred", {
    header: "PP2 Pred",
    size: 90,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("revel", {
    header: "REVEL",
    size: 70,
    cell: (info) => info.getValue()?.toFixed(3) ?? "",
  }),
  col.accessor("annotation_coverage", {
    // "Annotations" (count of annotation sources), not "Coverage" — the latter
    // reads as sequencing depth-of-coverage and clashes with the merge Source
    // column. The raw value is a bitmask; decode it to a readable count + the
    // per-source list in a tooltip rather than showing raw binary (#580).
    header: "Annotations",
    size: 90,
    cell: (info) => {
      const val = info.getValue()
      if (val == null) return ""
      const sources = decodeCoverageSources(val)
      return createElement(
        "span",
        { title: coverageTooltip(val), className: "tabular-nums" },
        String(sources.length),
      )
    },
  }),
  col.accessor("ensemble_pathogenic", {
    header: "Ensemble",
    size: 80,
    cell: (info) => (info.getValue() === true ? "Path" : ""),
  }),
  col.accessor("chrom_grch38", {
    header: coordinateHeader("Chr (GRCh38)", GRCH38_COORDINATE_TOOLTIP),
    size: 100,
    cell: (info) => info.getValue() ?? "",
  }),
  col.accessor("pos_grch38", {
    header: coordinateHeader("Pos (GRCh38)", GRCH38_COORDINATE_TOOLTIP),
    size: 120,
    cell: (info) => info.getValue()?.toLocaleString() ?? "",
  }),
  /** Merged-sample provenance columns (AncestryDNA Plan §10.7 / Step 71).
   *  Hidden on unmerged samples by the VariantTable visibility wiring;
   *  shown by default when ``useMergeProvenance`` resolves to 200. */
  col.accessor("source", {
    id: "source",
    header: "Source",
    size: 80,
    cell: (info) => {
      const value = info.getValue() as SourceTag | "" | undefined
      if (!value) return ""
      return SOURCE_LABELS[value]
    },
  }),
  col.accessor("concordance", {
    id: "concordance",
    header: "Concordance",
    size: 120,
    cell: (info) => {
      const value = info.getValue() as ConcordanceTag | "" | undefined
      if (!value) return ""
      return CONCORDANCE_LABELS[value]
    },
  }),
]
