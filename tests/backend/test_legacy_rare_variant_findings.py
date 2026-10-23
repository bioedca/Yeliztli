"""Upgraded samples rebuild the stored rare-variant derivation (#2577).

All loci, annotations and disease labels here are synthetic software inputs.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.rare_variant_finder import (
    RareVariantFilter,
    find_rare_variants,
    store_rare_variant_findings,
)
from backend.annotation.mondo_hpo import MONDO_HPO_INGESTION_REVISION
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    annotation_state,
    database_versions,
    findings,
    individuals,
    reference_metadata,
    samples,
)
from backend.services.staleness import REFERENCE_VERSION_SNAPSHOT_KEY

REVISION_KEY = "rare_variant_phenotype_scope_revision"
CURRENT = f"20260801+{MONDO_HPO_INGESTION_REVISION}"
MONDO_HET = "rsYELIZTLI2577001"
MONDO_HOM = "rsYELIZTLI2577002"
OMIM_HET = "rsYELIZTLI2577003"
OMIM_HOM = "rsYELIZTLI2577004"
NON_CARRIER = "rsYELIZTLI2577005"
Y_CALL = "rsYELIZTLI2577006"
EXPECTED = {MONDO_HET, MONDO_HOM, OMIM_HET}


def _row(rsid, pos, source="mondo_hpo", zygosity="het", chrom="1"):
    row = dict.fromkeys(annotated_variants.c.keys())
    row.update(
        rsid=rsid,
        chrom=chrom,
        pos=pos,
        ref="G",
        alt="A",
        genotype={"het": "GA", "hom_alt": "AA", "hom_ref": "GG"}[zygosity],
        zygosity=zygosity,
        gene_symbol="SYNTHETIC",
        consequence="missense_variant",
        gnomad_af_global=0.00001,
        gnomad_af_popmax=0.00001,
        gnomad_homozygous_count=0,
        clinvar_significance="Pathogenic",
        clinvar_review_stars=2,
        phenotype_source=source,
        disease_name=f"Synthetic {source} disease",
        inheritance_pattern="Autosomal dominant",
        annotation_coverage=0,
    )
    return row


def _legacy_sample(tmp_path: Path, snapshot="20260101"):
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    settings.samples_dir.mkdir(parents=True)
    reference = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(reference)
    with reference.begin() as conn:
        individual_id = conn.execute(
            individuals.insert().values(display_name="Synthetic individual", biological_sex="XX")
        ).inserted_primary_key[0]
        conn.execute(
            samples.insert().values(
                id=1,
                name="Synthetic sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                individual_id=individual_id,
            )
        )
    reference.dispose()
    engine = sa.create_engine(f"sqlite:///{settings.samples_dir / 'sample_1.db'}")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            annotated_variants.insert(),
            [
                _row(MONDO_HET, 2577001),
                _row(MONDO_HOM, 2577002, zygosity="hom_alt"),
                _row(OMIM_HET, 2577003, source="omim"),
                _row(OMIM_HOM, 2577004, source="omim", zygosity="hom_alt"),
                _row(NON_CARRIER, 2577005, zygosity="hom_ref"),
                _row(Y_CALL, 2577006, chrom="Y"),
            ],
        )
        if snapshot is not None:
            conn.execute(
                annotation_state.insert().values(
                    key=REFERENCE_VERSION_SNAPSHOT_KEY,
                    value=json.dumps({"mondo_hpo": snapshot}),
                )
            )
    # Reproduce the old producer: it saw the pooled inheritance before the
    # legacy-scope mask was introduced. Use the real finder and storage path.
    with patch(
        "backend.analysis.rare_variant_finder.sample_phenotype_scope_is_current",
        return_value=True,
    ):
        old = find_rare_variants(
            RareVariantFilter(carried_only=True, inferred_sex="XX", biological_sex="XX"),
            engine,
        )
    assert {v.rsid for v in old.variants} == {MONDO_HET, OMIM_HET}
    store_rare_variant_findings(old, engine)
    with engine.begin() as conn:
        # Released findings have no corrected-producer revision marker.
        conn.execute(annotation_state.delete().where(annotation_state.c.key == REVISION_KEY))
        conn.execute(
            findings.insert().values(module="unrelated", finding_text="Keep this finding")
        )
    engine.dispose()
    return settings


@pytest.mark.parametrize("snapshot", [None, "20260101", "invalid"])
@pytest.mark.parametrize("first_reader", ["findings", "export/tsv", "export/vcf"])
def test_first_reader_repairs_membership_and_fields(tmp_path, snapshot, first_reader):
    settings = _legacy_sample(tmp_path, snapshot)
    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        with TestClient(create_app()) as client:
            first = client.get(f"/api/analysis/rare-variants/{first_reader}?sample_id=1")
            assert first.status_code == 200, first.text
            response = client.get("/api/analysis/rare-variants/findings?sample_id=1")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 3
            items = {item["rsid"]: item for item in payload["items"]}
            assert set(items) == EXPECTED
            for rsid in (MONDO_HET, MONDO_HOM):
                assert items[rsid]["detail"]["disease_name"] is None
                assert items[rsid]["detail"]["inheritance_pattern"] is None
            assert items[OMIM_HET]["detail"]["disease_name"] == "Synthetic omim disease"
            assert items[OMIM_HET]["detail"]["inheritance_pattern"] == "Autosomal dominant"
            for extension in ("tsv", "vcf"):
                export = client.get(f"/api/analysis/rare-variants/export/{extension}?sample_id=1")
                assert export.status_code == 200
                assert all(rsid in export.text for rsid in EXPECTED)
                assert all(rsid not in export.text for rsid in (OMIM_HOM, NON_CARRIER, Y_CALL))
        reset_registry()


def test_registry_repairs_before_generic_readers_and_only_once(tmp_path):
    settings = _legacy_sample(tmp_path)
    registry = DBRegistry(settings)
    path = settings.samples_dir / "sample_1.db"
    try:
        engine = registry.get_sample_engine(path)
        with engine.connect() as conn:
            rows = conn.execute(sa.select(findings).order_by(findings.c.id)).all()
            assert {r.rsid for r in rows if r.module == "rare_variants"} == EXPECTED
            assert [r.finding_text for r in rows if r.module == "unrelated"] == [
                "Keep this finding"
            ]
            assert conn.execute(
                sa.select(annotation_state.c.value).where(annotation_state.c.key == REVISION_KEY)
            ).scalar_one()
        registry.dispose_sample_engine(path)
        with registry.get_sample_engine(path).connect() as conn:
            assert conn.execute(sa.select(findings).order_by(findings.c.id)).all() == rows
    finally:
        registry.dispose_all()


def test_current_scope_keeps_its_original_findings(tmp_path):
    settings = _legacy_sample(tmp_path, CURRENT)
    registry = DBRegistry(settings)
    try:
        with registry.get_sample_engine(settings.samples_dir / "sample_1.db").connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "rare_variants")
            ).all()
            assert {row.rsid for row in rows} == {MONDO_HET, OMIM_HET}
            assert all(json.loads(row.detail_json)["disease_name"] for row in rows)
    finally:
        registry.dispose_all()


def test_old_empty_derivation_recovers_missing_membership(tmp_path):
    settings = _legacy_sample(tmp_path)
    path = settings.samples_dir / "sample_1.db"
    raw = sa.create_engine(f"sqlite:///{path}")
    with raw.begin() as conn:
        conn.execute(findings.delete().where(findings.c.module == "rare_variants"))
    raw.dispose()
    registry = DBRegistry(settings)
    try:
        with registry.get_sample_engine(path).connect() as conn:
            assert (
                set(
                    conn.execute(
                        sa.select(findings.c.rsid).where(findings.c.module == "rare_variants")
                    ).scalars()
                )
                == EXPECTED
            )
    finally:
        registry.dispose_all()


def test_empty_corrected_result_is_stamped(tmp_path):
    settings = _legacy_sample(tmp_path)
    path = settings.samples_dir / "sample_1.db"
    raw = sa.create_engine(f"sqlite:///{path}")
    with raw.begin() as conn:
        conn.execute(annotated_variants.update().values(zygosity="hom_ref", genotype="GG"))
    raw.dispose()
    registry = DBRegistry(settings)
    try:
        with registry.get_sample_engine(path).connect() as conn:
            assert (
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(findings)
                    .where(findings.c.module == "rare_variants")
                ).scalar_one()
                == 0
            )
            assert conn.execute(
                sa.select(annotation_state.c.value).where(annotation_state.c.key == REVISION_KEY)
            ).scalar_one()
        registry.dispose_sample_engine(path)
        with patch("backend.services.rare_variant_findings.find_rare_variants") as finder:
            registry.get_sample_engine(path)
        finder.assert_not_called()
    finally:
        registry.dispose_all()


def test_non_mondo_sample_is_untouched(tmp_path):
    settings = _legacy_sample(tmp_path)
    path = settings.samples_dir / "sample_1.db"
    raw = sa.create_engine(f"sqlite:///{path}")
    with raw.begin() as conn:
        conn.execute(annotated_variants.update().values(phenotype_source="omim"))
        before = conn.execute(sa.select(findings).order_by(findings.c.id)).all()
    raw.dispose()
    registry = DBRegistry(settings)
    try:
        with registry.get_sample_engine(path).connect() as conn:
            assert conn.execute(sa.select(findings).order_by(findings.c.id)).all() == before
    finally:
        registry.dispose_all()


def test_recorded_xy_sex_keeps_y_finding(tmp_path):
    settings = _legacy_sample(tmp_path)
    registry = DBRegistry(settings)
    try:
        with registry.reference_engine.begin() as conn:
            conn.execute(individuals.update().values(biological_sex="XY"))
        with registry.get_sample_engine(settings.samples_dir / "sample_1.db").connect() as conn:
            assert set(
                conn.execute(
                    sa.select(findings.c.rsid).where(findings.c.module == "rare_variants")
                ).scalars()
            ) == EXPECTED | {Y_CALL}
    finally:
        registry.dispose_all()


@pytest.mark.parametrize("failure", ["revision", "finding", "provenance"])
def test_failed_repair_rolls_back_and_does_not_publish_engine(tmp_path, failure):
    settings = _legacy_sample(tmp_path)
    path = settings.samples_dir / "sample_1.db"
    raw = sa.create_engine(f"sqlite:///{path}")
    with raw.begin() as conn:
        before = conn.execute(sa.select(findings).order_by(findings.c.id)).all()
        table, condition = (
            ("annotation_state", f"NEW.key = '{REVISION_KEY}'")
            if failure == "revision"
            else ("findings", "NEW.module = 'rare_variants'")
        )
        operation = "UPDATE OF provenance" if failure == "provenance" else "INSERT"
        conn.exec_driver_sql(
            f"CREATE TRIGGER reject_repair BEFORE {operation} ON {table} WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
        )
    registry = DBRegistry(settings)
    try:
        with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
            registry.get_sample_engine(path)
        with raw.begin() as conn:
            assert conn.execute(sa.select(findings).order_by(findings.c.id)).all() == before
            assert (
                conn.execute(
                    sa.select(annotation_state.c.value).where(
                        annotation_state.c.key == REVISION_KEY
                    )
                ).first()
                is None
            )
            conn.exec_driver_sql("DROP TRIGGER reject_repair")
        with registry.get_sample_engine(path).connect() as conn:
            assert (
                set(
                    conn.execute(
                        sa.select(findings.c.rsid).where(findings.c.module == "rare_variants")
                    ).scalars()
                )
                == EXPECTED
            )
    finally:
        raw.dispose()
        registry.dispose_all()


@pytest.mark.parametrize("spelling", ["dot", "parent", "absolute", "merged"])
def test_normalized_legacy_paths_keep_registered_sex(tmp_path, spelling):
    settings = _legacy_sample(tmp_path)
    registry = DBRegistry(settings)
    path = settings.samples_dir / "sample_1.db"
    if spelling == "merged":
        target = settings.samples_dir / "merged_synthetic.db"
        path.rename(target)
        path = target
    value = {
        "dot": "./samples/sample_1.db",
        "parent": "samples/../samples/sample_1.db",
        "absolute": str(path.parent / ".." / "samples" / path.name),
        "merged": "./samples/merged_synthetic.db",
    }[spelling]
    try:
        with registry.reference_engine.begin() as conn:
            conn.execute(samples.update().values(db_path=value))
        with registry.get_sample_engine(path).connect() as conn:
            assert (
                set(
                    conn.execute(
                        sa.select(findings.c.rsid).where(findings.c.module == "rare_variants")
                    ).scalars()
                )
                == EXPECTED
            )
    finally:
        registry.dispose_all()


def test_normalized_duplicate_identity_fails_closed(tmp_path):
    settings = _legacy_sample(tmp_path)
    registry = DBRegistry(settings)
    try:
        with registry.reference_engine.begin() as conn:
            conn.execute(
                samples.insert().values(
                    name="Synthetic duplicate",
                    db_path="./samples/sample_1.db",
                    file_format="23andme_v5",
                )
            )
        with pytest.raises(ValueError, match="Cannot identify legacy sample"):
            registry.get_sample_engine(settings.samples_dir / "sample_1.db")
    finally:
        registry.dispose_all()


def test_rebuild_preserves_provenance_and_uses_recorded_source_versions(tmp_path):
    settings = _legacy_sample(tmp_path)
    path = settings.samples_dir / "sample_1.db"
    raw = sa.create_engine(f"sqlite:///{path}")
    old_provenance = {
        rsid: json.dumps(
            {
                "pipeline_version": "synthetic-old-producer",
                "pipeline_genome_build": "GRCh37",
                "sources": {
                    "clinvar": {"version": "synthetic-annotated-v1", "genome_build": "GRCh37"}
                },
                "variation_ids": {"rsid": rsid},
                "annotation_coverage": 2,
            },
            indent=2,
        )
        for rsid in (MONDO_HET, OMIM_HET)
    }
    with raw.begin() as conn:
        for rsid, provenance in old_provenance.items():
            conn.execute(
                findings.update().where(findings.c.rsid == rsid).values(provenance=provenance)
            )
        conn.execute(
            annotation_state.update()
            .where(annotation_state.c.key == REFERENCE_VERSION_SNAPSHOT_KEY)
            .values(
                value=json.dumps({"mondo_hpo": "20260101", "clinvar": "synthetic-annotated-v1"})
            )
        )
        conn.execute(annotated_variants.update().values(annotation_coverage=2))
    registry = DBRegistry(settings)
    try:
        with registry.reference_engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="clinvar", version="synthetic-installed-v2", genome_build="GRCh38"
                )
            )
        with registry.get_sample_engine(path).connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "rare_variants")
            ).all()
            assert {row.rsid for row in rows} == EXPECTED
            by_rsid = {row.rsid: row.provenance for row in rows}
            for rsid in (MONDO_HET, OMIM_HET):
                assert by_rsid[rsid] == old_provenance[rsid]
            recovered = json.loads(by_rsid[MONDO_HOM])
            assert recovered["sources"]["clinvar"] == {
                "version": "synthetic-annotated-v1",
                "genome_build": None,
            }
            assert recovered["sources"]["mondo_hpo"]["version"] == "20260101"
            assert recovered["variation_ids"]["rsid"] == MONDO_HOM
            assert recovered["annotation_coverage"] == 2
            assert recovered["annotation_coverage_sources"] == ["ClinVar"]
            assert "synthetic-installed-v2" not in by_rsid[MONDO_HOM]
    finally:
        raw.dispose()
        registry.dispose_all()


def test_unregistered_identity_fails_before_changing_findings(tmp_path):
    settings = _legacy_sample(tmp_path)
    registry = DBRegistry(settings)
    path = settings.samples_dir / "sample_1.db"
    raw = sa.create_engine(f"sqlite:///{path}")
    try:
        with raw.connect() as conn:
            before = conn.execute(sa.select(findings).order_by(findings.c.id)).all()
        with registry.reference_engine.begin() as conn:
            conn.execute(samples.update().values(db_path="samples/different.db"))
        with pytest.raises(ValueError, match="Cannot identify legacy sample"):
            registry.get_sample_engine(path)
        with raw.connect() as conn:
            assert conn.execute(sa.select(findings).order_by(findings.c.id)).all() == before
    finally:
        raw.dispose()
        registry.dispose_all()


def test_competing_registry_opens_rebuild_only_once(tmp_path):
    settings = _legacy_sample(tmp_path)
    path = settings.samples_dir / "sample_1.db"
    raw = sa.create_engine(f"sqlite:///{path}")
    with raw.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE repair_audit (revision TEXT)")
        conn.exec_driver_sql(
            "CREATE TRIGGER count_repair AFTER INSERT ON annotation_state "
            f"WHEN NEW.key = '{REVISION_KEY}' "
            "BEGIN INSERT INTO repair_audit VALUES (NEW.value); END"
        )
    registries = [DBRegistry(settings), DBRegistry(settings)]
    barrier = Barrier(2)

    def open_sample(registry):
        barrier.wait(timeout=10)
        with registry.get_sample_engine(path).connect() as conn:
            return set(
                conn.execute(
                    sa.select(findings.c.rsid).where(findings.c.module == "rare_variants")
                ).scalars()
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(open_sample, registries))
        assert results == [EXPECTED, EXPECTED]
        with raw.connect() as conn:
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM repair_audit").scalar_one() == 1
    finally:
        raw.dispose()
        for registry in registries:
            registry.dispose_all()
