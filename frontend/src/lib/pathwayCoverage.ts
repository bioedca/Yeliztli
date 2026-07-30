interface PathwayCoverageSummary {
  level: string
  called_snps: number
  total_snps: number
  missing_snps?: string[]
  no_call_snps?: string[]
  indeterminate_snps?: string[]
}

function plural(count: number, noun: string): string {
  return count === 1 ? `${count} ${noun}` : `${count} ${noun}s`
}

function noCallSet(pathway: PathwayCoverageSummary): Set<string> {
  return new Set(pathway.no_call_snps ?? [])
}

function missingSnps(pathway: PathwayCoverageSummary): string[] {
  return pathway.missing_snps ?? []
}

function indeterminateCount(pathway: PathwayCoverageSummary): number {
  return pathway.indeterminate_snps?.length ?? 0
}

function notAssessedLabel(pathway: PathwayCoverageSummary): string {
  const missing = missingSnps(pathway)
  const noCalls = noCallSet(pathway)
  const noCallCount = missing.filter((rsid) => noCalls.has(rsid)).length
  const offChipCount = Math.max(missing.length - noCallCount, 0)
  const parts: string[] = []

  if (offChipCount > 0) {
    parts.push(`${offChipCount} off-chip`)
  }
  if (noCallCount > 0) {
    parts.push(`${noCallCount} no-call`)
  }

  const base = plural(missing.length, "tracked SNP")
  return parts.length > 0 ? `${base} (${parts.join(", ")})` : base
}

/** True when SNPs were observed but every one of them was withheld from
 * interpretation, so nothing was actually scored.
 *
 * `called_snps` counts observed SNPs including indeterminate ones, so deriving a
 * label from it alone renders a clean "Standard" for a pathway where no variant
 * was scored (#2178). Shared by every surface — traits and fitness cards here,
 * the report/export/SVG paths via `backend.analysis.pathway_coverage` — so one
 * result cannot carry conflicting labels depending on where it is rendered.
 */
function pathwayNothingInterpreted(pathway: PathwayCoverageSummary): boolean {
  return pathway.called_snps > 0 && pathway.called_snps - indeterminateCount(pathway) <= 0
}

export function pathwayLevelDisplayLabel(
  pathway: PathwayCoverageSummary,
  defaultLabel: string,
): string {
  if (pathway.level !== "Standard") {
    return defaultLabel
  }
  if (pathwayNothingInterpreted(pathway)) {
    return "Not Assessed"
  }
  if (missingSnps(pathway).length === 0) {
    return defaultLabel
  }
  return pathway.called_snps === 0 ? "Not Assessed" : "Tested Standard"
}

export function pathwayCoverageCaveat(pathway: PathwayCoverageSummary): string | null {
  // "based on interpreted SNPs only" presumes at least one interpreted SNP. When
  // none were interpreted the card already reads "Not Assessed" and the
  // indeterminate caveat carries the reason, so add nothing here (#2178).
  if (pathwayNothingInterpreted(pathway)) {
    return null
  }
  if (missingSnps(pathway).length === 0) {
    return null
  }

  const notAssessed = notAssessedLabel(pathway)
  if (pathway.called_snps === 0) {
    return `No tracked SNPs were assessed; ${notAssessed} not assessed.`
  }
  if (pathway.level === "Standard") {
    if (indeterminateCount(pathway) > 0) {
      return `Standard result is based on interpreted SNPs only; ${notAssessed} not assessed.`
    }
    return `No variants of concern among tested SNPs; ${notAssessed} not assessed.`
  }
  return `${pathway.level} result is based on tested SNPs only; ${notAssessed} not assessed.`
}
