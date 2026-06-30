export function formatZygosityLabel(
  zygosity: string | null | undefined,
  zygosityLabel?: string | null,
): string {
  if (zygosityLabel) return zygosityLabel
  if (zygosity === "hom_alt") return "Homozygous"
  if (zygosity === "het") return "Heterozygous"
  return "—"
}
