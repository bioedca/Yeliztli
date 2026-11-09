"""The eBMD page receives its DXA/FRAX safety framing from the API (#2083).

``EBMDView`` renders ``context.not_a_substitute``, ``context.utility`` and
``context.byo`` from ``GET /api/analysis/ebmd/prs``. The content of those
strings is pinned in ``test_ebmd_prs.py``; this guards the route actually
serving them, including when no BYO score is installed (the default state).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.analysis.ebmd_prs import (
    EBMD_CONTEXT,
    EBMD_PGS_ID,
    score_ebmd_prs,
    store_ebmd_findings,
)
from backend.analysis.pgs_bridge import PgsScoreSpec
from backend.annotation.pgs_catalog import (
    create_pgs_tables,
    pgs_score_metadata,
    pgs_score_weights,
)
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, findings, reference_metadata, samples

# The context keys EBMDView.tsx renders.
_RENDERED_CONTEXT_KEYS = ("not_a_substitute", "direction", "utility", "byo")


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from backend.api.routes.ebmd import router

    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)

    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                }
            ],
        )
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    sample_engine.dispose()

    reset_registry()
    registry = DBRegistry(Settings(data_dir=data_dir))
    patchers = [
        patch("backend.api.routes.ebmd.get_registry", return_value=registry),
        patch("backend.api.dependencies.get_registry", return_value=registry),
        patch("backend.services.staleness.get_registry", return_value=registry),
    ]
    for p in patchers:
        p.start()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    try:
        yield TestClient(app)
    finally:
        for p in reversed(patchers):
            p.stop()
        registry.dispose_all()
        reset_registry()


def test_prs_response_serves_the_rendered_safety_framing(client: TestClient) -> None:
    resp = client.get("/api/analysis/ebmd/prs?sample_id=1")
    assert resp.status_code == 200
    body = resp.json()

    # No BYO score installed: the framing must still be served.
    assert body["available"] is False
    context = body["context"]
    for key in _RENDERED_CONTEXT_KEYS:
        assert context.get(key), f"context[{key!r}] is missing or empty"
        assert context[key] == EBMD_CONTEXT[key]
    assert "NOT a substitute for DXA" in context["not_a_substitute"]


def _store_ebmd_prs_finding(tmp_path: Path, detail: dict[str, object]) -> None:
    sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'data' / 'samples' / 'sample_1.db'}")
    try:
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="ebmd",
                    category="prs",
                    evidence_level=1,
                    finding_text="Heel eBMD polygenic score",
                    prs_percentile=None,
                    detail_json=json.dumps(detail),
                )
            )
    finally:
        sample_engine.dispose()


def test_a_never_scored_sample_is_neither_available_nor_withheld(client: TestClient) -> None:
    """#2307: with no stored score, the page must be able to say "not scored yet".

    It can only do that if the response does not dress the empty state up as a
    withheld one; ``scoring_state`` carries "never".
    """
    body = client.get("/api/analysis/ebmd/prs?sample_id=1").json()

    assert body["available"] is False
    assert body["prs"] is None
    assert body["withheld"] is False
    assert body["scoring_state"] == "never"


def test_a_refused_stored_score_is_withheld_not_not_installed(
    client: TestClient, tmp_path: Path
) -> None:
    """#2307: a stored score the presentability guard refuses is withheld.

    ``available`` alone read "not installed" for it, which is the #2297 shape:
    a withheld result presented as an absence the user could act on.
    """
    _store_ebmd_prs_finding(
        tmp_path,
        {"name": "Heel eBMD", "snps_used": 10, "source_study": "CYP2D6 tamoxifen dose guidance"},
    )

    resp = client.get("/api/analysis/ebmd/prs?sample_id=1")
    body = resp.json()

    assert body["withheld"] is True
    assert body["available"] is False
    assert body["prs"] is None
    assert "tamoxifen" not in resp.text.lower()


def test_a_presentable_stored_score_is_available_and_not_withheld(
    client: TestClient, tmp_path: Path
) -> None:
    _store_ebmd_prs_finding(
        tmp_path,
        {
            "name": "Heel eBMD",
            "snps_used": 10,
            "snps_total": 20,
            "source_study": "Synthetic study",
        },
    )

    body = client.get("/api/analysis/ebmd/prs?sample_id=1").json()

    assert body["withheld"] is False
    assert body["available"] is True
    assert body["prs"]["name"] == "Heel eBMD"
    assert body["prs"]["snps_used"] == 10


def test_score_store_and_http_response_preserve_development_provenance(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic weights and registry metadata traverse the real production path.

    The declared variant count intentionally differs from loaded/scored weights:
    provenance must not be replaced with either coverage count (#2423).
    """
    spec = PgsScoreSpec(
        pgs_id=EBMD_PGS_ID,
        module="ebmd",
        name="Synthetic heel score",
        trait_label="heel_ebmd",
        method="Synthetic method",
        multi_ancestry=False,
        ancestries=["EAS"],
        source_study="Synthetic provenance study",
        source_pmid="",
        sample_size=12345,
        license="Synthetic fixture license",
        source_url="https://example.org/synthetic-ebmd-score",
        higher_is="protective",
        bundle_ok=False,
    )
    monkeypatch.setattr(
        "backend.analysis.ebmd_prs.load_pgs_registry", lambda: {"heel_ebmd": [spec]}
    )
    pgs_engine = sa.create_engine("sqlite://")
    sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'data' / 'samples' / 'sample_1.db'}")
    try:
        create_pgs_tables(pgs_engine)
        with pgs_engine.begin() as conn:
            conn.execute(
                pgs_score_metadata.insert().values(
                    pgs_id=EBMD_PGS_ID,
                    pgs_name=spec.name,
                    trait_reported="Synthetic heel trait",
                    genome_build="GRCh37",
                    variants_number=1234,
                    weight_type="beta",
                    license=spec.license,
                    license_bundle_ok=0,
                )
            )
            conn.execute(
                pgs_score_weights.insert(),
                [
                    {
                        "pgs_id": EBMD_PGS_ID,
                        "rsid": f"rsSyntheticEbmd{i}",
                        "chrom": "1",
                        "pos": 700 + i,
                        "effect_allele": "A",
                        "other_allele": "G",
                        "effect_weight": 0.1,
                    }
                    for i in range(3)
                ],
            )
        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rsSyntheticEbmd0",
                    chrom="1",
                    pos=700,
                    genotype="AA",
                    annotation_coverage=0,
                )
            )
        score = score_ebmd_prs(sample_engine, pgs_engine, "EAS")
        assert score is not None
        assert store_ebmd_findings(score, sample_engine) == 1
        expected = {
            "source_ancestry": "EAS",
            "sample_size": 12345,
            "genome_build": "GRCh37",
            "variants_number": 1234,
            "source_url": spec.source_url,
        }
        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.module == "ebmd")).all()
        assert len(rows) == 1
        stored = json.loads(rows[0].detail_json)
        assert {key: stored[key] for key in expected} == expected

        response = client.get("/api/analysis/ebmd/prs?sample_id=1")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True and body["withheld"] is False
        prs = body["prs"]
        assert prs is not None
        assert {key: prs[key] for key in expected} == expected
        assert prs["snps_used"] == 1 and prs["snps_total"] == 3
        assert prs["pgs_license"] == spec.license
        assert body["research_use_only"] is True
        assert body["context"] == EBMD_CONTEXT
    finally:
        sample_engine.dispose()
        pgs_engine.dispose()


@pytest.mark.parametrize("source_ancestry", [None, "unknown"])
def test_legacy_and_unknown_provenance_have_explicit_response_defaults(
    client: TestClient, tmp_path: Path, source_ancestry: str | None
) -> None:
    detail: dict[str, object] = {"name": "Synthetic legacy score", "source_study": "Synthetic"}
    if source_ancestry is not None:
        detail.update(
            source_ancestry=source_ancestry,
            sample_size=0,
            genome_build=None,
            variants_number=None,
            source_url=None,
        )
    _store_ebmd_prs_finding(tmp_path, detail)

    response = client.get("/api/analysis/ebmd/prs?sample_id=1")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True and body["withheld"] is False
    prs = body["prs"]
    assert prs is not None
    assert prs["source_ancestry"] == (source_ancestry or "")
    assert prs["sample_size"] == 0
    assert prs["genome_build"] is None
    assert prs["variants_number"] is None
    assert prs["source_url"] is None
