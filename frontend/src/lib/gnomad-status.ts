export type GnomadSourceStatus = string

function isGnomadSourceUncovered(status: GnomadSourceStatus | null | undefined): boolean {
  return status === "source_uncovered"
}

/** gnomAD lists this rsID across several alternate alleles, but the genotype does
 * not identify which one is carried, so no frequency is published (#2171).
 *
 * This is NOT absence: saying "Not in gnomAD" about a variant gnomAD catalogues
 * is a false statement, and it is the reason this status exists rather than the
 * withheld row simply falling through to the absent branch.
 */
export function isGnomadAlleleAmbiguous(status: GnomadSourceStatus | null | undefined): boolean {
  return status === "allele_ambiguous"
}

/** Compact cell/row label when no AF is shown, or null to fall through to the
 * caller's own empty rendering.
 *
 * The table, the side panel and the detail page each special-cased
 * `source_uncovered` inline, which is exactly how `allele_ambiguous` came to be
 * rendered as a blank cell — indistinguishable from absence. One helper, three
 * call sites.
 */
export function gnomadNoFrequencyShortLabel(
  status: GnomadSourceStatus | null | undefined,
): string | null {
  if (isGnomadSourceUncovered(status)) {
    return "Not assessed"
  }
  if (isGnomadAlleleAmbiguous(status)) {
    return "Allele unresolved"
  }
  return null
}

export function gnomadNoFrequencyLabel(
  status: GnomadSourceStatus | null | undefined,
  isNovel = false,
): string {
  if (isGnomadSourceUncovered(status)) {
    return "Not assessed by current gnomAD exome source"
  }
  if (isGnomadAlleleAmbiguous(status)) {
    return "Allele not resolved in gnomAD"
  }
  if (isNovel) {
    return "Novel"
  }
  return "Not in gnomAD"
}

export function gnomadNoFrequencyDetail(
  status: GnomadSourceStatus | null | undefined,
  isNovel = false,
): string {
  if (isGnomadSourceUncovered(status)) {
    return "Not assessed by current gnomAD exome source"
  }
  if (isGnomadAlleleAmbiguous(status)) {
    return (
      "This rsID covers several alternate alleles in gnomAD and your genotype " +
      "does not identify which one you carry, so no frequency is shown"
    )
  }
  if (isNovel) {
    return "Novel - absent from gnomAD and not catalogued in dbSNP/ClinVar"
  }
  return "Not found in gnomAD"
}
