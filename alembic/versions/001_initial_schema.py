"""Initial schema for reference.db.

Revision ID: 001
Revises: None
Create Date: 2026-03-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Sample Registry ──────────────────────────────────────────────
    op.create_table(
        "samples",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("db_path", sa.Text, nullable=False, unique=True),
        sa.Column("file_format", sa.Text),
        sa.Column("file_hash", sa.Text),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime),
    )

    # ── Jobs (Huey ↔ FastAPI IPC) ────────────────────────────────────
    op.create_table(
        "jobs",
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
            comment="pending | running | complete | failed | cancelled",
        ),
        sa.Column("progress_pct", sa.Float, server_default="0"),
        sa.Column("message", sa.Text, server_default=""),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ── Database Versions ────────────────────────────────────────────
    op.create_table(
        "database_versions",
        sa.Column("db_name", sa.Text, primary_key=True),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text),
        sa.Column("file_size_bytes", sa.Integer),
        sa.Column("downloaded_at", sa.DateTime),
        sa.Column("checksum_sha256", sa.Text),
    )

    # ── Update History ───────────────────────────────────────────────
    op.create_table(
        "update_history",
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

    # ── Download Checkpoints ─────────────────────────────────────────
    op.create_table(
        "downloads",
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

    # ── ClinVar Variants ─────────────────────────────────────────────
    op.create_table(
        "clinvar_variants",
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
    op.create_index("idx_clinvar_chrom_pos", "clinvar_variants", ["chrom", "pos"])

    # ── MONDO/HPO Gene-Phenotype ─────────────────────────────────────
    op.create_table(
        "gene_phenotype",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gene_symbol", sa.Text, nullable=False, index=True),
        sa.Column("disease_name", sa.Text, nullable=False),
        sa.Column("disease_id", sa.Text, comment="MONDO or OMIM ID"),
        sa.Column(
            "hpo_terms",
            sa.Text,
            comment="JSON array of legacy HPO IDs or labelled {id, name} objects",
        ),
        sa.Column(
            "source",
            sa.Text,
            nullable=False,
            comment="mondo_hpo | omim",
        ),
        sa.Column("inheritance", sa.Text),
    )

    # ── CPIC Allele Definitions ──────────────────────────────────────
    op.create_table(
        "cpic_alleles",
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

    op.create_table(
        "cpic_diplotypes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gene", sa.Text, nullable=False, index=True),
        sa.Column("diplotype", sa.Text, nullable=False, comment="e.g. *1/*2"),
        sa.Column("phenotype", sa.Text, nullable=False),
        sa.Column("ehr_notation", sa.Text),
        sa.Column("activity_score", sa.Float),
    )

    op.create_table(
        "cpic_guidelines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gene", sa.Text, nullable=False),
        sa.Column("drug", sa.Text, nullable=False),
        sa.Column("phenotype", sa.Text, nullable=False),
        sa.Column("recommendation", sa.Text),
        sa.Column("classification", sa.Text, comment="e.g. A, B, C, D"),
        sa.Column("guideline_url", sa.Text),
    )
    op.create_index(
        "idx_cpic_guidelines_gene_drug",
        "cpic_guidelines",
        ["gene", "drug"],
    )

    # ── Literature Cache ─────────────────────────────────────────────
    op.create_table(
        "literature_cache",
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
    op.create_index("idx_literature_gene_pmid", "literature_cache", ["gene", "pmid"], unique=True)

    # ── UniProt Cache ────────────────────────────────────────────────
    op.create_table(
        "uniprot_cache",
        sa.Column("accession", sa.Text, primary_key=True),
        sa.Column("gene_symbol", sa.Text, index=True),
        sa.Column("domains", sa.Text, comment="JSON array of domain annotations"),
        sa.Column("features", sa.Text, comment="JSON array of protein features"),
        sa.Column("sequence_length", sa.Integer),
        sa.Column("fetched_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("ttl_days", sa.Integer, server_default="30"),
    )

    # ── Log Entries ──────────────────────────────────────────────────
    op.create_table(
        "log_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime, server_default=sa.func.now()),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("logger", sa.Text),
        sa.Column("message", sa.Text),
        sa.Column("event_data", sa.Text, comment="JSON structured log data"),
    )
    op.create_index("idx_log_timestamp", "log_entries", ["timestamp"])

    # ── Re-annotation Prompt State ───────────────────────────────────
    op.create_table(
        "reannotation_prompts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sample_id", sa.Integer, nullable=False),
        sa.Column("db_name", sa.Text, nullable=False),
        sa.Column("db_version", sa.Text, nullable=False),
        sa.Column("candidate_count", sa.Integer, server_default="0"),
        sa.Column("dismissed", sa.Boolean, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("idx_reannotation_sample", "reannotation_prompts", ["sample_id"])

    # ── GWAS Catalog ─────────────────────────────────────────────────
    op.create_table(
        "gwas_associations",
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


def downgrade() -> None:
    op.drop_table("gwas_associations")
    op.drop_table("reannotation_prompts")
    op.drop_table("log_entries")
    op.drop_table("uniprot_cache")
    op.drop_table("literature_cache")
    op.drop_table("cpic_guidelines")
    op.drop_table("cpic_diplotypes")
    op.drop_table("cpic_alleles")
    op.drop_table("gene_phenotype")
    op.drop_table("clinvar_variants")
    op.drop_table("downloads")
    op.drop_table("update_history")
    op.drop_table("database_versions")
    op.drop_table("jobs")
    op.drop_table("samples")
