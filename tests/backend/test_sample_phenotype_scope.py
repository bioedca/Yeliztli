"""Legacy MONDO/HPO phenotype columns are withheld by every sample reader (#2317).

#2238 made MONDO/HPO annotations disease-scoped and withheld a pre-migration
sample's stored gene-wide columns in variant detail only. These tests pin the
remaining readers - ``/api/annotations``, the query builder, its export and the
rare-variant finder - to the same rule, asserting the returned *set* where the
reader filters on those columns, not only the displayed fields.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.rare_variant_finder import RareVariantFilter, find_rare_variants
from backend.annotation.mondo_hpo import MONDO_HPO_INGESTION_REVISION
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, annotation_state, reference_metadata, samples
from backend.services.sample_phenotype_scope import (
    LEGACY_SCOPED_PHENOTYPE_FIELDS,
    sample_phenotype_scope_is_current,
    withhold_legacy_phenotype_fields,
)
from backend.services.staleness import REFERENCE_VERSION_SNAPSHOT_KEY

_CURRENT_MONDO_HPO = f"20260801+{MONDO_HPO_INGESTION_REVISION}"
_LEGACY_MONDO_HPO = "20260101"

_AD = "Autosomal dominant"


def _row(rsid: str, pos: int, **overrides: object) -> dict:
    row = {col.name: None for col in annotated_variants.columns}
    row.update(
        rsid=rsid,
        chrom="17",
        pos=pos,
        ref="G",
        alt="A",
        genotype="GA",
        zygosity="het",
        gene_symbol="NF1",
        consequence="stop_gained",
        gnomad_af_global=3.98e-6,
        gnomad_af_popmax=3.98e-6,
        gnomad_homozygous_count=0,
        clinvar_significance="Pathogenic",
        ensemble_pathogenic=False,
        evidence_conflict=False,
        annotation_coverage=0,
    )
    row.update(overrides)
    return row


# One row per phenotype source: the gene-wide MONDO/HPO merge that is withheld,
# OMIM enrichment that writes the same columns and is kept, and no source.
_MONDO = _row(
    "rs_mondo",
    1001,
    phenotype_source="mondo_hpo",
    disease_name="Gene-wide pooled disease",
    disease_id="MONDO:0000001",
    hpo_terms="HP:0000001",
    inheritance_pattern=_AD,
)
_OMIM = _row(
    "rs_omim",
    1002,
    phenotype_source="omim",
    disease_name="OMIM disease",
    disease_id="OMIM:100000",
    inheritance_pattern=_AD,
)
_UNSOURCED = _row("rs_unsourced", 1003)
_ROWS = [_MONDO, _OMIM, _UNSOURCED]


def _record_snapshot(engine: sa.Engine, mondo_hpo: str | None) -> None:
    """Record the run's reference snapshot; ``None`` leaves none recorded."""
    if mondo_hpo is None:
        return
    with engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": REFERENCE_VERSION_SNAPSHOT_KEY, "value": json.dumps({"mondo_hpo": mondo_hpo})},
        )


def _client(tmp_data_dir: Path, mondo_hpo: str | None) -> Iterator[tuple[TestClient, int]]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        sample_id = conn.execute(
            samples.insert().values(
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc123",
            )
        ).lastrowid
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{tmp_data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(annotated_variants.insert(), _ROWS)
    _record_snapshot(sample_engine, mondo_hpo)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        with TestClient(create_app()) as tc:
            yield tc, sample_id
        reset_registry()


@pytest.fixture
def legacy_client(tmp_data_dir: Path):
    yield from _client(tmp_data_dir, _LEGACY_MONDO_HPO)


@pytest.fixture
def unrecorded_client(tmp_data_dir: Path):
    yield from _client(tmp_data_dir, None)


@pytest.fixture
def current_client(tmp_data_dir: Path):
    yield from _client(tmp_data_dir, _CURRENT_MONDO_HPO)


def _by_rsid(items: list[dict]) -> dict[str, dict]:
    assert items, "reader returned no rows"
    return {item["rsid"]: item for item in items}


def _assert_mondo_withheld(item: dict) -> None:
    # Every reader serves disease and inheritance; the query builder's row
    # model carries no other phenotype field, so check whichever are present.
    assert {"disease_name", "inheritance_pattern"} <= item.keys()
    for field in LEGACY_SCOPED_PHENOTYPE_FIELDS:
        assert item.get(field) is None, field


def _assert_omim_kept(item: dict) -> None:
    assert item.get("phenotype_source", "omim") == "omim"
    assert item["disease_name"] == "OMIM disease"
    assert item["inheritance_pattern"] == _AD


_INHERITANCE_IS_AD = {
    "combinator": "and",
    "rules": [{"field": "inheritance_pattern", "operator": "=", "value": _AD}],
}
_DISEASE_IS_NULL = {
    "combinator": "and",
    "rules": [{"field": "disease_name", "operator": "null", "value": None}],
}


# ── Scope verdict ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mondo_hpo", "expected"),
    [(None, False), (_LEGACY_MONDO_HPO, False), (_CURRENT_MONDO_HPO, True)],
)
def test_scope_verdict_reads_the_samples_own_snapshot_and_fails_closed(
    sample_engine: sa.Engine, mondo_hpo: str | None, expected: bool
) -> None:
    _record_snapshot(sample_engine, mondo_hpo)
    assert sample_phenotype_scope_is_current(sample_engine) is expected


def test_withholding_touches_only_mondo_hpo_rows() -> None:
    mondo, omim, unsourced = dict(_MONDO), dict(_OMIM), dict(_UNSOURCED)
    for data in (mondo, omim, unsourced):
        withhold_legacy_phenotype_fields(data)
    _assert_mondo_withheld(mondo)
    assert mondo["rsid"] == "rs_mondo"
    assert omim == _OMIM
    assert unsourced == _UNSOURCED


# ── /api/annotations ──────────────────────────────────────────────────


class TestAnnotationsApi:
    def test_withholds_legacy_mondo_hpo_and_keeps_omim(self, legacy_client) -> None:
        tc, sid = legacy_client
        items = _by_rsid(tc.get(f"/api/annotations?sample_id={sid}").json()["items"])
        assert set(items) == {"rs_mondo", "rs_omim", "rs_unsourced"}
        _assert_mondo_withheld(items["rs_mondo"])
        _assert_omim_kept(items["rs_omim"])

    def test_fails_closed_without_a_recorded_snapshot(self, unrecorded_client) -> None:
        tc, sid = unrecorded_client
        items = _by_rsid(tc.get(f"/api/annotations?sample_id={sid}").json()["items"])
        _assert_mondo_withheld(items["rs_mondo"])
        _assert_omim_kept(items["rs_omim"])

    def test_serves_scoped_mondo_hpo(self, current_client) -> None:
        tc, sid = current_client
        items = _by_rsid(tc.get(f"/api/annotations?sample_id={sid}").json()["items"])
        assert items["rs_mondo"]["disease_name"] == "Gene-wide pooled disease"
        assert items["rs_mondo"]["inheritance_pattern"] == _AD


# ── Query builder and its export ──────────────────────────────────────


def _query(tc: TestClient, sid: int, rule_group: dict) -> dict[str, dict]:
    response = tc.post("/api/query", json={"sample_id": sid, "filter": rule_group})
    assert response.status_code == 200, response.text
    return {item["rsid"]: item for item in response.json()["items"]}


def _export(tc: TestClient, sid: int, rule_group: dict) -> dict[str, dict]:
    response = tc.post(
        "/api/export/query", json={"sample_id": sid, "filter": rule_group, "format": "json"}
    )
    assert response.status_code == 200, response.text
    return {item["rsid"]: item for item in response.json()}


@pytest.mark.parametrize("reader", [_query, _export], ids=["query", "export"])
class TestQueryBuilder:
    def test_filter_does_not_select_on_withheld_inheritance(self, legacy_client, reader) -> None:
        tc, sid = legacy_client
        items = reader(tc, sid, _INHERITANCE_IS_AD)
        assert set(items) == {"rs_omim"}
        _assert_omim_kept(items["rs_omim"])

    def test_null_filter_sees_the_withheld_value(self, legacy_client, reader) -> None:
        tc, sid = legacy_client
        items = reader(tc, sid, _DISEASE_IS_NULL)
        assert set(items) == {"rs_mondo", "rs_unsourced"}
        _assert_mondo_withheld(items["rs_mondo"])

    def test_unfiltered_rows_are_withheld_in_the_response(self, unrecorded_client, reader) -> None:
        tc, sid = unrecorded_client
        items = reader(tc, sid, {"combinator": "and", "rules": []})
        assert set(items) == {"rs_mondo", "rs_omim", "rs_unsourced"}
        _assert_mondo_withheld(items["rs_mondo"])
        _assert_omim_kept(items["rs_omim"])

    def test_scoped_sample_filters_on_its_mondo_hpo_values(self, current_client, reader) -> None:
        tc, sid = current_client
        items = reader(tc, sid, _INHERITANCE_IS_AD)
        assert set(items) == {"rs_mondo", "rs_omim"}
        assert items["rs_mondo"]["disease_name"] == "Gene-wide pooled disease"


# ── Rare-variant finder ───────────────────────────────────────────────


def _hom_alt(row: dict) -> dict:
    return {**row, "genotype": "AA", "zygosity": "hom_alt"}


@pytest.fixture
def hom_alt_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Unsupported P/LP hom-alt calls the AD plausibility guard suppresses."""
    with sample_engine.begin() as conn:
        conn.execute(annotated_variants.insert(), [_hom_alt(_MONDO), _hom_alt(_OMIM)])
    return sample_engine


@pytest.mark.parametrize("mondo_hpo", [_LEGACY_MONDO_HPO, None], ids=["legacy", "unrecorded"])
def test_finder_guard_does_not_act_on_legacy_inheritance(
    hom_alt_sample: sa.Engine, mondo_hpo: str | None
) -> None:
    _record_snapshot(hom_alt_sample, mondo_hpo)

    result = find_rare_variants(RareVariantFilter(carried_only=True), hom_alt_sample)

    # The OMIM row's own inheritance still drives the guard; the withheld
    # gene-wide MONDO/HPO value no longer decides membership either way.
    assert [variant.rsid for variant in result.variants] == ["rs_mondo"]
    assert result.variants[0].disease_name is None
    assert result.variants[0].inheritance_pattern is None
    assert result.hom_alt_plausibility_suppressed == 1


def test_finder_guard_acts_on_scoped_inheritance(hom_alt_sample: sa.Engine) -> None:
    _record_snapshot(hom_alt_sample, _CURRENT_MONDO_HPO)

    result = find_rare_variants(RareVariantFilter(carried_only=True), hom_alt_sample)

    assert result.variants == []
    assert result.hom_alt_plausibility_suppressed == 2


def test_finder_returns_scoped_phenotype_fields(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(annotated_variants.insert(), [_MONDO, _OMIM])
    _record_snapshot(sample_engine, _CURRENT_MONDO_HPO)

    result = find_rare_variants(RareVariantFilter(carried_only=True), sample_engine)

    by_rsid = {variant.rsid: variant for variant in result.variants}
    assert set(by_rsid) == {"rs_mondo", "rs_omim"}
    assert by_rsid["rs_mondo"].disease_name == "Gene-wide pooled disease"
    assert by_rsid["rs_mondo"].inheritance_pattern == _AD
    assert by_rsid["rs_omim"].inheritance_pattern == _AD
