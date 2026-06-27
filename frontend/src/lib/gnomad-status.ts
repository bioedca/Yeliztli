export type GnomadSourceStatus = string

export function isGnomadSourceUncovered(status: GnomadSourceStatus | null | undefined): boolean {
  return status === "source_uncovered"
}

export function gnomadNoFrequencyLabel(
  status: GnomadSourceStatus | null | undefined,
  isNovel = false,
): string {
  if (isGnomadSourceUncovered(status)) {
    return "Not assessed by current gnomAD exome source"
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
  if (isNovel) {
    return "Novel - absent from gnomAD and not catalogued in dbSNP/ClinVar"
  }
  return "Not found in gnomAD"
}
