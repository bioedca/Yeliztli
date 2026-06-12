"""API tests for the warfarin-dosing context endpoint (SW-E1 warfarin layer / #13).

GET /api/analysis/warfarin?sample_id=N
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from backend.analysis.warfarin import CYP4F2_RSID, VKORC1_RSID
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, raw_variants, reference_metadata, samples


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def warfarin_api_response(
    tmp_data_dir: Path,
) -> Generator[Callable[[int], dict], None, None]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    def make_sample(sample_id: int, ancestry: str | None) -> None:
        sample_db_path = tmp_data_dir / "samples" / f"sample_{sample_id}.db"
        sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        create_sample_tables(sample_engine)

        with ref_engine.begin() as conn:
            conn.execute(
                samples.insert().values(
                    id=sample_id,
                    name=f"Test Sample {sample_id}",
                    db_path=f"samples/sample_{sample_id}.db",
                    file_format="v5",
                    file_hash=f"abc123-{sample_id}",
                )
            )
        # VKORC1 G/A (forward CT → lower dose); CYP4F2 *1/*3 (forward CT).
        with sample_engine.begin() as conn:
            conn.execute(
                raw_variants.insert(),
                [
                    {
                        "rsid": VKORC1_RSID,
                        "chrom": "16",
                        "pos": 31107689,
                        "genotype": "CT",
                    },
                    {
                        "rsid": CYP4F2_RSID,
                        "chrom": "19",
                        "pos": 15990431,
                        "genotype": "CT",
                    },
                ],
            )
            if ancestry is not None:
                conn.execute(
                    findings.insert().values(
                        module="ancestry",
                        category="nnls_admixture",
                        evidence_level=1,
                        finding_text=f"Ancestry: {ancestry}",
                        detail_json=json.dumps(
                            {
                                "top_population": ancestry,
                                "admixture_fractions": {ancestry: 0.9},
                            }
                        ),
                    )
                )
        sample_engine.dispose()

    make_sample(1, "EUR")
    make_sample(2, "AFR")
    make_sample(3, None)

    ref_engine.dispose()

    reset_registry()
    registry = DBRegistry(settings)
    with (
        patch("backend.api.dependencies.get_registry", return_value=registry),
        patch("backend.api.routes.risk_common.get_registry", return_value=registry),
        patch("backend.services.staleness.get_registry", return_value=registry),
    ):
        from backend.api.routes.warfarin import get_warfarin

        def _call(sample_id: int) -> dict:
            return get_warfarin(sample_id=sample_id).model_dump(mode="json")

        yield _call
    registry.dispose_all()
    reset_registry()


class TestWarfarinEndpoint:
    def test_reports_both_genes_with_directions(
        self, warfarin_api_response: Callable[[int], dict]
    ) -> None:
        data = warfarin_api_response(1)
        genes = {g["gene"]: g for g in data["genes"]}
        assert data["inferred_ancestry"] == "EUR"
        assert genes["VKORC1"]["dose_effect"] == "lower"
        assert genes["VKORC1"]["diplotype"] == "G/A"
        assert genes["CYP4F2"]["dose_effect"] == "higher"
        assert genes["CYP4F2"]["diplotype"] == "*1/*3"
        assert genes["CYP4F2"]["ancestry_context"] == "EUR"
        assert genes["CYP4F2"]["ancestry_warning_text"] is None

    def test_african_ancestry_withholds_cyp4f2_higher_direction(
        self, warfarin_api_response: Callable[[int], dict]
    ) -> None:
        data = warfarin_api_response(2)
        genes = {g["gene"]: g for g in data["genes"]}
        assert data["inferred_ancestry"] == "AFR"
        assert genes["VKORC1"]["dose_effect"] == "lower"
        assert genes["CYP4F2"]["diplotype"] == "*1/*3"
        assert genes["CYP4F2"]["dose_effect"] == "not_established"
        assert "African" in genes["CYP4F2"]["ancestry_warning_text"]

    def test_missing_ancestry_requires_context_without_direction(
        self, warfarin_api_response: Callable[[int], dict]
    ) -> None:
        data = warfarin_api_response(3)
        genes = {g["gene"]: g for g in data["genes"]}
        assert data["inferred_ancestry"] is None
        assert genes["CYP4F2"]["diplotype"] == "*1/*3"
        assert genes["CYP4F2"]["dose_effect"] == "requires_ancestry_context"
        assert genes["CYP4F2"]["ancestry_context"] is None
        assert genes["CYP4F2"]["ancestry_warning_text"] is not None

    def test_context_only_disclosure(self, warfarin_api_response: Callable[[int], dict]) -> None:
        data = warfarin_api_response(1)
        assert data["context_only"] is True
        assert data["any_called"] is True
        assert data["note"]
        assert data["pmid_citations"]

    def test_invalid_sample_returns_404(
        self, warfarin_api_response: Callable[[int], dict]
    ) -> None:
        with pytest.raises(HTTPException) as exc_info:
            warfarin_api_response(999)
        assert exc_info.value.status_code == 404
