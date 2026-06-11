"""Tests for SQLAlchemy Core table definitions and DBRegistry."""

import sqlalchemy as sa

from backend.db.tables import (
    PREDEFINED_TAGS,
    annotated_variants,
    apoe_gate,
    auto_update_settings,
    clinvar_variants,
    cpic_guidelines,
    database_versions,
    findings,
    jobs,
    literature_cache,
    log_entries,
    raw_variants,
    reannotation_prompts,
    reference_metadata,
    sample_metadata_obj,
    sample_metadata_table,
    samples,
    uniprot_cache,
    variant_tags,
)

# ── Reference MetaData Tests ──────────────────────────────────────────


class TestReferenceMetadata:
    """Verify reference_metadata contains all expected tables."""

    def test_reference_table_count(self):
        assert len(reference_metadata.tables) == 26

    def test_reference_table_names(self):
        expected = {
            "individuals",
            "samples",
            "jobs",
            "database_versions",
            "auto_update_settings",
            "update_history",
            "downloads",
            "download_sessions",
            "download_session_jobs",
            "clinvar_variants",
            "gene_phenotype",
            "cpic_alleles",
            "cpic_diplotypes",
            "cpic_guidelines",
            "clingen_gene_validity",
            "literature_cache",
            "uniprot_cache",
            "log_entries",
            "reannotation_prompts",
            "gwas_associations",
            "dbsnp_merges",
            "hla_proxy_lookup",
            "custom_panels",
            "overlay_configs",
            "gnomad_gene_constraint",
            "risk_overlay_consent",
        }
        assert set(reference_metadata.tables.keys()) == expected

    def test_samples_primary_key(self):
        pk_cols = [c.name for c in samples.primary_key.columns]
        assert pk_cols == ["id"]

    def test_jobs_primary_key(self):
        pk_cols = [c.name for c in jobs.primary_key.columns]
        assert pk_cols == ["job_id"]

    def test_jobs_status_default(self):
        col = jobs.c.status
        assert col.server_default is not None

    def test_clinvar_has_rsid_index(self):
        idx_names = {idx.name for idx in clinvar_variants.indexes}
        # SQLAlchemy auto-names index for Column(..., index=True)
        assert any("rsid" in name for name in idx_names)

    def test_clinvar_chrom_pos_index(self):
        idx_names = {idx.name for idx in clinvar_variants.indexes}
        assert "idx_clinvar_chrom_pos" in idx_names

    def test_cpic_guidelines_gene_drug_index(self):
        idx_names = {idx.name for idx in cpic_guidelines.indexes}
        assert "idx_cpic_guidelines_gene_drug" in idx_names

    def test_literature_cache_unique_index(self):
        idx = next(i for i in literature_cache.indexes if i.name == "idx_literature_gene_pmid")
        assert idx.unique is True

    def test_log_entries_timestamp_index(self):
        idx_names = {idx.name for idx in log_entries.indexes}
        assert "idx_log_timestamp" in idx_names

    def test_reannotation_sample_index(self):
        idx_names = {idx.name for idx in reannotation_prompts.indexes}
        assert "idx_reannotation_sample" in idx_names

    def test_database_versions_pk(self):
        pk_cols = [c.name for c in database_versions.primary_key.columns]
        assert pk_cols == ["db_name"]

    def test_auto_update_settings_pk(self):
        pk_cols = [c.name for c in auto_update_settings.primary_key.columns]
        assert pk_cols == ["db_name"]

    def test_auto_update_settings_columns(self):
        col_names = [c.name for c in auto_update_settings.columns]
        assert col_names == ["db_name", "enabled", "updated_at"]

    def test_auto_update_settings_column_types(self):
        cols = {c.name: c for c in auto_update_settings.columns}
        assert isinstance(cols["db_name"].type, sa.Text)
        assert isinstance(cols["enabled"].type, sa.Boolean)
        assert isinstance(cols["updated_at"].type, sa.DateTime)
        assert cols["updated_at"].type.timezone is True

    def test_auto_update_settings_nullability(self):
        cols = {c.name: c for c in auto_update_settings.columns}
        # db_name is PK → implicitly NOT NULL
        assert cols["db_name"].nullable is False
        assert cols["enabled"].nullable is False
        assert cols["updated_at"].nullable is False

    def test_uniprot_cache_pk(self):
        pk_cols = [c.name for c in uniprot_cache.primary_key.columns]
        assert pk_cols == ["accession"]


# ── Sample MetaData Tests ─────────────────────────────────────────────


class TestSampleMetadata:
    """Verify sample_metadata_obj contains all expected tables."""

    def test_sample_table_count(self):
        assert len(sample_metadata_obj.tables) == 17

    def test_sample_table_names(self):
        expected = {
            "raw_variants",
            "annotated_variants",
            "findings",
            "qc_metrics",
            "sample_metadata",
            "apoe_gate",
            "parkinsons_gate",
            "aneuploidy_gate",
            "tags",
            "variant_tags",
            "haplogroup_assignments",
            "panel_coverage",
            "lai_results",
            "watched_variants",
            "variant_overlays",
            "annotation_state",
            "merge_provenance",
        }
        assert set(sample_metadata_obj.tables.keys()) == expected

    def test_raw_variants_columns(self):
        col_names = [c.name for c in raw_variants.columns]
        # v8 (Step 63) added the four provenance columns (Plan §10.4b).
        assert col_names == [
            "rsid",
            "chrom",
            "pos",
            "genotype",
            "source",
            "concordance",
            "discordant_alt_genotype",
            "alt_rsid",
        ]

    def test_raw_variants_pk(self):
        pk_cols = [c.name for c in raw_variants.primary_key.columns]
        assert pk_cols == ["rsid"]

    def test_annotated_variants_column_count(self):
        assert len(annotated_variants.columns) >= 30

    def test_annotated_variants_bitmask_columns(self):
        col_names = {c.name for c in annotated_variants.columns}
        # VEP columns
        assert {"gene_symbol", "consequence", "hgvs_protein", "mane_select"}.issubset(col_names)
        # ClinVar columns
        assert {"clinvar_significance", "clinvar_review_stars"}.issubset(col_names)
        # gnomAD columns
        assert {"gnomad_af_global", "gnomad_af_eur", "rare_flag"}.issubset(col_names)
        # dbNSFP columns
        assert {"cadd_phred", "sift_score", "revel"}.issubset(col_names)
        # Coverage bitmask
        assert "annotation_coverage" in col_names

    def test_annotated_variants_indexes(self):
        idx_names = {idx.name for idx in annotated_variants.indexes}
        assert "idx_annot_chrom_pos" in idx_names
        assert "idx_annot_gene" in idx_names
        assert "idx_annot_clinvar_sig" in idx_names
        assert "idx_annot_coverage" in idx_names

    def test_findings_indexes(self):
        idx_names = {idx.name for idx in findings.indexes}
        assert "idx_findings_module" in idx_names
        assert "idx_findings_evidence" in idx_names

    def test_sample_metadata_check_constraint(self):
        constraints = [
            c for c in sample_metadata_table.constraints if isinstance(c, sa.CheckConstraint)
        ]
        assert len(constraints) >= 1
        text = str(constraints[0].sqltext)
        assert "id = 1" in text

    def test_apoe_gate_check_constraint(self):
        constraints = [c for c in apoe_gate.constraints if isinstance(c, sa.CheckConstraint)]
        assert len(constraints) >= 1

    def test_variant_tags_composite_pk(self):
        pk_cols = {c.name for c in variant_tags.primary_key.columns}
        assert pk_cols == {"rsid", "tag_id"}

    def test_variant_tags_foreign_key(self):
        fks = list(variant_tags.foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "tags.id"

    def test_predefined_tags_list(self):
        assert len(PREDEFINED_TAGS) == 5
        assert "Review later" in PREDEFINED_TAGS
        assert "Actionable" in PREDEFINED_TAGS


# ── Core Table Query Building Tests ───────────────────────────────────


class TestQueryBuilding:
    """Verify that Core table objects can build SQL expressions."""

    def test_select_clinvar_by_rsid(self):
        stmt = sa.select(clinvar_variants).where(clinvar_variants.c.rsid == "rs123")
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "clinvar_variants" in compiled
        assert "rs123" in compiled

    def test_select_annotated_variants_bitmask(self):
        """Verify bitmask query pattern works: WHERE annotation_coverage & 2 != 0."""
        stmt = sa.select(annotated_variants).where(
            annotated_variants.c.annotation_coverage.op("&")(2) != 0
        )
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "annotation_coverage" in compiled

    def test_select_findings_by_module(self):
        stmt = (
            sa.select(findings)
            .where(findings.c.module == "pharmacogenomics")
            .order_by(findings.c.evidence_level.desc())
        )
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "pharmacogenomics" in compiled
        assert "ORDER BY" in compiled

    def test_insert_raw_variant(self):
        stmt = raw_variants.insert().values(rsid="rs123", chrom="1", pos=100000, genotype="AG")
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "INSERT" in compiled
        assert "raw_variants" in compiled

    def test_cursor_keyset_pagination(self):
        """Verify keyset pagination pattern on (chrom, pos)."""
        stmt = (
            sa.select(annotated_variants)
            .where(
                sa.tuple_(annotated_variants.c.chrom, annotated_variants.c.pos)
                > sa.tuple_(sa.literal("1"), sa.literal(50000))
            )
            .order_by(annotated_variants.c.chrom, annotated_variants.c.pos)
            .limit(100)
        )
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ORDER BY" in compiled
        assert "LIMIT" in compiled

    def test_join_clinvar_annotated(self):
        """Verify cross-DB join pattern (would happen in Python, not SQL,
        but the expression still compiles)."""
        stmt = sa.select(
            clinvar_variants.c.rsid,
            clinvar_variants.c.significance,
        ).where(clinvar_variants.c.rsid == "rs123")
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "significance" in compiled


# ── Create All / MetaData Materialisation Tests ──────────────────────


class TestMetadataMaterialisation:
    """Test that MetaData.create_all() produces valid SQLite databases."""

    def test_reference_create_all(self, tmp_path):
        db_path = tmp_path / "ref_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        reference_metadata.create_all(engine)

        insp = sa.inspect(engine)
        created_tables = set(insp.get_table_names())
        expected = set(reference_metadata.tables.keys())
        assert expected.issubset(created_tables)
        engine.dispose()

    def test_sample_create_all(self, tmp_path):
        db_path = tmp_path / "sample_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        sample_metadata_obj.create_all(engine)

        insp = sa.inspect(engine)
        created_tables = set(insp.get_table_names())
        expected = set(sample_metadata_obj.tables.keys())
        assert expected.issubset(created_tables)
        engine.dispose()

    def test_reference_insert_and_select(self, tmp_path):
        """Roundtrip test: insert into samples, select back."""
        db_path = tmp_path / "ref_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        reference_metadata.create_all(engine)

        with engine.connect() as conn:
            conn.execute(samples.insert().values(name="Test Sample", db_path="/tmp/s.db"))
            conn.commit()
            result = conn.execute(sa.select(samples)).fetchall()
            assert len(result) == 1
            assert result[0].name == "Test Sample"
        engine.dispose()

    def test_sample_insert_and_select(self, tmp_path):
        """Roundtrip test: insert raw_variant, select back."""
        db_path = tmp_path / "sample_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        sample_metadata_obj.create_all(engine)

        with engine.connect() as conn:
            conn.execute(
                raw_variants.insert().values(rsid="rs12345", chrom="1", pos=100000, genotype="AG")
            )
            conn.commit()
            result = conn.execute(
                sa.select(raw_variants).where(raw_variants.c.rsid == "rs12345")
            ).fetchone()
            assert result is not None
            assert result.chrom == "1"
            assert result.genotype == "AG"
        engine.dispose()

    def test_annotated_variants_bulk_insert(self, tmp_path):
        """Test executemany pattern for bulk annotation inserts."""
        db_path = tmp_path / "sample_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        sample_metadata_obj.create_all(engine)

        rows = [
            {"rsid": f"rs{i}", "chrom": "1", "pos": i * 1000, "annotation_coverage": 3}
            for i in range(100)
        ]
        with engine.connect() as conn:
            conn.execute(annotated_variants.insert(), rows)
            conn.commit()
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
            assert count == 100
        engine.dispose()


# ── DBRegistry Tests ──────────────────────────────────────────────────


class TestDBRegistry:
    """Test DBRegistry connection management."""

    def test_registry_creates_reference_engine(self, tmp_path):
        from backend.config import Settings
        from backend.db.connection import DBRegistry

        settings = Settings(data_dir=tmp_path)
        # Create the reference.db file first
        ref_path = settings.reference_db_path
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.touch()

        registry = DBRegistry(settings)
        assert registry.reference_engine is not None
        registry.dispose_all()

    def test_registry_sample_engine_caching(self, tmp_path):
        from backend.config import Settings
        from backend.db.connection import DBRegistry

        settings = Settings(data_dir=tmp_path)
        ref_path = settings.reference_db_path
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.touch()

        sample_path = tmp_path / "samples" / "sample_001.db"
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        sample_path.touch()

        registry = DBRegistry(settings)
        engine1 = registry.get_sample_engine(sample_path)
        engine2 = registry.get_sample_engine(sample_path)
        assert engine1 is engine2
        registry.dispose_all()

    def test_registry_dispose_all(self, tmp_path):
        from backend.config import Settings
        from backend.db.connection import DBRegistry

        settings = Settings(data_dir=tmp_path)
        ref_path = settings.reference_db_path
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.touch()

        registry = DBRegistry(settings)
        registry.dispose_all()
        # After dispose, _sample_engines should be empty
        assert len(registry._sample_engines) == 0
