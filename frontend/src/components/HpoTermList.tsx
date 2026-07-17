import { useId, useState } from "react"

const DEFAULT_VISIBLE_TERM_COUNT = 8

interface HpoTermDetail {
  id: string
  name: string | null
}

interface HpoTermListProps {
  termIds: string[] | null
  termDetails?: HpoTermDetail[] | null
  visibleTermCount?: number
}

function mergeHpoTerms(
  termIds: string[] | null,
  termDetails: HpoTermDetail[] | null | undefined,
): HpoTermDetail[] {
  const detailsById = new Map<string, HpoTermDetail>()
  for (const term of termDetails ?? []) {
    if (!detailsById.has(term.id) || (!detailsById.get(term.id)?.name && term.name)) {
      detailsById.set(term.id, term)
    }
  }

  const orderedIds = [
    ...(termIds ?? []),
    ...(termDetails ?? []).map((term) => term.id),
  ]
  const seen = new Set<string>()

  return orderedIds.flatMap((id) => {
    if (seen.has(id)) return []
    seen.add(id)
    return [detailsById.get(id) ?? { id, name: null }]
  })
}

/** Render HPO labels first while retaining accessions and legacy API fallback. */
export default function HpoTermList({
  termIds,
  termDetails,
  visibleTermCount = DEFAULT_VISIBLE_TERM_COUNT,
}: HpoTermListProps) {
  const [expanded, setExpanded] = useState(false)
  const listId = useId()
  const terms = mergeHpoTerms(termIds, termDetails)

  if (terms.length === 0) return null

  const hasOverflow = terms.length > visibleTermCount
  const visibleTerms = expanded ? terms : terms.slice(0, visibleTermCount)
  const hiddenCount = terms.length - visibleTermCount

  return (
    <div className="mt-2">
      <ul id={listId} className="flex flex-wrap gap-1" aria-label="Human Phenotype Ontology terms">
        {visibleTerms.map((term) => {
          const name = term.name?.trim()
          return (
            <li
              key={term.id}
              className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
            >
              {name ? `${name} (${term.id})` : term.id}
            </li>
          )
        })}
      </ul>
      {hasOverflow && (
        <button
          type="button"
          className="mt-2 text-xs text-primary hover:underline"
          aria-controls={listId}
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Show fewer HPO terms" : `Show ${hiddenCount} more HPO terms`}
        </button>
      )}
    </div>
  )
}
