"""Tests for column preset profiles API (P1-15c)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.api.routes.column_presets import PREDEFINED_PRESETS
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import reference_metadata

DOC_COLUMN_LABELS = {
    "genotype": "Genotype",
    "gene_symbol": "Gene",
    "consequence": "Consequence",
    "clinvar_significance": "ClinVar significance",
    "clinvar_review_stars": "ClinVar review stars",
    "cadd_phred": "CADD",
    "sift_score": "SIFT score/prediction",
    "sift_pred": "SIFT score/prediction",
    "polyphen2_hsvar_score": "PolyPhen-2 score/prediction",
    "polyphen2_hsvar_pred": "PolyPhen-2 score/prediction",
    "revel": "REVEL",
    "ensemble_pathogenic": "ensemble pathogenic flag",
    "gnomad_af_global": "global gnomAD AF",
    "rare_flag": "rare flag",
}


def _describe_columns_for_docs(column_ids: list[str]) -> str:
    labels = []
    seen = set()
    for column_id in column_ids:
        label = DOC_COLUMN_LABELS[column_id]
        if label not in seen:
            labels.append(label)
            seen.add(label)
    return ", ".join(labels)


def _variant_explorer_preset_rows() -> dict[str, str]:
    docs_path = Path(__file__).resolve().parents[2] / "docs/features/variant-explorer.md"
    table_rows: dict[str, str] = {}
    in_table = False

    for line in docs_path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "| Preset | Columns shown |":
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("|--------"):
            continue
        if not line.startswith("|"):
            break

        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2:
            table_rows[cells[0]] = re.sub(r"\s+", " ", cells[1])

    return table_rows


def test_variant_explorer_documents_grch38_coordinate_columns() -> None:
    """The optional GRCh38 toggle adds liftover coordinate columns beside the
    default GRCh37 ones, so a user sees two Position numbers per variant. The doc
    must explain the two-assembly view — that the default columns are GRCh37, the
    GRCh38 columns are a computational liftover, and a blank GRCh38 cell means the
    position could not be lifted over (#1591)."""
    docs_path = Path(__file__).resolve().parents[2] / "docs/features/variant-explorer.md"
    text = docs_path.read_text(encoding="utf-8").lower()
    missing = [t for t in ("grch37", "grch38", "liftover", "blank") if t not in text]
    assert not missing, (
        "docs/features/variant-explorer.md no longer documents the GRCh37/GRCh38 "
        f"coordinate columns (missing {missing}) — keep the 'Coordinates & assembly' "
        "section (#1591)."
    )


@pytest.fixture
def preset_client(tmp_data_dir: Path) -> TestClient:
    """TestClient with column_presets.get_settings also patched."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.column_presets.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestListPresets:
    def test_returns_predefined_presets(self, preset_client: TestClient) -> None:
        resp = preset_client.get("/api/column-presets")
        assert resp.status_code == 200
        data = resp.json()
        names = [p["name"] for p in data["presets"]]
        assert "Clinical" in names
        assert "Research" in names
        assert "Frequency" in names
        assert "Scores" in names
        # All predefined
        for p in data["presets"]:
            if p["name"] in ("Clinical", "Research", "Frequency", "Scores"):
                assert p["predefined"] is True

    def test_variant_explorer_docs_match_predefined_presets(self) -> None:
        doc_rows = _variant_explorer_preset_rows()

        assert set(doc_rows) == set(PREDEFINED_PRESETS)
        assert doc_rows == {
            name: _describe_columns_for_docs(column_ids)
            for name, column_ids in PREDEFINED_PRESETS.items()
        }

    def test_includes_custom_presets(self, preset_client: TestClient) -> None:
        preset_client.post(
            "/api/column-presets",
            json={"name": "MyPreset", "columns": ["genotype", "gene_symbol"]},
        )
        resp = preset_client.get("/api/column-presets")
        names = [p["name"] for p in resp.json()["presets"]]
        assert "MyPreset" in names


class TestCreatePreset:
    def test_creates_custom_preset(self, preset_client: TestClient) -> None:
        resp = preset_client.post(
            "/api/column-presets",
            json={"name": "Custom1", "columns": ["genotype", "gene_symbol"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Custom1"
        assert data["columns"] == ["genotype", "gene_symbol"]
        assert data["predefined"] is False

    def test_rejects_duplicate_name(self, preset_client: TestClient) -> None:
        preset_client.post(
            "/api/column-presets",
            json={"name": "Dup", "columns": ["genotype"]},
        )
        resp = preset_client.post(
            "/api/column-presets",
            json={"name": "Dup", "columns": ["genotype"]},
        )
        assert resp.status_code == 409

    def test_rejects_predefined_name(self, preset_client: TestClient) -> None:
        resp = preset_client.post(
            "/api/column-presets",
            json={"name": "Clinical", "columns": ["genotype"]},
        )
        assert resp.status_code == 400

    def test_rejects_empty_columns(self, preset_client: TestClient) -> None:
        resp = preset_client.post(
            "/api/column-presets",
            json={"name": "Empty", "columns": []},
        )
        assert resp.status_code == 422


class TestUpdatePreset:
    def test_updates_columns(self, preset_client: TestClient) -> None:
        preset_client.post(
            "/api/column-presets",
            json={"name": "Editable", "columns": ["genotype"]},
        )
        resp = preset_client.put(
            "/api/column-presets/Editable",
            json={"columns": ["genotype", "gene_symbol", "consequence"]},
        )
        assert resp.status_code == 200
        assert resp.json()["columns"] == ["genotype", "gene_symbol", "consequence"]

    def test_renames_preset(self, preset_client: TestClient) -> None:
        preset_client.post(
            "/api/column-presets",
            json={"name": "OldName", "columns": ["genotype"]},
        )
        resp = preset_client.put(
            "/api/column-presets/OldName",
            json={"new_name": "NewName"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "NewName"

        # Verify old name is gone
        listing = preset_client.get("/api/column-presets").json()
        names = [p["name"] for p in listing["presets"]]
        assert "OldName" not in names
        assert "NewName" in names

    def test_rejects_predefined(self, preset_client: TestClient) -> None:
        resp = preset_client.put(
            "/api/column-presets/Clinical",
            json={"columns": ["genotype"]},
        )
        assert resp.status_code == 400

    def test_rejects_nonexistent(self, preset_client: TestClient) -> None:
        resp = preset_client.put(
            "/api/column-presets/NoSuchPreset",
            json={"columns": ["genotype"]},
        )
        assert resp.status_code == 404


class TestDeletePreset:
    def test_deletes_custom_preset(self, preset_client: TestClient) -> None:
        preset_client.post(
            "/api/column-presets",
            json={"name": "ToDelete", "columns": ["genotype"]},
        )
        resp = preset_client.delete("/api/column-presets/ToDelete")
        assert resp.status_code == 204

        listing = preset_client.get("/api/column-presets").json()
        names = [p["name"] for p in listing["presets"]]
        assert "ToDelete" not in names

    def test_rejects_predefined(self, preset_client: TestClient) -> None:
        resp = preset_client.delete("/api/column-presets/Clinical")
        assert resp.status_code == 400

    def test_rejects_nonexistent(self, preset_client: TestClient) -> None:
        resp = preset_client.delete("/api/column-presets/NoSuchPreset")
        assert resp.status_code == 404
