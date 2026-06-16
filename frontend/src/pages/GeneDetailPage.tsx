/**
 * Gene detail page (P3-42).
 *
 * Route: /genes/:symbol?sample_id=N
 *
 * Sections: Protein Viewer (Nightingale), Variants, Population AF,
 * Phenotypes, Literature.
 *
 * Nightingale Web Components mounted via useEffect + ref for protein
 * domain diagrams. Variant positions mapped from HGVS protein notation.
 */

import { useState } from "react"
import { useParams, useSearchParams, Link } from "react-router-dom"
import {
  ArrowLeft,
  Dna,
  Globe,
  BookOpen,
  Shield,
  ExternalLink,
  FlaskConical,
  ChevronDown,
  ChevronUp,
} from "lucide-react"

import { useGeneDetail } from "@/api/gene-detail"
import { parseSampleId, formatAlleleFrequency } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { cn } from "@/lib/utils"
import { NightingaleViewer, PopulationAFChart } from "@/components/gene-detail"
import type { GeneVariantSummary, PubMedArticle } from "@/types/gene-detail"

function renderStars(stars: number | null): string {
  if (stars == null) return ""
  const clamped = Math.max(0, Math.min(4, stars))
  return "\u2605".repeat(clamped) + "\u2606".repeat(4 - clamped)
}

function formatConsequence(consequence: string | null): string {
  if (!consequence) return "—"
  return consequence.replace(/_/g, " ")
}

/* ── Section header ─────────────────────────────────────────────── */

function SectionHeader({ icon: Icon, label }: { icon: typeof Dna; label: string }) {
  return (
    <div className="flex items-center gap-2 mb-3 mt-8 first:mt-0">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </h2>
    </div>
  )
}

/* ── Literature card ────────────────────────────────────────────── */

function LiteratureCard({ article }: { article: PubMedArticle }) {
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
          >
            {expanded ? "Hide abstract" : "Show abstract"}
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
          {expanded && (
            <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
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

/* ── Main page ──────────────────────────────────────────────────── */

export default function GeneDetailPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedVariantRsid, setSelectedVariantRsid] = useState<string | null>(null)

  const { data, isLoading, isError, error, refetch } = useGeneDetail(symbol ?? null, sampleId)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Gene Detail</h1>
        <PageEmpty icon={Dna} title="Select a sample to view gene details." />
      </div>
    )
  }

  // Loading
  if (isLoading) {
    return <PageLoading message="Loading gene detail..." />
  }

  // Error
  if (isError || !data) {
    return (
      <div className="p-6 space-y-4">
        <Link
          to={`/variants?sample_id=${sampleId}`}
          className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Variant Explorer
        </Link>
        <PageError
          message={error instanceof Error ? error.message : "An unexpected error occurred."}
          onRetry={() => refetch()}
        />
      </div>
    )
  }

  const handleVariantClick = (v: GeneVariantSummary) => {
    setSelectedVariantRsid(v.rsid)
  }

  return (
    <div className="p-6 max-w-5xl" data-testid="gene-detail-page">
      {/* Back navigation */}
      <Link
        to={`/variants?sample_id=${sampleId}`}
        className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline mb-4"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Variant Explorer
      </Link>

      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <Dna className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold" data-testid="gene-symbol">
            {data.gene_symbol}
          </h1>
          {data.uniprot && (
            <p className="text-sm text-muted-foreground">
              UniProt:{" "}
              <a
                href={`https://www.uniprot.org/uniprot/${data.uniprot.accession}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                {data.uniprot.accession}
              </a>
              {" · "}
              {data.uniprot.sequence_length} aa
              {data.uniprot.is_cached && (
                <span className="text-xs text-muted-foreground ml-2">(cached)</span>
              )}
            </p>
          )}
          {data.uniprot_error && !data.uniprot && (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              {data.uniprot_error}
            </p>
          )}
        </div>
      </div>

      {/* ── Protein Visualization (Nightingale) ───────────────── */}
      <SectionHeader icon={FlaskConical} label="Protein Structure" />

      {data.uniprot ? (
        <div className="rounded-lg border bg-card p-4">
          <NightingaleViewer
            sequenceLength={data.uniprot.sequence_length}
            domains={data.uniprot.domains}
            features={data.uniprot.features}
            variants={data.variants}
            accession={data.uniprot.accession}
            onVariantClick={handleVariantClick}
          />
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
          {data.uniprot_error ?? "Protein data not available for this gene."}
        </div>
      )}

      {/* ── Variants in Gene ──────────────────────────────────── */}
      <SectionHeader icon={Dna} label={`Variants (${data.variants.length})`} />

      {data.variants.length > 0 ? (
        <div className="rounded-lg border bg-card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="gene-variants-table">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">rsid</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">HGVS Protein</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">Consequence</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">Genotype</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">ClinVar</th>
                  <th className="px-3 py-2 text-right font-medium text-muted-foreground">gnomAD AF</th>
                  <th className="px-3 py-2 text-right font-medium text-muted-foreground">CADD</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.variants.map((v) => (
                  <tr
                    key={v.rsid}
                    className={cn(
                      "hover:bg-accent/50 transition-colors cursor-pointer",
                      selectedVariantRsid === v.rsid && "bg-accent/70",
                    )}
                    onClick={() => handleVariantClick(v)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        handleVariantClick(v)
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-pressed={selectedVariantRsid === v.rsid}
                    data-testid={`variant-row-${v.rsid}`}
                  >
                    <td className="px-3 py-2">
                      <Link
                        to={`/variants/${v.rsid}?sample_id=${sampleId}`}
                        className="font-mono text-xs text-primary hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {v.rsid}
                      </Link>
                      {v.evidence_conflict && (
                        <span className="ml-1 text-amber-500" title="Evidence conflict">
                          ⚠
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                      {v.hgvs_protein ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {formatConsequence(v.consequence)}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {v.genotype ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {v.clinvar_significance ?? "—"}
                      {v.clinvar_review_stars != null && (
                        <span className="ml-1 text-amber-500 text-[10px]">
                          {renderStars(v.clinvar_review_stars)}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs text-muted-foreground">
                      {formatAlleleFrequency(v.gnomad_af_global)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs text-muted-foreground">
                      {v.cadd_phred != null ? v.cadd_phred.toFixed(1) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
          No annotated variants found in this gene for the current sample.
        </div>
      )}

      {/* ── Population Allele Frequency ───────────────────────── */}
      <SectionHeader icon={Globe} label="Population Allele Frequencies" />

      <div className="rounded-lg border bg-card p-4">
        <PopulationAFChart
          data={data.population_af}
          selectedVariant={selectedVariantRsid}
        />
      </div>

      {/* ── Gene-Phenotype Associations ───────────────────────── */}
      <SectionHeader icon={Shield} label={`Phenotypes (${data.phenotypes.length})`} />

      {data.phenotypes.length > 0 ? (
        <div className="space-y-2">
          {data.phenotypes.map((pheno, i) => (
            <div
              key={`${pheno.disease_id ?? pheno.disease_name}-${i}`}
              className="rounded-lg border bg-card p-4"
              data-testid={`phenotype-${i}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h4 className="text-sm font-medium">{pheno.disease_name}</h4>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Source: {pheno.source}
                    {pheno.inheritance && ` · ${pheno.inheritance}`}
                    {pheno.disease_id && ` · ${pheno.disease_id}`}
                  </p>
                </div>
                {pheno.omim_link && (
                  <a
                    href={pheno.omim_link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 text-primary hover:text-primary/80 text-xs"
                  >
                    OMIM <ExternalLink className="inline h-3 w-3" />
                  </a>
                )}
              </div>
              {pheno.hpo_terms && pheno.hpo_terms.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {pheno.hpo_terms.map((term) => (
                    <span
                      key={term}
                      className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                    >
                      {term}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
          No phenotype associations found for this gene.
        </div>
      )}

      {/* ── Literature ────────────────────────────────────────── */}
      <SectionHeader icon={BookOpen} label={`Literature (${data.literature.length})`} />

      {data.literature.length > 0 ? (
        <div className="space-y-2">
          {data.literature.map((article) => (
            <LiteratureCard key={article.pmid} article={article} />
          ))}
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
          No literature found for this gene.
          {data.literature_errors.length > 0 && (
            <p className="text-xs text-amber-700 dark:text-amber-400 mt-1">
              {data.literature_errors[0]}
            </p>
          )}
        </div>
      )}

      {/* Bottom padding */}
      <div className="h-8" />
    </div>
  )
}
