"""Tests for the v24 -> v25 persisted CYP2D6/tamoxifen guidance repair."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings

GUIDELINE_URL = "https://cpicpgx.org/guidelines/cpic-guideline-for-tamoxifen-based-on-cyp2d6/"
LEGACY_RECOMMENDATIONS = {
    "Normal Metabolizer": "Use label-recommended dosing.",
    "Intermediate Metabolizer": "Consider higher dose or alternative therapy.",
    "Poor Metabolizer": (
        "Avoid tamoxifen. Use alternative hormonal therapy such as aromatase inhibitor."
    ),
}
CURRENT_RECOMMENDATIONS = {
    "Normal Metabolizer": (
        "Avoid moderate and strong CYP2D6 inhibitors. Initiate therapy with "
        "recommended standard of care dosing (tamoxifen 20 mg/day)."
    ),
    "Intermediate Metabolizer": (
        "Consider hormonal therapy such as an aromatase inhibitor for postmenopausal women "
        "or aromatase inhibitor along with ovarian function suppression in premenopausal "
        "women, given that these approaches are superior to tamoxifen regardless of CYP2D6 "
        "genotype (PMID 26211827). If aromatase inhibitor use is contraindicated, "
        "consideration should be given to use a higher but FDA approved tamoxifen dose "
        "(40 mg/day)(PMID 27226358). Avoid CYP2D6 strong to weak inhibitors."
    ),
    "Poor Metabolizer": (
        "Recommend alternative hormonal therapy such as an aromatase inhibitor for "
        "postmenopausal women or aromatase inhibitor along with ovarian function suppression "
        "in premenopausal women given that these approaches are superior to tamoxifen "
        "regardless of CYP2D6 genotype (PMID 26211827) and based on knowledge that CYP2D6 "
        "poor metabolizers switched from tamoxifen to anastrozole do not have an increased "
        "risk of recurrence (PMID 23213055). Note, higher dose tamoxifen (40 mg/day) "
        "increases but does not normalize endoxifen concentrations and can be considered if "
        "there are contraindications to aromatase inhibitor therapy (PMID 27226358, "
        "21768473)."
    ),
}


def _finding_text(
    phenotype: str,
    recommendation: str,
    *,
    diplotype: str,
    suffix: str = "",
) -> str:
    return f"CYP2D6 {diplotype}: {phenotype} -- tamoxifen: {recommendation}{suffix}"


def _alert(
    phenotype: str,
    recommendation: str,
    *,
    diplotype: str = "*1/*4",
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
        "gene_symbol": "CYP2D6",
        "diplotype": diplotype,
        "metabolizer_status": phenotype,
        "drug": "tamoxifen",
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
    *,
    diplotype: str = "*1/*4",
    **overrides: object,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "gene_symbol": "CYP2D6",
        "drug": "tamoxifen",
        "diplotype": diplotype,
        "metabolizer_status": phenotype,
        "finding_text": _finding_text(phenotype, recommendation, diplotype=diplotype),
    }
    entry.update(overrides)
    return entry


def test_v25_repairs_exact_legacy_tamoxifen_alerts_in_place(
    sample_engine: sa.Engine,
) -> None:
    created_at = datetime(2026, 8, 1, 10, 0)
    cases = [
        (101, "Normal Metabolizer", "*1/*1", ""),
        (
            102,
            "Intermediate Metabolizer",
            "*1/*4",
            " (provisional -- see call confidence note)",
        ),
        (103, "Poor Metabolizer", "*4/*4", ""),
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
        conn.execute(sa.text("PRAGMA user_version = 24"))

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
    assert [row["id"] for row in migrated] == [101, 102, 103]
    for row, (_, phenotype, diplotype, suffix) in zip(migrated, cases, strict=True):
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
        assert detail["guideline_url"] == GUIDELINE_URL
        assert detail["future_metadata"] == {"preserve": [phenotype]}
        assert row["provenance"] is None
        assert row["created_at"] == created_at

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


def test_v25_leaves_malformed_current_custom_and_near_miss_alerts_untouched(
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
                {"recommendation": legacy, "classification": "B", "guideline_url": GUIDELINE_URL}
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
            finding_text=_finding_text(phenotype, f"{legacy} ", diplotype="*1/*4"),
        ),
        _alert(phenotype, legacy, row_id=7, metabolizer_status="Normal Metabolizer"),
        _alert(phenotype, legacy, row_id=8, drug="TAMOXIFEN"),
        _alert(phenotype, current, row_id=9),
        _alert(phenotype, "Locally curated recommendation.", row_id=10),
        _alert(phenotype, legacy, row_id=11, suffix=" (custom suffix)"),
        _alert(
            phenotype,
            legacy,
            row_id=12,
            diplotype="*4/*4",
            finding_text=_finding_text(phenotype, legacy, diplotype="*1/*4"),
        ),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        conn.execute(sa.text("PRAGMA user_version = 24"))

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        after = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == 25


def test_v25_removes_only_exact_legacy_diff_entries_and_recomputes_counts(
    sample_engine: sa.Engine,
) -> None:
    preserved_changed = _diff_entry(
        "Normal Metabolizer",
        LEGACY_RECOMMENDATIONS["Intermediate Metabolizer"],
        diplotype="*1/*1",
    )
    preserved_added = _diff_entry(
        "Intermediate Metabolizer",
        CURRENT_RECOMMENDATIONS["Intermediate Metabolizer"],
    )
    preserved_removed = _diff_entry("Poor Metabolizer", "Locally curated recommendation.")
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
            _diff_entry("Poor Metabolizer", LEGACY_RECOMMENDATIONS["Poor Metabolizer"]),
            preserved_added,
        ],
        "removed": [
            _diff_entry(
                "Poor Metabolizer",
                LEGACY_RECOMMENDATIONS["Poor Metabolizer"],
                drug="TAMOXIFEN",
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
        conn.execute(sa.text("PRAGMA user_version = 24"))

    assert ensure_sample_schema_current(sample_engine) is True
    with sample_engine.connect() as conn:
        stored = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json"
                )
            ).scalar_one()
        )

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
                drug="TAMOXIFEN",
            ),
            preserved_removed,
        ],
        "counts": {"changed": 2, "added": 1, "removed": 2},
    }


def test_v25_leaves_malformed_diff_json_untouched(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": "{not-json"},
        )
        conn.execute(sa.text("PRAGMA user_version = 24"))

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
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == 25


def test_v25_repairs_legacy_alert_when_provenance_column_is_absent() -> None:
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
                "diplotype TEXT, metabolizer_status TEXT, drug TEXT, finding_text TEXT, "
                "detail_json TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO findings "
                "(id, module, category, gene_symbol, diplotype, metabolizer_status, drug, "
                "finding_text, detail_json) VALUES "
                "(1, 'pharmacogenomics', 'prescribing_alert', 'CYP2D6', '*1/*4', "
                "'Intermediate Metabolizer', 'tamoxifen', :finding_text, :detail_json)"
            ),
            {
                "finding_text": _finding_text(phenotype, legacy, diplotype="*1/*4"),
                "detail_json": detail,
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 24"))

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
        diplotype="*1/*4",
    )
    assert json.loads(row.detail_json)["recommendation"] == CURRENT_RECOMMENDATIONS[phenotype]
    assert version == 25


def test_v25_locks_before_reading_or_writing_findings_and_diff(
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
        conn.execute(sa.text("PRAGMA user_version = 24"))

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
            "SELECT FINDINGS.ID, FINDINGS.DIPLOTYPE, FINDINGS.METABOLIZER_STATUS, "
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


def test_v25_rolls_back_all_repairs_when_one_update_fails(
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
            diplotype="*4/*4",
        ),
    ]
    diff = {
        "changed": [
            _diff_entry(
                "Intermediate Metabolizer",
                LEGACY_RECOMMENDATIONS["Intermediate Metabolizer"],
            )
        ],
        "added": [],
        "removed": [],
        "counts": {"changed": 1, "added": 0, "removed": 0},
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
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_poor_tamoxifen_repair "
                "BEFORE UPDATE OF finding_text ON findings "
                "WHEN OLD.metabolizer_status = 'Poor Metabolizer' "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 24"))

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)

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
    assert version == 24
