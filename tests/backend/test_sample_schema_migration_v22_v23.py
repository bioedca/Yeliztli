"""Tests for the v22 -> v23 persisted CYP2B6 efavirenz guidance repair."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings

GUIDELINE_URL = (
    "https://cpicpgx.org/guidelines/cpic-guideline-for-efavirenz-based-on-cyp2b6-genotype/"
)
LEGACY_RECOMMENDATIONS = {
    "Intermediate Metabolizer": (
        "Use label-recommended dosing; consider a reduced dose if CNS side effects occur."
    ),
    "Poor Metabolizer": (
        "Consider initiating at a decreased dose (e.g., 400 mg/day); higher plasma "
        "exposure raises CNS-toxicity risk."
    ),
}
CURRENT_RECOMMENDATIONS = {
    "Intermediate Metabolizer": (
        "Consider initiating efavirenz with decreased dose of 400 mg/day."
    ),
    "Poor Metabolizer": (
        "Consider initiating efavirenz with decreased dose of 400 or 200 mg/day."
    ),
}


def _finding_text(
    phenotype: str,
    recommendation: str,
    *,
    diplotype: str = "*1/*6",
    suffix: str = "",
) -> str:
    return f"CYP2B6 {diplotype}: {phenotype} -- efavirenz: {recommendation}{suffix}"


def _alert(
    phenotype: str,
    recommendation: str,
    *,
    diplotype: str = "*1/*6",
    suffix: str = "",
    row_id: int | None = None,
    **overrides: object,
) -> dict[str, object]:
    detail = {
        "recommendation": recommendation,
        "classification": "A",
        "guideline_url": GUIDELINE_URL,
        "call_confidence": "Partial",
        "future_metadata": {"preserve": [phenotype]},
    }
    row: dict[str, object] = {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2B6",
        "diplotype": diplotype,
        "metabolizer_status": phenotype,
        "drug": "efavirenz",
        "finding_text": _finding_text(
            phenotype,
            recommendation,
            diplotype=diplotype,
            suffix=suffix,
        ),
        "detail_json": json.dumps(detail),
        "provenance": json.dumps({"sources": {"cpic": {"version": "legacy"}}}),
    }
    if row_id is not None:
        row["id"] = row_id
    row.update(overrides)
    return row


def _diff_entry(
    phenotype: str,
    recommendation: str,
    **overrides: object,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "gene_symbol": "CYP2B6",
        "drug": "efavirenz",
        "diplotype": "*1/*6",
        "metabolizer_status": phenotype,
        "finding_text": _finding_text(phenotype, recommendation),
    }
    entry.update(overrides)
    return entry


def test_v23_repairs_exact_legacy_efavirenz_alerts_in_place(
    sample_engine: sa.Engine,
) -> None:
    created_at = datetime(2026, 7, 31, 4, 30)
    cases = [
        (
            101,
            "Intermediate Metabolizer",
            "*1/*1 (possible *1/*9)",
            " (conservative partial call -- see call confidence note)",
        ),
        (
            102,
            "Poor Metabolizer",
            "*6/*6",
            " (provisional -- see call confidence note)",
        ),
    ]
    rows = [
        _alert(
            phenotype,
            LEGACY_RECOMMENDATIONS[phenotype],
            row_id=row_id,
            diplotype=diplotype,
            suffix=suffix,
            created_at=created_at,
        )
        for row_id, phenotype, diplotype, suffix in cases
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        conn.execute(sa.text("PRAGMA user_version = 22"))

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        migrated = list(
            conn.execute(
                sa.select(
                    findings.c.id,
                    findings.c.metabolizer_status,
                    findings.c.diplotype,
                    findings.c.finding_text,
                    findings.c.detail_json,
                    findings.c.provenance,
                    findings.c.created_at,
                ).order_by(findings.c.id)
            ).mappings()
        )
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert version == SAMPLE_SCHEMA_VERSION == 25
    assert [row["id"] for row in migrated] == [101, 102]
    for row, (_, phenotype, diplotype, suffix) in zip(
        migrated,
        cases,
        strict=True,
    ):
        recommendation = CURRENT_RECOMMENDATIONS[phenotype]
        assert row["metabolizer_status"] == phenotype
        assert row["diplotype"] == diplotype
        assert row["finding_text"] == _finding_text(
            phenotype,
            recommendation,
            diplotype=diplotype,
            suffix=suffix,
        )
        detail = json.loads(row["detail_json"])
        assert detail["recommendation"] == recommendation
        assert detail["classification"] == "A"
        assert detail["future_metadata"] == {"preserve": [phenotype]}
        assert row["provenance"] is None
        assert row["created_at"] == created_at

    csv_path = Path(__file__).parents[2] / "backend/data/cpic/cpic_guidelines.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        bundled = {
            row["phenotype"]: row["recommendation"]
            for row in csv.DictReader(handle)
            if row["gene"] == "CYP2B6"
            and row["drug"] == "efavirenz"
            and row["phenotype"] in CURRENT_RECOMMENDATIONS
        }
    assert bundled == CURRENT_RECOMMENDATIONS

    snapshot = migrated
    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        rerun = list(
            conn.execute(
                sa.select(
                    findings.c.id,
                    findings.c.metabolizer_status,
                    findings.c.diplotype,
                    findings.c.finding_text,
                    findings.c.detail_json,
                    findings.c.provenance,
                    findings.c.created_at,
                ).order_by(findings.c.id)
            ).mappings()
        )
    assert rerun == snapshot


def test_v23_leaves_malformed_current_custom_and_near_miss_alerts_untouched(
    sample_engine: sa.Engine,
) -> None:
    phenotype = "Intermediate Metabolizer"
    legacy = LEGACY_RECOMMENDATIONS[phenotype]
    current = CURRENT_RECOMMENDATIONS[phenotype]
    rows = [
        _alert(phenotype, legacy, row_id=1, detail_json="{not-json"),
        _alert(phenotype, legacy, row_id=2, detail_json=json.dumps([legacy])),
        _alert(
            phenotype,
            legacy,
            row_id=3,
            detail_json=json.dumps({"recommendation": f"{legacy} "}),
        ),
        _alert(
            phenotype,
            legacy,
            row_id=4,
            detail_json=json.dumps(
                {
                    "recommendation": legacy,
                    "classification": "B",
                    "guideline_url": GUIDELINE_URL,
                }
            ),
        ),
        _alert(
            phenotype,
            legacy,
            row_id=5,
            detail_json=json.dumps(
                {
                    "recommendation": legacy,
                    "classification": "A",
                    "guideline_url": "https://example.test/local-guidance",
                }
            ),
        ),
        _alert(
            phenotype,
            legacy,
            row_id=6,
            finding_text=_finding_text(phenotype, f"{legacy} "),
        ),
        _alert(
            phenotype,
            legacy,
            row_id=7,
            metabolizer_status="Normal Metabolizer",
        ),
        _alert(phenotype, legacy, row_id=8, drug="EFAVIRENZ"),
        _alert(phenotype, current, row_id=9),
        _alert(phenotype, "Locally curated recommendation.", row_id=10),
        _alert(phenotype, legacy, row_id=11, suffix=" (custom suffix)"),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        conn.execute(sa.text("PRAGMA user_version = 22"))

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        after = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == SAMPLE_SCHEMA_VERSION


def test_v23_removes_only_exact_legacy_diff_entries_and_recomputes_counts(
    sample_engine: sa.Engine,
) -> None:
    preserved_changed = _diff_entry(
        "Normal Metabolizer",
        LEGACY_RECOMMENDATIONS["Intermediate Metabolizer"],
    )
    preserved_added = _diff_entry(
        "Intermediate Metabolizer",
        CURRENT_RECOMMENDATIONS["Intermediate Metabolizer"],
    )
    preserved_removed = _diff_entry(
        "Poor Metabolizer",
        "Locally curated recommendation.",
    )
    diff = {
        "schema_version": 1,
        "before_releases": {"cpic": "legacy"},
        "after_releases": {"cpic": "current"},
        "future_metadata": {"preserve": True},
        "changed": [
            _diff_entry(
                "Intermediate Metabolizer",
                LEGACY_RECOMMENDATIONS["Intermediate Metabolizer"],
            ),
            preserved_changed,
            "malformed entry",
        ],
        "added": [
            _diff_entry(
                "Poor Metabolizer",
                LEGACY_RECOMMENDATIONS["Poor Metabolizer"],
            ),
            preserved_added,
        ],
        "removed": [
            _diff_entry(
                "Poor Metabolizer",
                LEGACY_RECOMMENDATIONS["Poor Metabolizer"],
                drug="EFAVIRENZ",
            ),
            preserved_removed,
        ],
        "counts": {"changed": 99, "added": 99, "removed": 99},
    }
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 22"))

    assert ensure_sample_schema_current(sample_engine) is True
    with sample_engine.connect() as conn:
        stored = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json"
                )
            ).scalar_one()
        )
        finding_count = conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one()

    assert finding_count == 0
    assert stored == {
        **{
            key: value
            for key, value in diff.items()
            if key not in {"changed", "added", "removed", "counts"}
        },
        "changed": [preserved_changed, "malformed entry"],
        "added": [preserved_added],
        "removed": [
            _diff_entry(
                "Poor Metabolizer",
                LEGACY_RECOMMENDATIONS["Poor Metabolizer"],
                drug="EFAVIRENZ",
            ),
            preserved_removed,
        ],
        "counts": {"changed": 2, "added": 1, "removed": 2},
    }


def test_v23_leaves_malformed_diff_json_untouched(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": "{not-json"},
        )
        conn.execute(sa.text("PRAGMA user_version = 22"))

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        assert (
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json"
                )
            ).scalar_one()
            == "{not-json"
        )
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == SAMPLE_SCHEMA_VERSION


def test_v23_repairs_legacy_alert_when_provenance_column_is_absent() -> None:
    engine = sa.create_engine("sqlite://")
    phenotype = "Intermediate Metabolizer"
    legacy = LEGACY_RECOMMENDATIONS[phenotype]
    detail = json.dumps(
        {
            "recommendation": legacy,
            "classification": "A",
            "guideline_url": GUIDELINE_URL,
        }
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE findings ("
                "id INTEGER PRIMARY KEY, module TEXT, category TEXT, gene_symbol TEXT, "
                "metabolizer_status TEXT, drug TEXT, finding_text TEXT, detail_json TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO findings "
                "(id, module, category, gene_symbol, metabolizer_status, drug, "
                "finding_text, detail_json) VALUES "
                "(1, 'pharmacogenomics', 'prescribing_alert', 'CYP2B6', "
                "'Intermediate Metabolizer', 'efavirenz', :finding_text, :detail_json)"
            ),
            {
                "finding_text": _finding_text(phenotype, legacy),
                "detail_json": detail,
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 22"))

    assert ensure_sample_schema_current(engine) is True

    assert "provenance" not in {
        column["name"] for column in sa.inspect(engine).get_columns("findings")
    }
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT finding_text, detail_json FROM findings WHERE id = 1")
        ).one()
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert row.finding_text == _finding_text(
        phenotype,
        CURRENT_RECOMMENDATIONS[phenotype],
    )
    assert json.loads(row.detail_json)["recommendation"] == CURRENT_RECOMMENDATIONS[phenotype]
    assert version == SAMPLE_SCHEMA_VERSION


def test_v23_locks_before_reading_or_writing_findings_and_diff(
    sample_engine: sa.Engine,
) -> None:
    phenotype = "Intermediate Metabolizer"
    legacy = LEGACY_RECOMMENDATIONS[phenotype]
    diff = {
        "changed": [_diff_entry(phenotype, legacy)],
        "added": [],
        "removed": [],
        "counts": {"changed": 1, "added": 0, "removed": 0},
    }
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), _alert(phenotype, legacy))
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 22"))

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.split()).upper())

    sa.event.listen(sample_engine, "before_cursor_execute", record_statement)
    try:
        assert ensure_sample_schema_current(sample_engine) is True
    finally:
        sa.event.remove(sample_engine, "before_cursor_execute", record_statement)

    begin_index = statements.index("BEGIN IMMEDIATE")
    candidate_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith(
            "SELECT FINDINGS.ID, FINDINGS.METABOLIZER_STATUS, "
            "FINDINGS.FINDING_TEXT, FINDINGS.DETAIL_JSON"
        )
    )
    finding_update_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("UPDATE FINDINGS SET")
    )
    diff_select_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT ANNOTATION_STATE.VALUE")
    )
    diff_update_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("UPDATE ANNOTATION_STATE SET")
    )
    assert (
        begin_index
        < candidate_index
        < finding_update_index
        < diff_select_index
        < diff_update_index
    )


def test_v23_rolls_back_all_repairs_when_one_update_fails(
    sample_engine: sa.Engine,
) -> None:
    rows = [
        _alert(
            "Intermediate Metabolizer",
            LEGACY_RECOMMENDATIONS["Intermediate Metabolizer"],
            row_id=1,
        ),
        _alert(
            "Poor Metabolizer",
            LEGACY_RECOMMENDATIONS["Poor Metabolizer"],
            row_id=2,
        ),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_poor_efavirenz_repair "
                "BEFORE UPDATE OF finding_text ON findings "
                "WHEN OLD.metabolizer_status = 'Poor Metabolizer' "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 22"))

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)

    with sample_engine.connect() as conn:
        after = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == 22
