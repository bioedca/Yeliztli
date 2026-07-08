function hasAxisCounts(
  counts: readonly [number | null | undefined, number | null | undefined],
): counts is readonly [number, number] {
  const [deleteriousCount, totalAssessed] = counts
  return (
    typeof deleteriousCount === "number" &&
    Number.isFinite(deleteriousCount) &&
    typeof totalAssessed === "number" &&
    Number.isFinite(totalAssessed) &&
    totalAssessed > 0 &&
    deleteriousCount >= 0 &&
    deleteriousCount <= totalAssessed
  )
}

export function formatEnsemblePathogenicEvidenceLabel(
  deleteriousCount?: number | null,
  totalAssessed?: number | null,
): string {
  const counts = [deleteriousCount, totalAssessed] as const
  const countText = hasAxisCounts(counts)
    ? ` (${counts[0]}/${counts[1]})`
    : ""

  return `strict majority of assessed independent axes deleterious${countText}`
}

export function formatEnsemblePathogenicBadgeLabel(
  deleteriousCount?: number | null,
  totalAssessed?: number | null,
): string {
  return `Ensemble pathogenic: ${formatEnsemblePathogenicEvidenceLabel(
    deleteriousCount,
    totalAssessed,
  )}`
}

export function formatEnsemblePathogenicStatus(
  ensemblePathogenic: boolean | null | undefined,
  deleteriousCount?: number | null,
  totalAssessed?: number | null,
): string {
  if (!ensemblePathogenic) return "No"
  return `Yes - ${formatEnsemblePathogenicEvidenceLabel(deleteriousCount, totalAssessed)}`
}
