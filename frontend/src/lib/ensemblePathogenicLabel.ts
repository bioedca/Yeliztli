function hasAxisCounts(
  deleteriousCount: number | null | undefined,
  totalAssessed: number | null | undefined,
): deleteriousCount is number {
  return (
    typeof deleteriousCount === "number" &&
    Number.isFinite(deleteriousCount) &&
    typeof totalAssessed === "number" &&
    Number.isFinite(totalAssessed) &&
    totalAssessed > 0
  )
}

export function formatEnsemblePathogenicEvidenceLabel(
  deleteriousCount?: number | null,
  totalAssessed?: number | null,
): string {
  const countText = hasAxisCounts(deleteriousCount, totalAssessed)
    ? ` (${deleteriousCount}/${totalAssessed})`
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
