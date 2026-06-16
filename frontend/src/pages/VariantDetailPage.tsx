/**
 * Variant detail full page — 6-tab layout (P2-21a).
 *
 * Route: /variants/:rsid?sample_id=N
 *
 * Tabs: Overview | Population | Protein (stub) | Clinical | Literature (stub) | Genome
 */

import { useState, useMemo, useRef, useCallback } from "react"
import { useParams, useSearchParams, Link } from "react-router-dom"
import {
  ArrowLeft,
  MapPin,
  Dna,
  Shield,
  Activity,
  AlertTriangle,
  ExternalLink,
  BookOpen,
  Globe,
  FlaskConical,
  Microscope,
} from "lucide-react"

import { useVariantDetail } from "@/api/variant-detail"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import type {
  VariantDetail,
  TranscriptAnnotation,
  GenePhenotypeRecord,
  EvidenceConflictDetail,
} from "@/types/variant-detail"
import { IgvBrowser } from "@/components/igv-browser"
import { buildDefaultTracks } from "@/components/igv-browser/tracks"
import WatchButton from "@/components/variant-detail/WatchButton"
import { cn } from "@/lib/utils"
import { getClinvarSignificanceBadgeClass } from "@/lib/clinvar-significance"
import { formatClinvarConditionsText } from "@/lib/clinvar-conditions"
import { formatAlleleFrequency } from "@/lib/format"
import { polyphen2Display } from "@/lib/insilico"

/* ------------------------------------------------------------------ */
/*  Shared helpers (reused from side panel)                           */
/* ------------------------------------------------------------------ */

function renderStars(stars: number | null): string {
  if (stars == null) return ""
  const clamped = Math.max(0, Math.min(4, stars))
  return "\u2605".repeat(clamped) + "\u2606".repeat(4 - clamped)
}

function formatConsequence(consequence: string | null): string {
  if (!consequence) return "—"
  return consequence.replace(/_/g, " ")
}

/* ------------------------------------------------------------------ */
/*  Tab definitions                                                    */
/* ------------------------------------------------------------------ */

type TabId = "overview" | "population" | "protein" | "clinical" | "literature" | "genome"

const TABS: { id: TabId; label: string; icon: typeof Dna }[] = [
  { id: "overview", label: "Overview", icon: Dna },
  { id: "population", label: "Population", icon: Globe },
  { id: "protein", label: "Protein", icon: FlaskConical },
  { id: "clinical", label: "Clinical", icon: Shield },
  { id: "literature", label: "Literature", icon: BookOpen },
  { id: "genome", label: "Genome", icon: Microscope },
]

/* ------------------------------------------------------------------ */
/*  Reusable sub-components                                            */
/* ------------------------------------------------------------------ */

function SectionHeader({ icon: Icon, label }: { icon: typeof Dna; label: string }) {
  return (
    <div className="flex items-center gap-2 mb-3 mt-6 first:mt-0">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </h3>
    </div>
  )
}

function DetailRow({
  label,
  value,
  className,
}: {
  label: string
  value: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex justify-between items-baseline py-1 border-b border-border/50 last:border-0", className)}>
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-foreground text-right max-w-[65%]">
        {value ?? "—"}
      </span>
    </div>
  )
}

function ClinVarSignificanceBadge({ significance }: { significance: string | null }) {
  if (!significance) return <span>—</span>
  return (
    <span
      className={cn(
        "inline-block px-2 py-0.5 rounded-full text-xs font-medium",
        getClinvarSignificanceBadgeClass(significance),
      )}
    >
      {significance}
    </span>
  )
}

/* ------------------------------------------------------------------ */
/*  Population frequency bar                                           */
/* ------------------------------------------------------------------ */

interface PopulationBarProps {
  label: string
  code: string
  af: number | null
  maxAF: number
  highlighted?: boolean
}

function PopulationBar({ label, code, af, maxAF, highlighted }: PopulationBarProps) {
  const widthPct = af != null && maxAF > 0 ? Math.max(1, (af / maxAF) * 100) : 0

  return (
    <div className="flex items-center gap-3 py-1.5" data-testid={`pop-bar-${code}`}>
      <span className={cn(
        "text-sm w-28 shrink-0",
        highlighted ? "font-semibold text-primary" : "text-muted-foreground",
      )}>
        {label}
      </span>
      <div className="flex-1 h-5 bg-muted rounded-sm overflow-hidden relative">
        {af != null && (
          <div
            className={cn(
              "h-full rounded-sm transition-all",
              highlighted ? "bg-primary" : "bg-primary/60",
            )}
            style={{ width: `${widthPct}%` }}
          />
        )}
      </div>
      <span className={cn(
        "text-sm w-24 text-right font-mono tabular-nums",
        highlighted ? "font-semibold text-foreground" : "text-muted-foreground",
      )}>
        {/* Per-population AF as a raw fraction — the shared single source of
            truth (#564/#664). The bar *width* above stays proportional; only the
            printed value is unified so it cannot drift to a percentage again. */}
        {formatAlleleFrequency(af)}
      </span>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Tab: Overview                                                      */
/* ------------------------------------------------------------------ */

function OverviewTab({ variant }: { variant: VariantDetail }) {
  const rareLabel = variant.ultra_rare_flag
    ? "Ultra-rare"
    : variant.rare_flag
      ? "Rare"
      : null
  const conditions = formatClinvarConditionsText(variant.clinvar_conditions)

  return (
    <div className="space-y-1" data-testid="tab-overview">
      {/* Genomic location */}
      <SectionHeader icon={MapPin} label="Genomic Location" />
      <DetailRow label="Chromosome" value={variant.chrom} />
      <DetailRow label="Position" value={variant.pos.toLocaleString()} />
      <DetailRow label="Genotype" value={variant.genotype} />
      <DetailRow label="Ref / Alt" value={`${variant.ref ?? "—"} / ${variant.alt ?? "—"}`} />
      <DetailRow label="Zygosity" value={variant.zygosity} />
      <DetailRow label="Consequence" value={formatConsequence(variant.consequence)} />
      {variant.dbsnp_build != null && (
        <DetailRow label="dbSNP Build" value={variant.dbsnp_build} />
      )}

      {/* HGVS */}
      {(variant.hgvs_coding || variant.hgvs_protein) && (
        <>
          <SectionHeader icon={Dna} label="HGVS Notation" />
          {variant.hgvs_coding && <DetailRow label="Coding" value={variant.hgvs_coding} />}
          {variant.hgvs_protein && <DetailRow label="Protein" value={variant.hgvs_protein} />}
        </>
      )}

      {/* Transcript info */}
      <SectionHeader icon={Dna} label="Transcript" />
      <DetailRow label="Gene" value={variant.gene_symbol} />
      <DetailRow label="Transcript" value={variant.transcript_id} />
      {variant.exon_number != null && <DetailRow label="Exon" value={variant.exon_number} />}
      {variant.intron_number != null && <DetailRow label="Intron" value={variant.intron_number} />}
      <DetailRow label="Strand" value={variant.strand} />
      {variant.mane_select && (
        <span className="inline-block mt-1 px-2 py-0.5 text-xs font-medium rounded bg-primary/10 text-primary">
          MANE Select
        </span>
      )}

      {/* ClinVar summary */}
      <SectionHeader icon={Shield} label="ClinVar" />
      <DetailRow
        label="Significance"
        value={<ClinVarSignificanceBadge significance={variant.clinvar_significance} />}
      />
      <DetailRow label="Review Stars" value={renderStars(variant.clinvar_review_stars) || "—"} />
      {conditions && (
        <DetailRow label="Conditions" value={conditions} />
      )}

      {/* Key scores */}
      <SectionHeader icon={Activity} label="Key Scores" />
      <DetailRow label="gnomAD AF" value={
        <span className="flex items-center gap-1.5">
          {formatAlleleFrequency(variant.gnomad_af_global)}
          {rareLabel && (
            <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">
              {rareLabel}
            </span>
          )}
        </span>
      } />
      <DetailRow label="CADD" value={variant.cadd_phred?.toFixed(1)} />
      <DetailRow label="REVEL" value={variant.revel?.toFixed(3)} />
      <DetailRow label="SIFT" value={
        variant.sift_pred
          ? `${variant.sift_pred === "D" ? "Deleterious" : "Tolerated"}${variant.sift_score != null ? ` (${variant.sift_score.toFixed(3)})` : ""}`
          : null
      } />
      <DetailRow label="PolyPhen-2" value={
        variant.polyphen2_hsvar_pred
          ? `${polyphen2Display(variant.polyphen2_hsvar_pred).label}${variant.polyphen2_hsvar_score != null ? ` (${variant.polyphen2_hsvar_score.toFixed(3)})` : ""}`
          : null
      } />
      {variant.ensemble_pathogenic && (
        <div className="mt-2 px-3 py-2 rounded text-sm font-medium bg-red-50 dark:bg-red-950/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800/30">
          Ensemble pathogenic (≥3 independent axes deleterious)
        </div>
      )}

      {/* Evidence conflict */}
      {variant.evidence_conflict_detail?.has_conflict && (
        <EvidenceConflictBox detail={variant.evidence_conflict_detail} />
      )}

      {/* All transcripts */}
      {variant.transcripts.length > 1 && (
        <>
          <SectionHeader icon={Dna} label="All Transcripts" />
          <TranscriptTable transcripts={variant.transcripts} />
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Tab: Population                                                    */
/* ------------------------------------------------------------------ */

function PopulationTab({ variant }: { variant: VariantDetail }) {
  const populations: { label: string; code: string; af: number | null }[] = [
    { label: "Global", code: "global", af: variant.gnomad_af_global },
    { label: "African", code: "afr", af: variant.gnomad_af_afr },
    { label: "Latino/Admixed", code: "amr", af: variant.gnomad_af_amr },
    { label: "East Asian", code: "eas", af: variant.gnomad_af_eas },
    { label: "European", code: "eur", af: variant.gnomad_af_eur },
    { label: "Finnish", code: "fin", af: variant.gnomad_af_fin },
    { label: "South Asian", code: "sas", af: variant.gnomad_af_sas },
  ]

  const maxAF = Math.max(
    ...populations.map((p) => p.af ?? 0),
    0.0001, // minimum scale
  )

  const hasAny = populations.some((p) => p.af != null)

  return (
    <div data-testid="tab-population">
      <SectionHeader icon={Globe} label="gnomAD Population Frequencies" />

      {hasAny ? (
        <>
          <div className="space-y-0.5">
            {populations.map((pop) => (
              <PopulationBar
                key={pop.code}
                label={pop.label}
                code={pop.code}
                af={pop.af}
                maxAF={maxAF}
                highlighted={pop.code === "global"}
              />
            ))}
          </div>

          {variant.gnomad_homozygous_count != null && (
            <div className="mt-4 p-3 rounded-md bg-muted/50 border border-border">
              <DetailRow
                label="Homozygous count"
                value={variant.gnomad_homozygous_count.toLocaleString()}
              />
            </div>
          )}

          {(variant.rare_flag || variant.ultra_rare_flag) && (
            <div className="mt-4 px-3 py-2 rounded-md bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800/30">
              <p className="text-sm text-amber-800 dark:text-amber-200">
                {variant.ultra_rare_flag
                  ? "This variant is ultra-rare (AF < 0.01%)."
                  : "This variant is rare (AF < 1%)."}
              </p>
            </div>
          )}
        </>
      ) : (
        <div className="text-center py-8 text-muted-foreground">
          <Globe className="h-8 w-8 mx-auto mb-2 opacity-40" />
          <p className="text-sm">No population frequency data available for this variant.</p>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Tab: Protein (stub)                                                */
/* ------------------------------------------------------------------ */

function ProteinTab({ variant, sampleId }: { variant: VariantDetail; sampleId: number | null }) {
  return (
    <div data-testid="tab-protein">
      {variant.hgvs_protein && (
        <div className="mb-4">
          <p className="text-sm text-muted-foreground">
            Protein change: <span className="font-mono font-medium text-foreground">{variant.hgvs_protein}</span>
          </p>
        </div>
      )}
      {variant.gene_symbol ? (
        <div className="rounded-lg border bg-card p-6 text-center">
          <FlaskConical className="h-8 w-8 mx-auto mb-3 text-muted-foreground" />
          <h3 className="text-base font-medium text-foreground mb-2">
            Protein Domain Visualization
          </h3>
          <p className="text-sm text-muted-foreground mb-4">
            View the full protein domain diagram with Nightingale,
            variant positions, and domain annotations.
          </p>
          <Link
            to={`/genes/${variant.gene_symbol}${sampleId != null ? `?sample_id=${sampleId}` : ""}`}
            className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline font-medium"
          >
            View full gene detail for {variant.gene_symbol} →
          </Link>
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
          No gene symbol available for this variant.
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Tab: Clinical                                                      */
/* ------------------------------------------------------------------ */

function ClinicalTab({ variant }: { variant: VariantDetail }) {
  const conditions = formatClinvarConditionsText(variant.clinvar_conditions)
  return (
    <div data-testid="tab-clinical">
      {/* ClinVar Record */}
      <SectionHeader icon={Shield} label="ClinVar Record" />
      {variant.clinvar_significance ? (
        <div className="space-y-1">
          <DetailRow
            label="Significance"
            value={<ClinVarSignificanceBadge significance={variant.clinvar_significance} />}
          />
          <DetailRow label="Review Stars" value={
            <span className="text-amber-500">{renderStars(variant.clinvar_review_stars)}</span>
          } />
          {variant.clinvar_accession && (
            <DetailRow label="Accession" value={
              <a
                href={`https://www.ncbi.nlm.nih.gov/clinvar/variation/${variant.clinvar_accession}/`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                {variant.clinvar_accession}
                <ExternalLink className="h-3 w-3" />
              </a>
            } />
          )}
          {conditions && (
            <DetailRow label="Conditions" value={conditions} />
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground py-2">No ClinVar record for this variant.</p>
      )}

      {/* Evidence conflict detail */}
      {variant.evidence_conflict_detail?.has_conflict && (
        <>
          <SectionHeader icon={AlertTriangle} label="Evidence Conflict" />
          <EvidenceConflictBox detail={variant.evidence_conflict_detail} />
        </>
      )}

      {/* In-silico predictions */}
      <SectionHeader icon={Activity} label="In-Silico Predictions" />
      <div className="space-y-1">
        <DetailRow label="CADD (Phred)" value={variant.cadd_phred?.toFixed(1)} />
        <DetailRow label="SIFT" value={
          variant.sift_pred ? (
            <span className={cn(
              variant.sift_pred === "D" ? "text-red-700 dark:text-red-400" : "text-green-700 dark:text-green-400",
            )}>
              {variant.sift_pred === "D" ? "Deleterious" : "Tolerated"}
              {variant.sift_score != null && ` (${variant.sift_score.toFixed(3)})`}
            </span>
          ) : null
        } />
        <DetailRow label="PolyPhen-2" value={
          variant.polyphen2_hsvar_pred ? (
            <span className={polyphen2Display(variant.polyphen2_hsvar_pred).colorClass}>
              {polyphen2Display(variant.polyphen2_hsvar_pred).label}
              {variant.polyphen2_hsvar_score != null && ` (${variant.polyphen2_hsvar_score.toFixed(3)})`}
            </span>
          ) : null
        } />
        <DetailRow label="REVEL" value={variant.revel?.toFixed(3)} />
        <DetailRow label="MutPred2" value={variant.mutpred2?.toFixed(3)} />
        <DetailRow label="VEST4" value={variant.vest4?.toFixed(3)} />
        <DetailRow label="MetaSVM" value={variant.metasvm?.toFixed(3)} />
        <DetailRow label="MetaLR" value={variant.metalr?.toFixed(3)} />
        <DetailRow label="GERP++ RS" value={variant.gerp_rs?.toFixed(2)} />
        <DetailRow label="phyloP" value={variant.phylop?.toFixed(3)} />
        <DetailRow label="MPC" value={variant.mpc?.toFixed(3)} />
        <DetailRow label="PrimateAI" value={variant.primateai?.toFixed(3)} />
      </div>
      {variant.ensemble_pathogenic && (
        <div className="mt-3 px-3 py-2 rounded text-sm font-medium bg-red-50 dark:bg-red-950/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800/30">
          Ensemble pathogenic (≥3 independent axes deleterious)
        </div>
      )}

      {/* Disease Associations */}
      {variant.gene_phenotypes.length > 0 && (
        <>
          <SectionHeader icon={Shield} label="Disease Associations" />
          <DiseaseAssociationList phenotypes={variant.gene_phenotypes} />
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Tab: Literature (stub)                                             */
/* ------------------------------------------------------------------ */

function LiteratureTab({ variant }: { variant: VariantDetail }) {
  return (
    <div className="text-center py-12" data-testid="tab-literature">
      <BookOpen className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
      <h3 className="text-lg font-medium text-foreground mb-2">Literature</h3>
      <p className="text-sm text-muted-foreground max-w-md mx-auto mb-4">
        PubMed literature search with cache-first fetching will be available in Phase 3.
        Abstracts will be keyed by gene and phenotype.
      </p>
      {variant.gene_symbol && (
        <p className="text-sm text-muted-foreground">
          Search will cover: <span className="font-medium text-foreground">{variant.gene_symbol}</span>
          {variant.disease_name && (
            <> + <span className="font-medium text-foreground">{variant.disease_name}</span></>
          )}
        </p>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Tab: Genome                                                        */
/* ------------------------------------------------------------------ */

function GenomeTab({ variant, sampleId }: { variant: VariantDetail; sampleId: number | null }) {
  const igvRef = useRef(null)
  const locus = `chr${variant.chrom}:${Math.max(1, variant.pos - 5000)}-${variant.pos + 5000}`

  const tracks = useMemo(
    () => buildDefaultTracks(sampleId ?? undefined),
    [sampleId],
  )

  return (
    <div data-testid="tab-genome">
      <SectionHeader icon={Microscope} label="Genomic Context" />
      <p className="text-sm text-muted-foreground mb-3">
        ~10 kb window around <span className="font-mono font-medium">{variant.rsid}</span>
        {" "}(chr{variant.chrom}:{variant.pos.toLocaleString()})
      </p>
      <div className="rounded-md border border-border overflow-hidden">
        <IgvBrowser
          ref={igvRef}
          locus={locus}
          tracks={tracks}
          minHeight={400}
          className=""
        />
      </div>
      <div className="mt-3">
        <Link
          to={`/genome-browser?locus=chr${variant.chrom}:${variant.pos}${sampleId != null ? `&sample_id=${sampleId}` : ""}`}
          className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
        >
          Open full browser <ExternalLink className="h-3.5 w-3.5" />
        </Link>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Shared detail components                                           */
/* ------------------------------------------------------------------ */

function EvidenceConflictBox({ detail }: { detail: EvidenceConflictDetail }) {
  return (
    <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-50/50 dark:bg-amber-950/20 p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
        <div className="text-sm leading-relaxed text-amber-900 dark:text-amber-200">
          <p className="font-semibold mb-1">Evidence Conflict</p>
          <p>{detail.summary}</p>
          {detail.deleterious_tools.length > 0 && (
            <p className="mt-2 text-amber-700 dark:text-amber-300">
              Deleterious predictions: {detail.deleterious_tools.join(", ")}
            </p>
          )}
          {detail.clinvar_accession && (
            <a
              href={`https://www.ncbi.nlm.nih.gov/clinvar/variation/${detail.clinvar_accession}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 mt-2 text-amber-700 dark:text-amber-300 hover:underline"
            >
              View ClinVar entry <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </div>
    </div>
  )
}

function TranscriptTable({ transcripts }: { transcripts: TranscriptAnnotation[] }) {
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-muted/50 border-b border-border">
            <th className="text-left px-3 py-2 font-medium text-muted-foreground">Transcript</th>
            <th className="text-left px-3 py-2 font-medium text-muted-foreground">Gene</th>
            <th className="text-left px-3 py-2 font-medium text-muted-foreground">Consequence</th>
            <th className="text-left px-3 py-2 font-medium text-muted-foreground">HGVS (c.)</th>
            <th className="text-left px-3 py-2 font-medium text-muted-foreground">HGVS (p.)</th>
            <th className="text-center px-3 py-2 font-medium text-muted-foreground">MANE</th>
          </tr>
        </thead>
        <tbody>
          {transcripts.map((tx, i) => (
            <tr key={i} className="border-b border-border/50 last:border-0">
              <td className="px-3 py-1.5 font-mono text-xs">{tx.transcript_id ?? "—"}</td>
              <td className="px-3 py-1.5">{tx.gene_symbol ?? "—"}</td>
              <td className="px-3 py-1.5 text-xs">{formatConsequence(tx.consequence)}</td>
              <td className="px-3 py-1.5 font-mono text-xs max-w-48 truncate">{tx.hgvs_coding ?? "—"}</td>
              <td className="px-3 py-1.5 font-mono text-xs max-w-48 truncate">{tx.hgvs_protein ?? "—"}</td>
              <td className="px-3 py-1.5 text-center">
                {tx.mane_select && (
                  <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-primary/10 text-primary">
                    MANE
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DiseaseAssociationList({ phenotypes }: { phenotypes: GenePhenotypeRecord[] }) {
  return (
    <div className="space-y-2">
      {phenotypes.map((gp, i) => (
        <div key={i} className="rounded-md border border-border p-3">
          <p className="font-medium text-sm text-foreground">{gp.disease_name}</p>
          <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
            <span>{gp.source === "omim" ? "OMIM" : "MONDO/HPO"}</span>
            {gp.disease_id && <span>· {gp.disease_id}</span>}
            {gp.inheritance && <span>· {gp.inheritance}</span>}
          </div>
          {gp.hpo_terms && gp.hpo_terms.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {gp.hpo_terms.map((term, j) => (
                <span
                  key={j}
                  className="inline-block px-1.5 py-0.5 text-[10px] rounded bg-muted text-muted-foreground"
                >
                  {term}
                </span>
              ))}
            </div>
          )}
          {gp.omim_link && (
            <a
              href={gp.omim_link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 mt-2 text-xs text-primary hover:underline"
            >
              OMIM <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Page component                                                     */
/* ------------------------------------------------------------------ */

export default function VariantDetailPage() {
  const { rsid } = useParams<{ rsid: string }>()
  const [searchParams] = useSearchParams()
  const rawId = searchParams.get("sample_id")
  const parsed = rawId ? Number(rawId) : NaN
  const sampleId = Number.isFinite(parsed) ? parsed : null

  const [activeTab, setActiveTab] = useState<TabId>("overview")
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

  const { data: variant, isLoading, error, refetch } = useVariantDetail(rsid ?? null, sampleId)

  const handleTabKeyDown = useCallback((e: React.KeyboardEvent, index: number) => {
    const count = TABS.length
    let nextIndex: number | null = null
    if (e.key === "ArrowRight") {
      nextIndex = (index + 1) % count
    } else if (e.key === "ArrowLeft") {
      nextIndex = (index - 1 + count) % count
    } else if (e.key === "Home") {
      nextIndex = 0
    } else if (e.key === "End") {
      nextIndex = count - 1
    }
    if (nextIndex != null) {
      e.preventDefault()
      setActiveTab(TABS[nextIndex].id)
      tabRefs.current[nextIndex]?.focus()
    }
  }, [])

  if (isLoading) {
    return <PageLoading message="Loading variant detail..." />
  }

  if (error || !variant) {
    return (
      <div className="p-6 max-w-2xl mx-auto space-y-4">
        <PageError
          message={error?.message ?? `Variant ${rsid ?? "unknown"} not found.`}
          onRetry={() => refetch()}
        />
        <Link
          to={sampleId != null ? `/variants?sample_id=${sampleId}` : "/variants"}
          className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Variant Explorer
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-40px)]">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center gap-3">
          <Link
            to={sampleId != null ? `/variants?sample_id=${sampleId}` : "/variants"}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            aria-label="Back to Variant Explorer"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-foreground truncate">{variant.rsid}</h1>
              {variant.clinvar_review_stars != null && (
                <span className="text-amber-500 text-lg" title={`${variant.clinvar_review_stars}-star ClinVar review`}>
                  {renderStars(variant.clinvar_review_stars)}
                </span>
              )}
              {variant.mane_select && (
                <span className="px-2 py-0.5 text-xs font-medium rounded bg-primary/10 text-primary">
                  MANE Select
                </span>
              )}
              {sampleId != null && (
                <WatchButton rsid={variant.rsid} sampleId={sampleId} compact />
              )}
            </div>
            <p className="text-sm text-muted-foreground mt-0.5 truncate">
              {[
                variant.gene_symbol,
                variant.transcript_id,
                `chr${variant.chrom}:${variant.pos.toLocaleString()}`,
                formatConsequence(variant.consequence),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-border px-4" role="tablist" aria-label="Variant detail tabs">
        <div className="flex gap-0 -mb-px overflow-x-auto">
          {TABS.map((tab, index) => {
            const Icon = tab.icon
            const isActive = activeTab === tab.id
            return (
              <button
                key={tab.id}
                ref={(el) => { tabRefs.current[index] = el }}
                type="button"
                role="tab"
                id={`tab-${tab.id}`}
                aria-selected={isActive}
                aria-controls={`tabpanel-${tab.id}`}
                tabIndex={isActive ? 0 : -1}
                onClick={() => setActiveTab(tab.id)}
                onKeyDown={(e) => handleTabKeyDown(e, index)}
                className={cn(
                  "flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors",
                  isActive
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:border-border",
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {tab.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Tab content */}
      <div
        className="flex-1 overflow-y-auto p-4 sm:p-6 max-w-4xl"
        role="tabpanel"
        id={`tabpanel-${activeTab}`}
        aria-labelledby={`tab-${activeTab}`}
      >
        {activeTab === "overview" && <OverviewTab variant={variant} />}
        {activeTab === "population" && <PopulationTab variant={variant} />}
        {activeTab === "protein" && <ProteinTab variant={variant} sampleId={sampleId} />}
        {activeTab === "clinical" && <ClinicalTab variant={variant} />}
        {activeTab === "literature" && <LiteratureTab variant={variant} />}
        {activeTab === "genome" && <GenomeTab variant={variant} sampleId={sampleId} />}
      </div>
    </div>
  )
}
