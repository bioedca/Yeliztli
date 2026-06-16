/** Types for the query builder UI (P4-02). */

/** A single rule from react-querybuilder. */
interface RuleModel {
  field: string
  operator: string
  value?: unknown
  disabled?: boolean
}

/** Recursive rule group from react-querybuilder (RuleGroupType). */
export interface RuleGroupModel {
  combinator: string
  rules: Array<RuleGroupModel | RuleModel>
  not?: boolean
}

/** Single variant row in query results. */
export interface QueryVariantRow {
  rsid: string
  chrom: string
  pos: number
  genotype: string | null
  ref: string | null
  alt: string | null
  zygosity: string | null
  gene_symbol: string | null
  transcript_id: string | null
  consequence: string | null
  hgvs_coding: string | null
  hgvs_protein: string | null
  clinvar_significance: string | null
  clinvar_review_stars: number | null
  clinvar_accession: string | null
  clinvar_conditions: string | null
  gnomad_af_global: number | null
  gnomad_af_afr: number | null
  gnomad_af_amr: number | null
  gnomad_af_eas: number | null
  gnomad_af_eur: number | null
  gnomad_af_fin: number | null
  gnomad_af_sas: number | null
  rare_flag: boolean | null
  ultra_rare_flag: boolean | null
  cadd_phred: number | null
  sift_score: number | null
  sift_pred: string | null
  polyphen2_hsvar_score: number | null
  polyphen2_hsvar_pred: string | null
  revel: number | null
  annotation_coverage: number | null
  evidence_conflict: boolean | null
  ensemble_pathogenic: boolean | null
  disease_name: string | null
  inheritance_pattern: string | null
}

/** Paginated query result. */
export interface QueryResultPage {
  items: QueryVariantRow[]
  total_matching: number | null
  next_cursor_chrom: string | null
  next_cursor_pos: number | null
  has_more: boolean
  limit: number
}

/** Metadata about an allowed query field. */
export interface QueryFieldInfo {
  name: string
  type: string
  label: string
}

/** Response for GET /api/query/fields. */
export interface QueryMetaResponse {
  fields: QueryFieldInfo[]
  operators: string[]
}

/** A saved query entry. */
export interface SavedQuery {
  name: string
  filter: RuleGroupModel
  created_at: string
  updated_at: string
}

/** Response for GET /api/saved-queries. */
export interface SavedQueryListResponse {
  queries: SavedQuery[]
}

// ── Export types (P4-05) ─────────────────────────────────────────────

/** Supported export formats for query builder results. */
export type QueryExportFormat = "vcf" | "tsv" | "json" | "csv"

/** Supported export formats for SQL console results (no VCF). */
export type SqlExportFormat = "tsv" | "json" | "csv"

// ── SQL Console types (P4-04) ────────────────────────────────────────

/** Column metadata returned from SQL console. */
interface SqlResultColumn {
  name: string
  type: string | null
}

/** Response for POST /api/query/sql. */
export interface SqlResult {
  columns: SqlResultColumn[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  execution_time_ms: number
}

/** A table in the schema sidebar. */
export interface SchemaTable {
  name: string
  columns: SchemaColumn[]
}

/** A column in a schema table. */
export interface SchemaColumn {
  name: string
  type: string
}
