import { useState } from "react"
import { ChevronDown, ChevronUp, ExternalLink } from "lucide-react"

import type { PubMedArticle } from "@/types/gene-detail"

export default function LiteratureCard({ article }: { article: PubMedArticle }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-lg border bg-card p-4" data-testid={`pubmed-${article.pmid}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-sm font-medium leading-snug">{article.title}</h4>
          <p className="text-xs text-muted-foreground mt-1">
            {article.authors.slice(0, 3).join(", ")}
            {article.authors.length > 3 && " et al."}
            {article.journal && ` · ${article.journal}`}
            {article.year && ` (${article.year})`}
          </p>
        </div>
        <a
          href={`https://pubmed.ncbi.nlm.nih.gov/${article.pmid}/`}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 text-primary hover:text-primary/80"
          aria-label={`Open PubMed ${article.pmid}`}
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>
      {article.abstract && (
        <>
          <button
            type="button"
            className="flex items-center gap-1 text-xs text-primary mt-2 hover:underline"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
            aria-controls={`pubmed-abstract-${article.pmid}`}
          >
            {expanded ? "Hide abstract" : "Show abstract"}
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
          {expanded && (
            <p
              id={`pubmed-abstract-${article.pmid}`}
              className="text-xs text-muted-foreground mt-2 leading-relaxed"
            >
              {article.abstract}
            </p>
          )}
        </>
      )}
      {article.is_stale && (
        <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
          Cached — may not reflect latest publications.
        </p>
      )}
    </div>
  )
}
