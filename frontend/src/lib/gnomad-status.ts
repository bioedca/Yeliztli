export type GnomadSourceStatus = string

function isGnomadSourceUncovered(status: GnomadSourceStatus | null | undefined): boolean {
  return status === "source_uncovered"
}

/** gnomAD lists this rsID across several alternate alleles and no single frequency
 * can be attributed to the call -- it carries more than one of them, carries none,
 * or the alleles cannot be matched -- so none is published (#2171).
 *
 * This is NOT absence: saying "Not in gnomAD" about a variant gnomAD catalogues
 * is a false statement, and it is the reason this status exists rather than the
 * withheld row simply falling through to the absent branch.
 */
/** gnomAD lists this rsID, but only at other coordinates (#2214 review).
 *
 * Distinct from allele ambiguity: the alleles may be identical and it is the
 * COORDINATE that disagrees, so the "several alternate alleles" wording would be
 * false here and would hide a build/mapping mismatch.
 */
/** Several of the sample's own calls collapse onto this rsID at positions gnomAD
 * DOES list (#2214 review).
 *
 * Neither the alleles nor the coordinates are at fault, so both of the other
 * explanations would be false: the limit is that one per-rsID result cannot
 * carry a frequency for two different calls.
 */
/** gnomAD's frequency is for a different alternate allele than the one this row
 * records (#2214 review).
 *
 * The genotype DID identify an allele, so the allele-ambiguity wording would be
 * false: the disagreement is between the frequency source and the source that
 * supplied this variant's identity.
 */
function isGnomadAlleleMismatch(status: GnomadSourceStatus | null | undefined): boolean {
  return status === "allele_mismatch"
}

function isGnomadAliasUnresolved(status: GnomadSourceStatus | null | undefined): boolean {
  return status === "alias_unresolved"
}

function isGnomadLocusUnresolved(status: GnomadSourceStatus | null | undefined): boolean {
  return status === "locus_unresolved"
}

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
  if (isGnomadAlleleMismatch(status)) {
    return "Other allele"
  }
  if (isGnomadAliasUnresolved(status)) {
    return "Shared rsID"
  }
  if (isGnomadLocusUnresolved(status)) {
    return "Position unmatched"
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
  if (isGnomadAlleleMismatch(status)) {
    return "gnomAD frequency is for a different allele"
  }
  if (isGnomadAliasUnresolved(status)) {
    return "Shared rsID across positions"
  }
  if (isGnomadLocusUnresolved(status)) {
    return "Position not matched in gnomAD"
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
  if (isGnomadAlleleMismatch(status)) {
    return (
      "gnomAD's frequency for this rsID describes a different alternate allele " +
      "from the one recorded for this variant, so it is not shown"
    )
  }
  if (isGnomadAliasUnresolved(status)) {
    return (
      "More than one call in your file uses this rsID at different positions, " +
      "so a single frequency cannot describe them all"
    )
  }
  if (isGnomadLocusUnresolved(status)) {
    return (
      "gnomAD lists this rsID only at other genomic positions, so no frequency " +
      "is shown for the position on your chip"
    )
  }
  if (isGnomadAlleleAmbiguous(status)) {
    return (
      "This rsID covers several alternate alleles in gnomAD and no single " +
      "frequency can be attributed to your call, so none is shown"
    )
  }
  if (isNovel) {
    return "Novel - absent from gnomAD and not catalogued in dbSNP/ClinVar"
  }
  return "Not found in gnomAD"
}
