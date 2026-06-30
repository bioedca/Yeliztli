export function formatZygosityLabel(
  zygosity: string | null | undefined,
  zygosityLabel?: string | null,
): string {
  if (zygosityLabel) return zygosityLabel
  if (zygosity) return "—"
  return "—"
}
