/** Decode the `annotation_coverage` bitmask into annotation-source labels.
 *
 * Each bit of `annotated_variants.annotation_coverage` records whether one
 * annotation source matched the variant. The canonical bit definitions live in
 * the backend (`backend/analysis/provenance.py::_COVERAGE_BITS`, mirroring
 * `backend/annotation/engine.py`). This list is the reader-facing copy; a
 * cross-stack parity test (`tests/backend/test_annotation_coverage_legend_parity.py`)
 * asserts it stays byte-identical to the backend so the two cannot silently
 * drift. Order is ascending bit value.
 */
export const COVERAGE_BITS: ReadonlyArray<readonly [number, string]> = [
  [0b0000001, "VEP"],
  [0b0000010, "ClinVar"],
  [0b0000100, "gnomAD"],
  [0b0001000, "dbNSFP"],
  [0b0010000, "gene_phenotype"],
  [0b0100000, "GWAS"],
  [0b1000000, "CPIC"],
  [0b10000000, "AlphaMissense"],
]

/** Decode an `annotation_coverage` bitmask into the list of source labels
 * whose bit is set, in ascending bit order. Returns `[]` for null/0. */
export function decodeCoverageSources(mask: number | null | undefined): string[] {
  if (!mask) return []
  return COVERAGE_BITS.filter(([bit]) => (mask & bit) !== 0).map(([, label]) => label)
}

/** Human-readable tooltip for an `annotation_coverage` bitmask. */
export function coverageTooltip(mask: number | null | undefined): string {
  const sources = decodeCoverageSources(mask)
  return sources.length > 0
    ? `Annotation sources (${sources.length}): ${sources.join(", ")}`
    : "No annotation sources covered this variant"
}
