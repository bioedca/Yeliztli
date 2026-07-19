/**
 * Add the active sample to an internal navigation target.
 *
 * Only the global sample context is carried between pages. Route-local state
 * such as a Genome Browser locus or Variant Explorer profile belongs to the
 * destination and must not leak into unrelated pages.
 */
export function withActiveSample(
  path: string,
  sampleId: number | null | undefined,
): string {
  if (!Number.isInteger(sampleId) || (sampleId ?? 0) <= 0) return path

  const hashIndex = path.indexOf('#')
  const hash = hashIndex >= 0 ? path.slice(hashIndex) : ''
  const pathAndSearch = hashIndex >= 0 ? path.slice(0, hashIndex) : path
  const searchIndex = pathAndSearch.indexOf('?')
  const pathname = searchIndex >= 0
    ? pathAndSearch.slice(0, searchIndex)
    : pathAndSearch
  const search = searchIndex >= 0 ? pathAndSearch.slice(searchIndex + 1) : ''
  const params = new URLSearchParams(search)

  params.set('sample_id', String(sampleId))

  return `${pathname}?${params.toString()}${hash}`
}
