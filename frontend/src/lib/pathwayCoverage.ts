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

export function pathwayLevelDisplayLabel(
  pathway: PathwayCoverageSummary,
  defaultLabel: string,
): string {
  if (pathway.level !== "Standard" || missingSnps(pathway).length === 0) {
    return defaultLabel
  }
  return pathway.called_snps === 0 ? "Not Assessed" : "Tested Standard"
}

export function pathwayCoverageCaveat(pathway: PathwayCoverageSummary): string | null {
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
