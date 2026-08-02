"""Regression coverage for non-destructive CYP2D6/tamoxifen audit retention (#2019)."""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings


def _held_alert(*, row_id: int, detail: dict[str, object]) -> dict[str, object]:
    """Create a legacy-looking source record that must remain available for audit."""
    return {
        "id": row_id,
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2D6",
        "diplotype": "*1/*4",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "tamoxifen",
        "finding_text": "Legacy CYP2D6/tamoxifen source record",
        "detail_json": json.dumps(detail),
        "provenance": None,
    }


def test_v25_retains_legacy_and_custom_audit_records_and_diff_state(
    sample_engine: sa.Engine,
) -> None:
    """No source shape can authorize destructive removal of a held clinical record."""
    rows = [
        _held_alert(
            row_id=1,
            detail={
                "recommendation": "legacy CYP2D6/tamoxifen guidance",
                "classification": "A",
                "guideline_url": "https://example.test/cpic",
                "call_confidence": "Partial",
                "confidence_note": "Local clinician audit annotation.",
            },
        ),
        _held_alert(
            row_id=2,
            detail={
                "legacy": {"gene": "CYP2D6", "drug": "tamoxifen"},
                "coverage": {"assessed": 1, "total": 1},
                "local_audit_note": "Retain this source exactly.",
            },
        ),
    ]
    diff = {
        "schema_version": 1,
        "changed": [
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2D6",
                "drug": "tamoxifen",
                "local_audit_note": "Retain this diff context.",
            }
        ],
        "added": [
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2D6",
                "drug": "tamoxifen",
                "local_audit_note": "Retain this added diff context.",
            }
        ],
        "removed": [
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2D6",
                "drug": "tamoxifen",
                "local_audit_note": "Retain this removed diff context.",
            }
        ],
        "counts": {"changed": 1, "added": 1, "removed": 1},
    }
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        before_findings = list(
            conn.execute(sa.select(findings).order_by(findings.c.id)).mappings()
        )
        before_diff = conn.execute(
            sa.select(annotation_state.c.value).where(
                annotation_state.c.key == "last_finding_diff_json"
            )
        ).scalar_one()
        conn.execute(sa.text("PRAGMA user_version = 24"))

    assert ensure_sample_schema_current(sample_engine) is False

    with sample_engine.connect() as conn:
        after_findings = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        after_diff = conn.execute(
            sa.select(annotation_state.c.value).where(
                annotation_state.c.key == "last_finding_diff_json"
            )
        ).scalar_one()
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after_findings == before_findings
    assert after_diff == before_diff
    assert version == SAMPLE_SCHEMA_VERSION == 25
    assert ensure_sample_schema_current(sample_engine) is False


def test_v25_never_executes_a_destructive_quarantine_delete(sample_engine: sa.Engine) -> None:
    """A deletion trigger must not fire while v25 stamps the retention policy."""
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert(),
            _held_alert(
                row_id=1,
                detail={
                    "legacy": {"gene": "CYP2D6", "drug": "tamoxifen"},
                    "local_audit_note": "A delete would lose this evidence.",
                },
            ),
        )
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_tamoxifen_delete "
                "BEFORE DELETE ON findings "
                "WHEN OLD.gene_symbol = 'CYP2D6' AND OLD.drug = 'tamoxifen' "
                "BEGIN SELECT RAISE(ABORT, 'unexpected destructive quarantine'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 24"))

    assert ensure_sample_schema_current(sample_engine) is False

    with sample_engine.connect() as conn:
        retained = conn.execute(sa.select(findings.c.id).where(findings.c.id == 1)).one_or_none()
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert retained is not None
    assert version == SAMPLE_SCHEMA_VERSION == 25
