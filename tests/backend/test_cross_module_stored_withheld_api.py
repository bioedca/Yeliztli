"""Cards stored before #2310 for a withheld call stop being served (#2310).

A cross-module card is written when its module is scored, and a scorer change
marks no sample stale. So a sample analysed before #2310 still holds, for
example, the "see Metabolic" card its FTO TT homozygote got, although the
scorer withholds that call as Indeterminate. These tests start from rows stored
the way the pre-#2310 builders wrote them and go through the real endpoints:
each module's own page, and the generic findings aggregator.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.cross_module_links import panel_cross_module_links
from backend.analysis.gene_health import load_gene_health_panel
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, reference_metadata, samples


def _stored_rows(fto_genotype: str, drd4_genotype: str, drd4_category: str) -> list[dict]:
    """Cross-module rows exactly as the pre-#2310 builders stored them."""
    note = panel_cross_module_links(load_gene_health_panel())["rs9939609"]["note"]
    return [
        {
            "module": "gene_health",
            "category": "cross_module",
            "evidence_level": 2,
            "gene_symbol": "FTO",
            "rsid": "rs9939609",
            "finding_text": f"FTO intron 1 ({fto_genotype}) — {note}",
            "pmid_citations": json.dumps([]),
            "detail_json": json.dumps(
                {
                    "genotype": fto_genotype,
                    "source_pathway": "Metabolic",
                    "target_module": "metabolic",
                    "cross_module_note": note,
                }
            ),
        },
        {
            "module": "traits",
            "category": "cross_module",
            "evidence_level": 1,
            "gene_symbol": "DRD4",
            "rsid": "rs747302",
            "finding_text": f"DRD4 ({drd4_genotype}) — See Gene Health module.",
            "pmid_citations": json.dumps([]),
            "detail_json": json.dumps(
                {
                    "genotype": drd4_genotype,
                    "category": drd4_category,
                    "trait_domain": "behavioral",
                    "to_module": "gene_health",
                    "link_type": "related",
                    "note": "probe",
                }
            ),
        },
    ]


@pytest.fixture
def stored_client(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Generator[tuple[TestClient, int], None, None]:
    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)
    settings = Settings(data_dir=data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        sample_id = conn.execute(
            samples.insert().values(
                name="stored",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_stored",
            )
        ).lastrowid
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        for row in _stored_rows(*request.param):
            conn.execute(findings.insert().values(**row))
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        DBRegistry(settings)

        from backend.main import create_app

        with TestClient(create_app()) as client:
            yield client, sample_id

        reset_registry()


# (FTO genotype, DRD4 genotype, DRD4 category as stored) -> whether cards show.
WITHHELD = ("TT", "GG", "Indeterminate")
CARRIER = ("AT", "CG", "Moderate")


def _page_rsids(client: TestClient, module: str, sample_id: int) -> list[str]:
    resp = client.get(f"/api/analysis/{module}/pathways?sample_id={sample_id}")
    assert resp.status_code == 200, resp.text
    return [card["rsid"] for card in resp.json()["cross_module"]]


def _aggregated_cross_module_rsids(client: TestClient, sample_id: int) -> list[str]:
    resp = client.get(f"/api/analysis/findings?sample_id={sample_id}&limit=500")
    assert resp.status_code == 200, resp.text
    return sorted(row["rsid"] for row in resp.json() if row["category"] == "cross_module")


@pytest.mark.parametrize("stored_client", [WITHHELD], indirect=True)
def test_cards_stored_for_withheld_calls_are_not_served(
    stored_client: tuple[TestClient, int],
) -> None:
    client, sample_id = stored_client
    assert _page_rsids(client, "gene_health", sample_id) == []
    assert _page_rsids(client, "traits", sample_id) == []
    assert _aggregated_cross_module_rsids(client, sample_id) == []


@pytest.mark.parametrize("stored_client", [CARRIER], indirect=True)
def test_cards_stored_for_carrier_calls_are_still_served(
    stored_client: tuple[TestClient, int],
) -> None:
    """The control: same rows, determinate carrier genotypes, every card shown."""
    client, sample_id = stored_client
    assert _page_rsids(client, "gene_health", sample_id) == ["rs9939609"]
    assert _page_rsids(client, "traits", sample_id) == ["rs747302"]
    assert _aggregated_cross_module_rsids(client, sample_id) == ["rs747302", "rs9939609"]
