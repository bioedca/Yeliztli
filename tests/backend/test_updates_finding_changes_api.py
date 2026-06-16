"""API tests for the finding-level change diff endpoints (SW-A4b / #8).

GET /api/updates/finding-changes and POST /api/updates/finding-changes/dismiss,
reading/writing the per-sample ``annotation_state.last_finding_diff_json`` blob.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from backend.analysis.finding_diff import DIFF_STATE_KEY
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotation_state, apoe_gate, reference_metadata, samples

_DIFF_WITH_CHANGES = {
    "schema_version": 1,
    "before_releases": {"clinvar": "2024-01"},
    "after_releases": {"clinvar": "2024-06"},
    "release_deltas": [{"db_name": "clinvar", "before": "2024-01", "after": "2024-06"}],
    "changed": [
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "gene_symbol": "BRCA1",
            "rsid": "rs80357906",
            "drug": None,
            "diplotype": None,
            "finding_text": "BRCA1 Pathogenic",
            "clinvar_significance": "Pathogenic",
            "evidence_level": 4,
            "metabolizer_status": None,
            "pathway_level": None,
            "changes": [
                {
                    "field": "clinvar_significance",
                    "before": "Uncertain_significance",
                    "after": "Pathogenic",
                }
            ],
        }
    ],
    "added": [
        {
            "module": "parkinsons",
            "category": "lrrk2_risk",
            "gene_symbol": "LRRK2",
            "rsid": "rs34637584",
            "drug": None,
            "diplotype": None,
            "finding_text": "LRRK2 G2019S Parkinson's risk finding",
            "clinvar_significance": None,
            "evidence_level": 4,
            "metabolizer_status": None,
            "pathway_level": None,
        }
    ],
    "removed": [
        {
            "module": "sex_aneuploidy",
            "category": "xxy_screen",
            "gene_symbol": None,
            "rsid": None,
            "drug": None,
            "diplotype": None,
            "finding_text": "Possible XXY sex-chromosome aneuploidy pattern",
            "clinvar_significance": None,
            "evidence_level": 4,
            "metabolizer_status": None,
            "pathway_level": None,
        }
    ],
    "counts": {"changed": 1, "added": 1, "removed": 1},
    "dismissed": False,
    "generated_at": "2026-06-09T00:00:00+00:00",
}

_APOE_CHANGED = {
    "module": "apoe",
    "category": "alzheimers_risk",
    "gene_symbol": "APOE",
    "rsid": None,
    "drug": None,
    "diplotype": "e3/e4",
    "finding_text": "APOE e3/e4 is associated with elevated Alzheimer's risk",
    "clinvar_significance": None,
    "evidence_level": 5,
    "metabolizer_status": None,
    "pathway_level": None,
    "changes": [{"field": "evidence_level", "before": "4", "after": "5"}],
}

_DIFF_WITH_MIXED_GATED_CHANGES = {
    **_DIFF_WITH_CHANGES,
    "changed": [*_DIFF_WITH_CHANGES["changed"], _APOE_CHANGED],
    "counts": {"changed": 2, "added": 1, "removed": 1},
}

_DIFF_WITH_ONLY_GATED_CHANGES = {
    **_DIFF_WITH_CHANGES,
    "changed": [_APOE_CHANGED],
    "added": [],
    "removed": [],
    "counts": {"changed": 1, "added": 0, "removed": 0},
}


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[DBRegistry, None, None]:
    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)

    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "with_diff",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "h1",
                },
                {
                    "name": "no_diff",
                    "db_path": "samples/sample_2.db",
                    "file_format": "23andme_v5",
                    "file_hash": "h2",
                },
                {
                    "name": "only_gated_diff",
                    "db_path": "samples/sample_3.db",
                    "file_format": "23andme_v5",
                    "file_hash": "h3",
                },
            ],
        )

    # sample 1 carries a stored mixed diff; sample 2 has none; sample 3 carries
    # only gated changes, which must be indistinguishable from no visible diff
    # until the relevant disclosure gate is acknowledged.
    s1 = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(s1)
    with s1.begin() as conn:
        conn.execute(
            annotation_state.insert().values(
                key=DIFF_STATE_KEY, value=json.dumps(_DIFF_WITH_MIXED_GATED_CHANGES)
            )
        )
    s1.dispose()

    s2 = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_2.db'}")
    create_sample_tables(s2)
    s2.dispose()

    s3 = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_3.db'}")
    create_sample_tables(s3)
    with s3.begin() as conn:
        conn.execute(
            annotation_state.insert().values(
                key=DIFF_STATE_KEY, value=json.dumps(_DIFF_WITH_ONLY_GATED_CHANGES)
            )
        )
    s3.dispose()

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    with patch("backend.api.routes.risk_common.get_registry", return_value=registry):
        yield registry
    registry.dispose_all()
    reset_registry()


def _get_finding_changes(sample_id: int) -> dict:
    from backend.api.routes.updates import get_finding_changes

    response = asyncio.run(get_finding_changes(sample_id=sample_id))
    return json.loads(response.model_dump_json())


def _dismiss_finding_changes(sample_id: int) -> dict:
    from backend.api.routes.updates import dismiss_finding_changes

    return asyncio.run(dismiss_finding_changes(sample_id=sample_id))


def test_get_finding_changes_returns_diff(_env: DBRegistry) -> None:
    body = _get_finding_changes(1)
    assert body["available"] is True
    assert body["counts"] == {"changed": 1, "added": 0, "removed": 0}
    assert body["added"] == []
    assert body["removed"] == []
    assert body["release_deltas"] == [
        {"db_name": "clinvar", "before": "2024-01", "after": "2024-06"}
    ]
    (changed,) = body["changed"]
    assert changed["gene_symbol"] == "BRCA1"
    assert changed["changes"][0]["after"] == "Pathogenic"
    serialized = json.dumps(body)
    assert "APOE" not in serialized
    assert "LRRK2" not in serialized
    assert "XXY" not in serialized


def test_get_finding_changes_reveals_acknowledged_gated_module(
    _env: DBRegistry,
) -> None:
    sample_db = _env.settings.data_dir / "samples" / "sample_1.db"
    engine = sa.create_engine(f"sqlite:///{sample_db}")
    with engine.begin() as conn:
        conn.execute(
            apoe_gate.insert().values(
                id=1,
                acknowledged=True,
                acknowledged_at=datetime.now(UTC),
            )
        )
    engine.dispose()

    body = _get_finding_changes(1)
    assert body["available"] is True
    assert body["counts"] == {"changed": 2, "added": 0, "removed": 0}
    assert {entry["module"] for entry in body["changed"]} == {"cancer", "apoe"}
    serialized = json.dumps(body)
    assert "APOE e3/e4" in serialized
    assert "LRRK2" not in serialized
    assert "XXY" not in serialized


def test_get_finding_changes_all_gated_is_unavailable(_env: DBRegistry) -> None:
    assert _get_finding_changes(3) == {
        "available": False,
        "generated_at": None,
        "release_deltas": [],
        "changed": [],
        "added": [],
        "removed": [],
        "counts": {},
    }


def test_get_finding_changes_absent_is_unavailable(_env: DBRegistry) -> None:
    assert _get_finding_changes(2) == {
        "available": False,
        "generated_at": None,
        "release_deltas": [],
        "changed": [],
        "added": [],
        "removed": [],
        "counts": {},
    }


def test_dismiss_then_unavailable(_env: DBRegistry) -> None:
    assert _dismiss_finding_changes(1) == {"status": "dismissed", "sample_id": 1}

    assert _get_finding_changes(1)["available"] is False


def test_dismiss_without_diff_returns_404(_env: DBRegistry) -> None:
    with pytest.raises(HTTPException) as excinfo:
        _dismiss_finding_changes(2)
    assert excinfo.value.status_code == 404


def test_unknown_sample_returns_404(_env: DBRegistry) -> None:
    with pytest.raises(HTTPException) as excinfo:
        _get_finding_changes(999)
    assert excinfo.value.status_code == 404
