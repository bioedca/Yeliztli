"""SQLAlchemy Core table definitions for Yeliztli.

All tables are defined as SQLAlchemy Core Table objects — no ORM.
Two MetaData instances exist:

- ``reference_metadata``: Tables in the shared reference.db
  (Alembic-managed, one file for all users).
- ``sample_metadata_obj``: Tables in per-sample databases
  (created programmatically at runtime, one file per sample).

Import individual tables or entire metadata objects as needed::

    from backend.db.tables import clinvar_variants, raw_variants
    from backend.db.tables import reference_metadata, sample_metadata_obj
"""

import sqlalchemy as sa

# ═══════════════════════════════════════════════════════════════════════
# Reference DB (reference.db) — Alembic-managed
# ═══════════════════════════════════════════════════════════════════════

reference_metadata = sa.MetaData()

# ── Individuals (sample aggregation; AncestryDNA Plan §9.2) ────────────

individuals = sa.Table(
    "individuals",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column("notes", sa.Text, server_default=""),
    sa.Column(
        "biological_sex",
        sa.Text,
        comment="'XX' | 'XY' | NULL — inferred or user-set",
    ),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime),
)

# ── Sample Registry ────────────────────────────────────────────────────

samples = sa.Table(
    "samples",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("db_path", sa.Text, nullable=False, unique=True),
    sa.Column("file_format", sa.Text),
    sa.Column("file_hash", sa.Text),
    sa.Column(
        "individual_id",
        sa.Integer,
        sa.ForeignKey("individuals.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime),
)

sa.Index("ix_samples_individual_id", samples.c.individual_id)

# ── Jobs (Huey ↔ FastAPI IPC) ─────────────────────────────────────────

jobs = sa.Table(
    "jobs",
    reference_metadata,
    sa.Column("job_id", sa.Text, primary_key=True),
    sa.Column("sample_id", sa.Integer, nullable=True),
    sa.Column(
        "job_type",
        sa.Text,
        nullable=False,
        comment="e.g. annotation, download, analysis",
    ),
    sa.Column(
        "status",
        sa.Text,
        nullable=False,
        server_default="pending",
        comment="pending | running | complete | partial | failed | cancelled",
    ),
    sa.Column("progress_pct", sa.Float, server_default="0"),
    sa.Column("message", sa.Text, server_default=""),
    sa.Column("error", sa.Text),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
)

# ── Database Versions ──────────────────────────────────────────────────

database_versions = sa.Table(
    "database_versions",
    reference_metadata,
    sa.Column("db_name", sa.Text, primary_key=True),
    sa.Column("version", sa.Text, nullable=False),
    sa.Column("file_path", sa.Text),
    sa.Column("file_size_bytes", sa.Integer),
    sa.Column("downloaded_at", sa.DateTime),
    sa.Column("checksum_sha256", sa.Text),
    # Genome build of the source's coordinates (F30). NULL for build-agnostic /
    # gene-keyed sources (dbsnp, mondo_hpo, omim, lai_bundle, ancestry_pca).
    # dbNSFP is legitimately GRCh38; the rest of the live path is GRCh37.
    sa.Column("genome_build", sa.Text),
)

# ── Auto-Update Settings ───────────────────────────────────────────────

auto_update_settings = sa.Table(
    "auto_update_settings",
    reference_metadata,
    sa.Column("db_name", sa.Text, primary_key=True),
    sa.Column("enabled", sa.Boolean, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

# ── Update History ─────────────────────────────────────────────────────

update_history = sa.Table(
    "update_history",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("db_name", sa.Text, nullable=False),
    sa.Column("previous_version", sa.Text),
    sa.Column("new_version", sa.Text, nullable=False),
    sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("variants_added", sa.Integer, server_default="0"),
    sa.Column("variants_reclassified", sa.Integer, server_default="0"),
    sa.Column("download_size_bytes", sa.Integer),
    sa.Column("duration_seconds", sa.Integer),
)

# ── Download Checkpoints ───────────────────────────────────────────────

downloads = sa.Table(
    "downloads",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("dest_path", sa.Text, nullable=False),
    sa.Column("total_bytes", sa.Integer),
    sa.Column("downloaded_bytes", sa.Integer, server_default="0"),
    sa.Column("checksum_sha256", sa.Text),
    sa.Column(
        "status",
        sa.Text,
        server_default="pending",
        comment="pending | downloading | complete | failed",
    ),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime),
)

# ── Download Sessions ─────────────────────────────────────────────────

download_sessions = sa.Table(
    "download_sessions",
    reference_metadata,
    sa.Column("session_id", sa.Text, primary_key=True),
    sa.Column(
        "status",
        sa.Text,
        nullable=False,
        server_default="in_progress",
        comment="in_progress | complete | failed | interrupted | stale",
    ),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
)

download_session_jobs = sa.Table(
    "download_session_jobs",
    reference_metadata,
    sa.Column(
        "session_id",
        sa.Text,
        sa.ForeignKey("download_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    ),
    sa.Column("db_name", sa.Text, nullable=False, primary_key=True),
    sa.Column("job_id", sa.Text, nullable=False),
)

# ── ClinVar Variants ──────────────────────────────────────────────────

clinvar_variants = sa.Table(
    "clinvar_variants",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("rsid", sa.Text, index=True),
    sa.Column("chrom", sa.Text, nullable=False),
    sa.Column("pos", sa.Integer, nullable=False),
    sa.Column("ref", sa.Text, nullable=False),
    sa.Column("alt", sa.Text, nullable=False),
    sa.Column("significance", sa.Text),
    sa.Column("review_stars", sa.Integer),
    sa.Column("accession", sa.Text),
    sa.Column("conditions", sa.Text),
    sa.Column("gene_symbol", sa.Text),
    sa.Column("variation_id", sa.Integer),
)

sa.Index("idx_clinvar_chrom_pos", clinvar_variants.c.chrom, clinvar_variants.c.pos)

# ── MONDO/HPO Gene-Phenotype ──────────────────────────────────────────

gene_phenotype = sa.Table(
    "gene_phenotype",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("gene_symbol", sa.Text, nullable=False, index=True),
    sa.Column("disease_name", sa.Text, nullable=False),
    sa.Column("disease_id", sa.Text, comment="MONDO or OMIM ID"),
    sa.Column("hpo_terms", sa.Text, comment="JSON array of HPO term IDs"),
    sa.Column(
        "source",
        sa.Text,
        nullable=False,
        comment="mondo_hpo | omim",
    ),
    sa.Column("inheritance", sa.Text),
)

# ── ClinGen Gene-Disease Validity (SW-A11) ────────────────────────────

clingen_gene_validity = sa.Table(
    "clingen_gene_validity",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("gene_symbol", sa.Text, nullable=False, index=True),
    sa.Column("hgnc_id", sa.Text, comment="HGNC:NNN"),
    sa.Column("disease_label", sa.Text, nullable=False),
    sa.Column("disease_id", sa.Text, comment="MONDO id"),
    sa.Column("moi", sa.Text, comment="mode of inheritance: AD/AR/XL/SD/MT/UD"),
    sa.Column("sop", sa.Text, comment="ClinGen SOP version, e.g. SOP10"),
    sa.Column(
        "classification",
        sa.Text,
        nullable=False,
        comment="Definitive|Strong|Moderate|Limited|Disputed|Refuted|"
        "No Known Disease Relationship",
    ),
    sa.Column("report_url", sa.Text, comment="ClinGen online report URL"),
    sa.Column("classification_date", sa.Text, comment="ISO-8601 timestamp"),
    sa.Column("gcep", sa.Text, comment="Gene Curation Expert Panel"),
)

# ── CPIC Allele Definitions ───────────────────────────────────────────

cpic_alleles = sa.Table(
    "cpic_alleles",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("gene", sa.Text, nullable=False, index=True),
    sa.Column("allele_name", sa.Text, nullable=False, comment="e.g. *1, *2"),
    sa.Column(
        "defining_variants",
        sa.Text,
        comment="JSON array of {rsid, ref, alt} objects",
    ),
    sa.Column("function", sa.Text),
    sa.Column("activity_score", sa.Float),
)

cpic_diplotypes = sa.Table(
    "cpic_diplotypes",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("gene", sa.Text, nullable=False, index=True),
    sa.Column("diplotype", sa.Text, nullable=False, comment="e.g. *1/*2"),
    sa.Column("phenotype", sa.Text, nullable=False),
    sa.Column("ehr_notation", sa.Text),
    sa.Column("activity_score", sa.Float),
)

cpic_guidelines = sa.Table(
    "cpic_guidelines",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("gene", sa.Text, nullable=False),
    sa.Column("drug", sa.Text, nullable=False),
    sa.Column("phenotype", sa.Text, nullable=False),
    sa.Column("recommendation", sa.Text),
    sa.Column("classification", sa.Text, comment="e.g. A, B, C, D"),
    sa.Column("guideline_url", sa.Text),
)

sa.Index(
    "idx_cpic_guidelines_gene_drug",
    cpic_guidelines.c.gene,
    cpic_guidelines.c.drug,
)

# ── Literature Cache ──────────────────────────────────────────────────

literature_cache = sa.Table(
    "literature_cache",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("pmid", sa.Text, nullable=False),
    sa.Column("gene", sa.Text),
    sa.Column("query", sa.Text),
    sa.Column("title", sa.Text),
    sa.Column("abstract", sa.Text),
    sa.Column("authors", sa.Text, comment="JSON array"),
    sa.Column("journal", sa.Text),
    sa.Column("year", sa.Integer),
    sa.Column("fetched_at", sa.DateTime, server_default=sa.func.now()),
)

sa.Index(
    "idx_literature_gene_pmid",
    literature_cache.c.gene,
    literature_cache.c.pmid,
    unique=True,
)

# ── UniProt Cache ─────────────────────────────────────────────────────

uniprot_cache = sa.Table(
    "uniprot_cache",
    reference_metadata,
    sa.Column("accession", sa.Text, primary_key=True),
    sa.Column("gene_symbol", sa.Text, index=True),
    sa.Column("domains", sa.Text, comment="JSON array of domain annotations"),
    sa.Column("features", sa.Text, comment="JSON array of protein features"),
    sa.Column("sequence_length", sa.Integer),
    sa.Column("fetched_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("ttl_days", sa.Integer, server_default="30"),
)

# ── Log Entries ────────────────────────────────────────────────────────

log_entries = sa.Table(
    "log_entries",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("timestamp", sa.DateTime, server_default=sa.func.now()),
    sa.Column("level", sa.Text, nullable=False),
    sa.Column("logger", sa.Text),
    sa.Column("message", sa.Text),
    sa.Column("event_data", sa.Text, comment="JSON structured log data"),
)

sa.Index("idx_log_timestamp", log_entries.c.timestamp)

# ── Re-annotation Prompt State ─────────────────────────────────────────

reannotation_prompts = sa.Table(
    "reannotation_prompts",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("sample_id", sa.Integer, nullable=False),
    sa.Column("db_name", sa.Text, nullable=False),
    sa.Column("db_version", sa.Text, nullable=False),
    sa.Column("candidate_count", sa.Integer, server_default="0"),
    sa.Column("watched_count", sa.Integer, server_default="0"),
    sa.Column(
        "watched_details",
        sa.Text,
        server_default="[]",
        comment="JSON array of watched variant reclassifications",
    ),
    sa.Column("dismissed", sa.Boolean, server_default=sa.text("0")),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

sa.Index("idx_reannotation_sample", reannotation_prompts.c.sample_id)

# ── dbSNP Merged rsids ────────────────────────────────────────────────

dbsnp_merges = sa.Table(
    "dbsnp_merges",
    reference_metadata,
    sa.Column("old_rsid", sa.Text, primary_key=True),
    sa.Column("current_rsid", sa.Text, nullable=False, index=True),
    sa.Column("build_id", sa.Integer, comment="dbSNP build where merge occurred"),
)

# ── GWAS Catalog ──────────────────────────────────────────────────────

gwas_associations = sa.Table(
    "gwas_associations",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("rsid", sa.Text, nullable=False, index=True),
    sa.Column("chrom", sa.Text),
    sa.Column("pos", sa.Integer),
    sa.Column("trait", sa.Text, nullable=False),
    sa.Column("p_value", sa.Float),
    sa.Column("odds_ratio", sa.Float),
    sa.Column("beta", sa.Float),
    sa.Column("risk_allele", sa.Text),
    sa.Column("pubmed_id", sa.Text),
    sa.Column("study", sa.Text),
    sa.Column("sample_size", sa.Integer),
)

# ── gnomAD gene constraint (LOEUF / pLI / missense-z) ─────────────────
# gnomAD v2.1.1 (GRCh37, CC0) lof_metrics.by_gene. Powers a gene-level
# "doesn't tolerate loss-of-function" context badge on monogenic findings
# (EXPANSION_STRATEGY.md §7 / roadmap #12). Context only — never auto-upgrades
# an ACMG classification. lof_constrained is computed at lookup
# (loeuf < 0.35 or pli > 0.9), not stored, so the table holds raw metrics.

gnomad_gene_constraint = sa.Table(
    "gnomad_gene_constraint",
    reference_metadata,
    sa.Column("gene_symbol", sa.Text, primary_key=True),
    sa.Column("transcript", sa.Text),
    sa.Column("oe_lof", sa.Float, comment="observed/expected LoF point estimate"),
    sa.Column("loeuf", sa.Float, comment="oe_lof_upper — the LOEUF score"),
    sa.Column("pli", sa.Float, comment="prob. of loss-of-function intolerance"),
    sa.Column("mis_z", sa.Float, comment="missense constraint Z-score"),
    sa.Column("syn_z", sa.Float, comment="synonymous Z-score (QC sanity)"),
)

sa.Index("idx_gnomad_constraint_loeuf", gnomad_gene_constraint.c.loeuf)

# ── HLA Proxy Lookup ─────────────────────────────────────────────────

hla_proxy_lookup = sa.Table(
    "hla_proxy_lookup",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("hla_allele", sa.Text, nullable=False, comment="e.g. HLA-B*57:01"),
    sa.Column("proxy_rsid", sa.Text, nullable=False, comment="Tagging SNP rsid"),
    sa.Column(
        "r_squared",
        sa.Float,
        nullable=False,
        comment="Linkage disequilibrium r² value",
    ),
    sa.Column(
        "ancestry_pop",
        sa.Text,
        nullable=False,
        comment="Ancestry population e.g. EUR, EAS, ALL",
    ),
    sa.Column(
        "clinical_context",
        sa.Text,
        comment="Clinical association e.g. Abacavir hypersensitivity",
    ),
    sa.Column("pmid", sa.Text, comment="Supporting publication PMID"),
)

sa.Index("idx_hla_proxy_rsid", hla_proxy_lookup.c.proxy_rsid)
sa.Index("idx_hla_proxy_allele", hla_proxy_lookup.c.hla_allele)

# ── Custom Gene Panels (P4-11) ──────────────────────────────────────

custom_panels = sa.Table(
    "custom_panels",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, server_default=""),
    sa.Column(
        "gene_symbols",
        sa.Text,
        nullable=False,
        comment="JSON array of gene symbols",
    ),
    sa.Column(
        "bed_regions",
        sa.Text,
        comment="JSON array of {chrom, start, end, name} objects (BED source only)",
    ),
    sa.Column(
        "source_type",
        sa.Text,
        nullable=False,
        server_default="gene_list",
        comment="gene_list | bed",
    ),
    sa.Column("gene_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

sa.Index("idx_custom_panels_name", custom_panels.c.name)

# ── Overlay Configs (P4-12, vcfanno integration) ─────────────────────

overlay_configs = sa.Table(
    "overlay_configs",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, server_default=""),
    sa.Column(
        "file_type",
        sa.Text,
        nullable=False,
        comment="bed | vcf",
    ),
    sa.Column(
        "column_names",
        sa.Text,
        nullable=False,
        comment="JSON array of annotation column names from the overlay file",
    ),
    sa.Column("region_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

sa.Index("idx_overlay_configs_name", overlay_configs.c.name)


# Opt-in consent for sensitive risk overlays (SW-B8). A per-sample, per-feature
# record gating features that quantify absolute disease risk (e.g. the breast
# absolute-risk overlay), which are shown only after explicit acknowledgment.
risk_overlay_consent = sa.Table(
    "risk_overlay_consent",
    reference_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("sample_id", sa.Integer, nullable=False),
    sa.Column(
        "feature",
        sa.Text,
        nullable=False,
        comment="Overlay feature key, e.g. 'breast_absolute_risk'",
    ),
    sa.Column("consented", sa.Integer, nullable=False, server_default="0"),
    # Set only when consent is granted (NULL otherwise) — an audit timestamp of
    # *when consent was given*, mirroring apoe_gate.acknowledged_at.
    sa.Column("consented_at", sa.DateTime),
)

sa.Index(
    "idx_risk_overlay_consent_sample_feature",
    risk_overlay_consent.c.sample_id,
    risk_overlay_consent.c.feature,
    unique=True,
)


# ═══════════════════════════════════════════════════════════════════════
# Sample DB (sample_{id}.db) — Created programmatically per sample
# ═══════════════════════════════════════════════════════════════════════

sample_metadata_obj = sa.MetaData()

# ── Raw Variants (as parsed from 23andMe file) ────────────────────────

raw_variants = sa.Table(
    "raw_variants",
    sample_metadata_obj,
    sa.Column("rsid", sa.Text, primary_key=True),
    sa.Column("chrom", sa.Text, nullable=False),
    sa.Column("pos", sa.Integer, nullable=False),
    sa.Column("genotype", sa.Text, nullable=False),
    # Provenance columns (AncestryDNA Plan §10.4b). Populated only for merged
    # samples; unmerged samples carry empty-string defaults.
    sa.Column(
        "source",
        sa.Text,
        nullable=False,
        server_default="",
        comment="'S1' | 'S2' | 'both' | '' (unmerged)",
    ),
    sa.Column(
        "concordance",
        sa.Text,
        nullable=False,
        server_default="",
        comment="'match' | 'filled_nocall' | 'discordant' | 'unique' | ''",
    ),
    sa.Column(
        "discordant_alt_genotype",
        sa.Text,
        nullable=False,
        server_default="",
    ),
    sa.Column(
        "alt_rsid",
        sa.Text,
        nullable=False,
        server_default="",
        comment="Rejected rsid at a collapsed locus",
    ),
)

sa.Index("idx_raw_chrom_pos", raw_variants.c.chrom, raw_variants.c.pos)

# ── Annotated Variants (single wide table) ────────────────────────────

annotated_variants = sa.Table(
    "annotated_variants",
    sample_metadata_obj,
    sa.Column("rsid", sa.Text, primary_key=True),
    sa.Column("chrom", sa.Text, nullable=False),
    sa.Column("pos", sa.Integer, nullable=False),
    sa.Column("ref", sa.Text),
    sa.Column("alt", sa.Text),
    sa.Column("genotype", sa.Text),
    sa.Column("zygosity", sa.Text),  # hom_ref, het, hom_alt
    # VEP annotation (bitmask bit 0)
    sa.Column("gene_symbol", sa.Text),
    sa.Column("transcript_id", sa.Text),
    sa.Column("consequence", sa.Text),  # SO term
    sa.Column("hgvs_coding", sa.Text),
    sa.Column("hgvs_protein", sa.Text),
    sa.Column("strand", sa.Text),
    sa.Column("exon_number", sa.Integer),
    sa.Column("intron_number", sa.Integer),
    sa.Column("mane_select", sa.Boolean, server_default=sa.text("0")),
    # ClinVar (bitmask bit 1)
    sa.Column("clinvar_significance", sa.Text),
    sa.Column("clinvar_review_stars", sa.Integer),
    sa.Column("clinvar_accession", sa.Text),
    sa.Column("clinvar_conditions", sa.Text),
    # gnomAD allele frequency (bitmask bit 2)
    sa.Column("gnomad_af_global", sa.Float),
    sa.Column("gnomad_af_afr", sa.Float),
    sa.Column("gnomad_af_amr", sa.Float),
    sa.Column("gnomad_af_eas", sa.Float),
    sa.Column("gnomad_af_eur", sa.Float),
    sa.Column("gnomad_af_fin", sa.Float),
    sa.Column("gnomad_af_sas", sa.Float),
    sa.Column(
        "gnomad_af_popmax",
        sa.Float,
        comment="Population-max AF (max of non-null pop AFs); rarity denominator (F15)",
    ),
    sa.Column("gnomad_homozygous_count", sa.Integer),
    sa.Column("rare_flag", sa.Boolean, server_default=sa.text("0")),
    sa.Column("ultra_rare_flag", sa.Boolean, server_default=sa.text("0")),
    # dbNSFP in-silico scores (bitmask bit 3)
    sa.Column("cadd_phred", sa.Float),
    sa.Column("sift_score", sa.Float),
    sa.Column("sift_pred", sa.Text),
    sa.Column("polyphen2_hsvar_score", sa.Float),
    sa.Column("polyphen2_hsvar_pred", sa.Text),
    sa.Column("revel", sa.Float),
    sa.Column("mutpred2", sa.Float),
    sa.Column("vest4", sa.Float),
    sa.Column("metasvm", sa.Float),
    sa.Column("metalr", sa.Float),
    sa.Column("gerp_rs", sa.Float),
    sa.Column("phylop", sa.Float),
    sa.Column("mpc", sa.Float),
    sa.Column("primateai", sa.Float),
    # AlphaMissense context-only missense predictions (bitmask bit 7)
    sa.Column(
        "alphamissense_pathogenicity",
        sa.Float,
        comment="AlphaMissense am_pathogenicity score; context only, not ACMG evidence",
    ),
    sa.Column(
        "alphamissense_class",
        sa.Text,
        comment="AlphaMissense am_class label; context only, not ACMG evidence",
    ),
    # dbSNP cross-reference
    sa.Column("dbsnp_build", sa.Integer, comment="dbSNP build where rsid first appeared"),
    sa.Column(
        "dbsnp_rsid_current",
        sa.Text,
        comment="Current rsid if original was merged; NULL if already current",
    ),
    sa.Column(
        "dbsnp_validation",
        sa.Text,
        comment="valid | merged | i_prefix | invalid",
    ),
    # Gene-phenotype (bitmask bit 4)
    sa.Column("disease_name", sa.Text),
    sa.Column("disease_id", sa.Text, comment="MONDO or OMIM ID"),
    sa.Column("phenotype_source", sa.Text, comment="mondo_hpo | omim"),
    sa.Column("hpo_terms", sa.Text, comment="JSON array of HPO term IDs"),
    sa.Column("inheritance_pattern", sa.Text),
    # Ensemble pathogenicity (dbNSFP-derived)
    sa.Column(
        "deleterious_count",
        sa.Integer,
        comment="Independent in-silico axes voting deleterious (0-4, F24)",
    ),
    sa.Column(
        "deleterious_total_assessed",
        sa.Integer,
        comment="Independent in-silico axes assessed — k-of-present denominator (F25)",
    ),
    # Evidence & conflict
    sa.Column("evidence_conflict", sa.Boolean, server_default=sa.text("0")),
    sa.Column("ensemble_pathogenic", sa.Boolean, server_default=sa.text("0")),
    # Annotation coverage bitmask:
    # VEP|ClinVar|gnomAD|dbNSFP|gene phenotype|GWAS|CPIC|AlphaMissense
    sa.Column("annotation_coverage", sa.Integer),
    # GRCh38 liftover (P4-19) — parallel coordinates, NULL if unmapped
    sa.Column("chrom_grch38", sa.Text, comment="GRCh38 chromosome (lifted from GRCh37)"),
    sa.Column("pos_grch38", sa.Integer, comment="GRCh38 position, 1-based (lifted from GRCh37)"),
)

sa.Index(
    "idx_annot_chrom_pos",
    annotated_variants.c.chrom,
    annotated_variants.c.pos,
)
sa.Index("idx_annot_gene", annotated_variants.c.gene_symbol)
sa.Index("idx_annot_clinvar_sig", annotated_variants.c.clinvar_significance)
sa.Index("idx_annot_coverage", annotated_variants.c.annotation_coverage)
sa.Index("idx_annot_rare_flag", annotated_variants.c.rare_flag)
sa.Index("idx_annot_ultra_rare_flag", annotated_variants.c.ultra_rare_flag)
sa.Index("idx_annot_gnomad_af", annotated_variants.c.gnomad_af_global)

# ── Findings (unified output from all analysis modules) ────────────────

findings = sa.Table(
    "findings",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("module", sa.Text, nullable=False),
    sa.Column("category", sa.Text),
    sa.Column("evidence_level", sa.Integer),  # 1-4 stars
    sa.Column("gene_symbol", sa.Text),
    sa.Column("rsid", sa.Text),
    sa.Column("finding_text", sa.Text, nullable=False),
    sa.Column("phenotype", sa.Text),
    sa.Column("conditions", sa.Text),
    sa.Column("zygosity", sa.Text),
    sa.Column("clinvar_significance", sa.Text),
    sa.Column("diplotype", sa.Text),
    sa.Column("metabolizer_status", sa.Text),
    sa.Column("drug", sa.Text),
    sa.Column("haplogroup", sa.Text),
    sa.Column("prs_score", sa.Float),
    sa.Column("prs_percentile", sa.Float),
    sa.Column("pathway", sa.Text),
    sa.Column("pathway_level", sa.Text),  # Elevated / Moderate / Standard
    sa.Column("svg_path", sa.Text),
    sa.Column("pmid_citations", sa.Text),  # JSON array of PubMed IDs
    sa.Column("detail_json", sa.Text),  # arbitrary module-specific data (JSON)
    # Per-finding provenance + version pinning (SW-A4 / #8): JSON snapshot of the
    # source releases (ClinVar/gnomAD/dbNSFP/CPIC versions + genome_build, F30),
    # the variant's variation IDs, the annotation_coverage bitmask, and the
    # pipeline version that produced the finding. NULL on rows predating SW-A4 /
    # before the post-run stamping pass. Audit metadata only — never alters
    # evidence_level / clinvar_significance.
    sa.Column("provenance", sa.Text),
    sa.Column("related_module", sa.Text),  # cross-module link target module name
    sa.Column("related_finding_id", sa.Integer),  # cross-module link target finding ID
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

sa.Index("idx_findings_module", findings.c.module)
sa.Index("idx_findings_evidence", findings.c.evidence_level)
sa.Index("idx_findings_related_module", findings.c.related_module)

# ── QC Metrics ─────────────────────────────────────────────────────────

qc_metrics = sa.Table(
    "qc_metrics",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("call_rate", sa.Float),
    sa.Column("heterozygosity_rate", sa.Float),
    sa.Column("ti_tv_ratio", sa.Float),
    sa.Column("total_variants", sa.Integer),
    sa.Column("called_variants", sa.Integer),
    sa.Column("nocall_variants", sa.Integer),
    sa.Column("computed_at", sa.DateTime, server_default=sa.func.now()),
)

# ── Sample Metadata ────────────────────────────────────────────────────

sample_metadata_table = sa.Table(
    "sample_metadata",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("notes", sa.Text, server_default=""),
    sa.Column("date_collected", sa.Date),
    sa.Column("source", sa.Text, server_default=""),
    sa.Column("file_format", sa.Text),
    sa.Column("file_hash", sa.Text),
    sa.Column("extra", sa.Text, server_default="{}"),  # JSON
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    sa.CheckConstraint("id = 1", name="single_row_metadata"),
)

# ── APOE Gate State ────────────────────────────────────────────────────

apoe_gate = sa.Table(
    "apoe_gate",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("acknowledged", sa.Boolean, server_default=sa.text("0")),
    sa.Column("acknowledged_at", sa.DateTime),
    sa.CheckConstraint("id = 1", name="single_row_apoe"),
)

# ── Parkinson's Gate State ─────────────────────────────────────────────
# Mirrors apoe_gate: a single-row opt-in acknowledgment for the ethically
# sensitive, late-onset LRRK2 G2019S Parkinson's-risk disclosure (roadmap #41).

parkinsons_gate = sa.Table(
    "parkinsons_gate",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("acknowledged", sa.Boolean, server_default=sa.text("0")),
    sa.Column("acknowledged_at", sa.DateTime),
    sa.CheckConstraint("id = 1", name="single_row_parkinsons"),
)

# ── Sex-chromosome aneuploidy screen gate state ────────────────────────
# Single-row opt-in acknowledgment for the psychosocially-sensitive
# sex-chromosome aneuploidy screen (XXY/Klinefelter pattern; roadmap #48).

aneuploidy_gate = sa.Table(
    "aneuploidy_gate",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("acknowledged", sa.Boolean, server_default=sa.text("0")),
    sa.Column("acknowledged_at", sa.DateTime),
    sa.CheckConstraint("id = 1", name="single_row_aneuploidy"),
)

# ── Tags ───────────────────────────────────────────────────────────────

tags = sa.Table(
    "tags",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.Text, nullable=False, unique=True),
    sa.Column("color", sa.Text, server_default="'#6B7280'"),
    sa.Column("is_predefined", sa.Boolean, server_default=sa.text("0")),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

PREDEFINED_TAGS = [
    "Review later",
    "Discuss with clinician",
    "False positive",
    "Actionable",
    "Benign override",
]

# ── Variant Tags (many-to-many) ────────────────────────────────────────

variant_tags = sa.Table(
    "variant_tags",
    sample_metadata_obj,
    sa.Column("rsid", sa.Text, nullable=False, primary_key=True),
    sa.Column(
        "tag_id",
        sa.Integer,
        sa.ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    ),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

# ── Haplogroup Assignments ─────────────────────────────────────────────

haplogroup_assignments = sa.Table(
    "haplogroup_assignments",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("type", sa.Text, nullable=False),  # 'mt' or 'Y'
    sa.Column("haplogroup", sa.Text, nullable=False),
    sa.Column("confidence", sa.Float),
    sa.Column("defining_snps_present", sa.Integer),
    sa.Column("defining_snps_total", sa.Integer),
    sa.Column("assigned_at", sa.DateTime, server_default=sa.func.now()),
)

# ── Panel Coverage Tracking (P3-58) ──────────────────────────────────

panel_coverage = sa.Table(
    "panel_coverage",
    sample_metadata_obj,
    sa.Column("module", sa.Text, nullable=False, primary_key=True),
    sa.Column("rsid", sa.Text, nullable=False, primary_key=True),
    sa.Column("gene", sa.Text),
    sa.Column("expected_trait", sa.Text),
    sa.Column(
        "coverage_status",
        sa.Text,
        nullable=False,
    ),
    sa.CheckConstraint(
        "coverage_status IN ('called', 'no_call', 'not_on_array')",
        name="ck_panel_coverage_status",
    ),
)

# ── LAI Results (Local Ancestry Inference) ────────────────────────────

lai_results = sa.Table(
    "lai_results",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("global_ancestry_json", sa.Text, nullable=False),
    sa.Column("chromosome_painting_json", sa.Text, nullable=False),
    sa.Column("metadata_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
)

# ── Watched Variants (VUS tracking) ───────────────────────────────────

watched_variants = sa.Table(
    "watched_variants",
    sample_metadata_obj,
    sa.Column("rsid", sa.Text, primary_key=True),
    sa.Column("watched_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column("clinvar_significance_at_watch", sa.Text),
    sa.Column("notes", sa.Text, server_default=""),
)

# ── Merge Provenance (single-row, present only on merged samples) ─────
# AncestryDNA Plan §10.4c. Created on every sample DB but only populated by
# the merge service on merged samples. CheckConstraint enforces single row.

merge_provenance = sa.Table(
    "merge_provenance",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("merged_at", sa.DateTime, server_default=sa.func.now()),
    sa.Column(
        "strategy",
        sa.Text,
        nullable=False,
        comment="prefer_23andme | prefer_ancestrydna | flag_only",
    ),
    sa.Column(
        "source_sample_ids",
        sa.Text,
        nullable=False,
        comment="JSON array, ordered [S1, S2]",
    ),
    sa.Column(
        "source_file_hashes",
        sa.Text,
        nullable=False,
        comment="JSON array, ordered [S1, S2] — same order as source_sample_ids",
    ),
    sa.Column(
        "concordance_summary",
        sa.Text,
        nullable=False,
        comment=("JSON {match, filled_nocall, discordant, unique_S1, unique_S2, collapsed_rsid}"),
    ),
    sa.CheckConstraint("id = 1", name="single_row_merge_provenance"),
)

# ── Annotation State (kv table for per-sample annotation provenance) ──

annotation_state = sa.Table(
    "annotation_state",
    sample_metadata_obj,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
)

# ── Variant Overlays (P4-12, vcfanno integration) ────────────────────

variant_overlays = sa.Table(
    "variant_overlays",
    sample_metadata_obj,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("rsid", sa.Text, nullable=False),
    sa.Column("overlay_id", sa.Integer, nullable=False),
    sa.Column(
        "annotations",
        sa.Text,
        nullable=False,
        comment="JSON object mapping column_name -> value",
    ),
)

sa.Index(
    "idx_variant_overlays_rsid_overlay",
    variant_overlays.c.rsid,
    variant_overlays.c.overlay_id,
    unique=True,
)
sa.Index("idx_variant_overlays_overlay_id", variant_overlays.c.overlay_id)
