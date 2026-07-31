"""Tests for the v19 → v20 legacy breast-PRS finding quarantine."""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings


def _finding(*, module: str, category: str, trait: str, text: str) -> dict[str, object]:
    return {
        "module": module,
        "category": category,
        "evidence_level": 1,
        "finding_text": text,
        "detail_json": json.dumps({"trait": trait}),
    }


def test_v20_migration_quarantines_legacy_or_unidentified_cancer_prs(
    sample_engine: sa.Engine,
) -> None:
    """Remove unsafe output while preserving identifiable active-model rows."""
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                _finding(
                    module="cancer",
                    category="prs",
                    trait="breast_cancer",
                    text="Breast cancer (BCAC): stale percentile",
                ),
                _finding(
                    module="cancer",
                    category="prs",
                    trait="colorectal_cancer",
                    text="Pre-fingerprint colorectal cancer PRS",
                ),
                _finding(
                    module="traits",
                    category="prs",
                    trait="breast_cancer",
                    text="Unrelated traits-module row",
                ),
                _finding(
                    module="cancer",
                    category="monogenic_variant",
                    trait="breast_cancer",
                    text="BRCA1 pathogenic variant",
                ),
                {
                    "module": "cancer",
                    "category": "prs",
                    "evidence_level": 1,
                    "finding_text": "Unidentified legacy PRS with no metadata",
                    "detail_json": None,
                },
                {
                    "module": "cancer",
                    "category": "prs",
                    "evidence_level": 1,
                    "finding_text": "Unidentified legacy PRS with malformed metadata",
                    "detail_json": "{not-json",
                },
            ],
        )
        conn.execute(
            sa.insert(annotation_state),
            {
                "key": "last_finding_diff_json",
                "value": json.dumps(
                    {
                        "changed": [
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": "breast_cancer",
                                "finding_text": "Stale breast PRS changed",
                            },
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": "colorectal_cancer",
                                "finding_text": "Current colorectal PRS changed",
                            },
                            {
                                "module": "cancer",
                                "category": "prs",
                                "finding_text": "Unidentified cancer PRS changed",
                            },
                        ],
                        "added": [
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": "breast_cancer",
                                "finding_text": "Stale breast PRS added",
                            },
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": "colorectal_cancer",
                                "finding_text": "Current colorectal PRS added",
                            },
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": None,
                                "finding_text": "Unidentified cancer PRS added",
                            },
                        ],
                        "removed": [
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": "breast_cancer",
                                "finding_text": "Stale breast PRS removed",
                            },
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": "colorectal_cancer",
                                "finding_text": "Current colorectal PRS removed",
                            },
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": " ",
                                "finding_text": "Unidentified cancer PRS removed",
                            },
                        ],
                        "counts": {"changed": 3, "added": 3, "removed": 3},
                        "dismissed": False,
                    }
                ),
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 19"))

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings.c.module, findings.c.category, findings.c.finding_text).order_by(
                findings.c.id
            )
        ).fetchall()
        diff_json = conn.execute(
            sa.select(annotation_state.c.value).where(
                annotation_state.c.key == "last_finding_diff_json"
            )
        ).scalar_one()
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert version == SAMPLE_SCHEMA_VERSION == 23
    assert [(row.module, row.category, row.finding_text) for row in rows] == [
        # Older valid active-model rows have a trait but no model fingerprint;
        # they remain surfaceable. Only breast or unidentified PRS rows are
        # quarantined, because opaque scores can be regenerated safely.
        ("cancer", "prs", "Pre-fingerprint colorectal cancer PRS"),
        ("traits", "prs", "Unrelated traits-module row"),
        ("cancer", "monogenic_variant", "BRCA1 pathogenic variant"),
    ]
    assert json.loads(diff_json) == {
        "changed": [
            {
                "module": "cancer",
                "category": "prs",
                "trait": "colorectal_cancer",
                "finding_text": "Current colorectal PRS changed",
            }
        ],
        "added": [
            {
                "module": "cancer",
                "category": "prs",
                "trait": "colorectal_cancer",
                "finding_text": "Current colorectal PRS added",
            }
        ],
        "removed": [
            {
                "module": "cancer",
                "category": "prs",
                "trait": "colorectal_cancer",
                "finding_text": "Current colorectal PRS removed",
            }
        ],
        "counts": {"changed": 1, "added": 1, "removed": 1},
        "dismissed": False,
    }

    # The version gate makes the content migration idempotent.
    assert ensure_sample_schema_current(sample_engine) is False


def test_v20_migration_normalizes_malformed_finding_diff_buckets(
    sample_engine: sa.Engine,
) -> None:
    """Malformed buckets become empty lists and contribute zero to counts."""
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(annotation_state),
            {
                "key": "last_finding_diff_json",
                "value": json.dumps(
                    {
                        "changed": {"unexpected": "mapping"},
                        "added": [
                            {
                                "module": "cancer",
                                "category": "prs",
                                "trait": "breast_cancer",
                            }
                        ],
                        "removed": 7,
                        "counts": {"changed": 99, "added": 1, "removed": 99},
                    }
                ),
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 19"))

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        diff_json = conn.execute(
            sa.select(annotation_state.c.value).where(
                annotation_state.c.key == "last_finding_diff_json"
            )
        ).scalar_one()

    assert json.loads(diff_json) == {
        "changed": [],
        "added": [],
        "removed": [],
        "counts": {"changed": 0, "added": 0, "removed": 0},
    }


def test_v20_migration_tolerates_partial_legacy_findings_table() -> None:
    """A malformed/partial old table is skipped instead of crashing the DB open."""
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE findings (id INTEGER PRIMARY KEY, module TEXT, category TEXT)")
        )
        conn.execute(
            sa.text(
                "INSERT INTO findings (id, module, category) VALUES "
                "(1, 'cancer', 'prs'), "
                "(2, 'cancer', 'monogenic_variant'), "
                "(3, 'traits', 'prs')"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 19"))

    assert ensure_sample_schema_current(engine) is True

    with engine.connect() as conn:
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == 23
        remaining = conn.execute(
            sa.text("SELECT id, module, category FROM findings ORDER BY id")
        ).fetchall()

    assert [tuple(row) for row in remaining] == [
        (2, "cancer", "monogenic_variant"),
        (3, "traits", "prs"),
    ]
