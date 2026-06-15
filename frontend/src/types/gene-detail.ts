/** Gene detail page API types (P3-42). */

/** A single protein domain from UniProt. */
export interface ProteinDomain {
  type: string
  description: string
  start: number
  end: number
}

/** A protein feature annotation from UniProt. */
export interface ProteinFeature {
  type: string
  description: string
  position: number | null
  start: number | null
  end: number | null
}

/** UniProt protein data for Nightingale rendering. */
interface UniProtData {
  accession: string
  gene_symbol: string
  sequence_length: number
  domains: ProteinDomain[]
  features: ProteinFeature[]
  fetched_at: string | null
  is_cached: boolean
}

/** Gene-phenotype association from MONDO/HPO or OMIM. */
interface GenePhenotypeRecord {
  gene_symbol: string
  disease_name: string
  disease_id: string | null
  source: string
  hpo_terms: string[] | null
  inheritance: string | null
  omim_link: string | null
}

/** A PubMed article summary. */
export interface PubMedArticle {
  pmid: string
  title: string
  abstract: string
  authors: string[]
  journal: string
  year: number | null
  is_stale: boolean
}

/** Summary of a variant in a gene from the sample. */
export interface GeneVariantSummary {
  rsid: string
  chrom: string
  pos: number
  genotype: string | null
  consequence: string | null
  hgvs_protein: string | null
  hgvs_coding: string | null
  clinvar_significance: string | null
  clinvar_review_stars: number | null
  gnomad_af_global: number | null
  cadd_phred: number | null
  evidence_conflict: boolean | null
  annotation_coverage: number | null
}

/** Per-population allele frequency summary. */
export interface PopulationAFSummary {
  rsid: string
  hgvs_protein: string | null
  gnomad_af_global: number | null
  gnomad_af_afr: number | null
  gnomad_af_amr: number | null
  gnomad_af_eas: number | null
  gnomad_af_eur: number | null
  gnomad_af_fin: number | null
  gnomad_af_sas: number | null
}

/** Full gene detail response from GET /api/genes/{symbol}. */
export interface GeneDetailResponse {
  gene_symbol: string
  uniprot: UniProtData | null
  uniprot_error: string | null
  phenotypes: GenePhenotypeRecord[]
  literature: PubMedArticle[]
  literature_errors: string[]
  variants: GeneVariantSummary[]
  population_af: PopulationAFSummary[]
}
