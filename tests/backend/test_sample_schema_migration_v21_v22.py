"""Tests for the v21 → v22 TPMT Poor Metabolizer guidance repair."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings

LEGACY_RECOMMENDATIONS = {
    "azathioprine": ("Reduce dose to 10% of standard or use alternative agent.",),
    "mercaptopurine": ("Reduce dose to 10% of standard. Consider alternative agent.",),
    "thioguanine": (
        "Start with drastically reduced doses (reduce by 50-75%) and titrate based on "
        "myelosuppression; for nonmalignant conditions consider an alternative agent.",
        "Start with drastically reduced doses (reduce daily dose by 10-fold and dose thrice "
        "weekly instead of daily) and titrate based on myelosuppression; for nonmalignant "
        "conditions consider an alternative agent.",
    ),
}

CURRENT_RECOMMENDATIONS = {
    "azathioprine": "Consider alternative nonthiopurine immunosuppressant therapy.",
    "mercaptopurine": (
        "For malignancy: initiate therapy with drastically reduced starting doses. "
        "Reduce starting dose by 10-fold and reduce frequency to thrice weekly instead of "
        "daily (e.g. 10 mg/m2/day given 3 days/week). During therapy, adjust mercaptopurine "
        "doses based on the degree of myelosuppression and disease-specific guidelines. It "
        "usually takes at least 4-6 weeks of stable dosing to reach steady state after each "
        "dose adjustment. If myelosuppression occurs, emphasis should be on reducing "
        "mercaptopurine over other agents. For nonmalignancy: consider alternative "
        "nonthiopurine immunosuppressant therapy."
    ),
    "thioguanine": (
        "Initiate therapy with drastically reduced starting doses. Reduce starting dose by "
        "10-fold and reduce frequency to thrice weekly instead of daily. During therapy, "
        "adjust thioguanine doses based on degree of myelosuppression and disease-specific "
        "guidelines. It usually takes at least 4-6 weeks of stable dosing to reach steady "
        "state after each dose adjustment. If myelosuppression occurs, emphasis should be "
        "on reducing thioguanine over other agents."
    ),
}


def _finding_text(drug: str, recommendation: str, suffix: str = "") -> str:
    return f"TPMT *3A/*3A: Poor Metabolizer -- {drug}: {recommendation}{suffix}"


def _alert(
    drug: str,
    recommendation: str,
    *,
    suffix: str = "",
    row_id: int | None = None,
    **overrides: object,
) -> dict[str, object]:
    detail = {
        "recommendation": recommendation,
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/",
        "future_metadata": {"preserve": [drug]},
    }
    row: dict[str, object] = {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "TPMT",
        "diplotype": "*3A/*3A",
        "metabolizer_status": "Poor Metabolizer",
        "drug": drug,
        "finding_text": _finding_text(drug, recommendation, suffix),
        "detail_json": json.dumps(detail),
        "provenance": json.dumps({"sources": {"cpic": {"version": "legacy"}}}),
    }
    if row_id is not None:
        row["id"] = row_id
    row.update(overrides)
    return row


def _diff_entry(drug: str, recommendation: str, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "gene_symbol": "TPMT",
        "drug": drug,
        "diplotype": "*3A/*3A",
        "metabolizer_status": "Poor Metabolizer",
        "finding_text": _finding_text(drug, recommendation),
    }
    entry.update(overrides)
    return entry


def test_v22_repairs_all_exact_legacy_alerts_in_place(sample_engine: sa.Engine) -> None:
    created_at = datetime(2026, 7, 17, 12, 30)
    suffixes = {
        "azathioprine": "",
        "mercaptopurine": " (provisional -- see call confidence note)",
        "thioguanine": "",
    }
    legacy_cases = [
        (drug, recommendation)
        for drug, recommendations in LEGACY_RECOMMENDATIONS.items()
        for recommendation in recommendations
    ]
    alert_rows = []
    expected_texts: dict[int, str] = {}
    for index, (drug, legacy) in enumerate(legacy_cases):
        row_id = 101 + index
        suffix = suffixes[drug]
        row = _alert(
            drug,
            legacy,
            suffix=suffix,
            row_id=row_id,
            created_at=created_at,
        )
        if legacy == LEGACY_RECOMMENDATIONS["thioguanine"][0]:
            suffix = " (conservative partial call -- see call confidence note)"
            row["diplotype"] = "*1/*3A"
            row["finding_text"] = (
                f"TPMT *1/*3A (possible *3B/*3C): Poor Metabolizer -- {drug}: {legacy}{suffix}"
            )
            expected_texts[row_id] = (
                "TPMT *1/*3A (possible *3B/*3C): Poor Metabolizer -- "
                f"{drug}: {CURRENT_RECOMMENDATIONS[drug]}{suffix}"
            )
        else:
            expected_texts[row_id] = _finding_text(drug, CURRENT_RECOMMENDATIONS[drug], suffix)
        alert_rows.append(row)

    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), alert_rows)
        conn.execute(sa.text("PRAGMA user_version = 21"))

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                findings.c.id,
                findings.c.drug,
                findings.c.finding_text,
                findings.c.detail_json,
                findings.c.provenance,
                findings.c.created_at,
            ).order_by(findings.c.id)
        ).mappings()
        migrated = list(rows)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert version == SAMPLE_SCHEMA_VERSION == 26
    assert [row["id"] for row in migrated] == [101, 102, 103, 104]
    for row in migrated:
        drug = row["drug"]
        recommendation = CURRENT_RECOMMENDATIONS[drug]
        assert row["finding_text"] == expected_texts[row["id"]]
        detail = json.loads(row["detail_json"])
        assert detail["recommendation"] == recommendation
        assert detail["classification"] == "A"
        assert detail["future_metadata"] == {"preserve": [drug]}
        assert row["provenance"] is None
        assert row["created_at"] == created_at

    csv_path = Path(__file__).parents[2] / "backend/data/cpic/cpic_guidelines.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        bundled = {
            row["drug"]: row["recommendation"]
            for row in csv.DictReader(handle)
            if row["gene"] == "TPMT"
            and row["phenotype"] == "Poor Metabolizer"
            and row["drug"] in CURRENT_RECOMMENDATIONS
        }
    assert bundled == CURRENT_RECOMMENDATIONS

    snapshot = migrated
    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        rerun = list(
            conn.execute(
                sa.select(
                    findings.c.id,
                    findings.c.drug,
                    findings.c.finding_text,
                    findings.c.detail_json,
                    findings.c.provenance,
                    findings.c.created_at,
                ).order_by(findings.c.id)
            ).mappings()
        )
    assert rerun == snapshot


def test_v22_leaves_malformed_current_custom_and_near_miss_alerts_untouched(
    sample_engine: sa.Engine,
) -> None:
    legacy = LEGACY_RECOMMENDATIONS["azathioprine"][0]
    current = CURRENT_RECOMMENDATIONS["azathioprine"]
    rows = [
        _alert("azathioprine", legacy, row_id=1, detail_json="{not-json"),
        _alert("azathioprine", legacy, row_id=2, detail_json=json.dumps([legacy])),
        _alert(
            "azathioprine",
            legacy,
            row_id=10,
            detail_json=json.dumps({"recommendation": [legacy]}),
        ),
        _alert(
            "azathioprine",
            legacy,
            row_id=3,
            detail_json=json.dumps({"recommendation": f"{legacy} "}),
        ),
        _alert(
            "azathioprine",
            legacy,
            row_id=11,
            detail_json=json.dumps(
                {
                    "recommendation": legacy,
                    "classification": "B",
                    "guideline_url": (
                        "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/"
                    ),
                }
            ),
        ),
        _alert(
            "azathioprine",
            legacy,
            row_id=12,
            detail_json=json.dumps(
                {
                    "recommendation": legacy,
                    "classification": "A",
                    "guideline_url": "https://example.org/local-guidance",
                }
            ),
        ),
        _alert(
            "azathioprine",
            legacy,
            row_id=4,
            finding_text=_finding_text("azathioprine", f"{legacy} "),
        ),
        _alert("azathioprine", legacy, row_id=5, metabolizer_status="Intermediate Metabolizer"),
        _alert("AZATHIOPRINE", legacy, row_id=6),
        _alert("azathioprine", current, row_id=7),
        _alert("azathioprine", "Locally curated recommendation.", row_id=8),
        _alert("azathioprine", legacy, suffix=" (custom suffix)", row_id=9),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        conn.execute(sa.text("PRAGMA user_version = 21"))

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        after = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert len(after) == len(rows)
    assert version == SAMPLE_SCHEMA_VERSION


def test_v22_removes_only_exact_legacy_diff_entries_and_recomputes_counts(
    sample_engine: sa.Engine,
) -> None:
    preserved_changed = _diff_entry(
        "azathioprine",
        LEGACY_RECOMMENDATIONS["azathioprine"][0],
        metabolizer_status="Intermediate Metabolizer",
    )
    preserved_added = _diff_entry("mercaptopurine", CURRENT_RECOMMENDATIONS["mercaptopurine"])
    preserved_removed = _diff_entry(
        "thioguanine",
        "Locally curated recommendation.",
    )
    diff = {
        "schema_version": 1,
        "before_releases": {"cpic": "legacy"},
        "after_releases": {"cpic": "current"},
        "release_deltas": [{"db_name": "cpic", "before": "legacy", "after": "current"}],
        "generated_at": "2026-07-17T00:00:00Z",
        "dismissed": False,
        "future_metadata": {"preserve": True},
        "changed": [
            _diff_entry("azathioprine", LEGACY_RECOMMENDATIONS["azathioprine"][0]),
            preserved_changed,
            "malformed entry",
        ],
        "added": [
            _diff_entry("mercaptopurine", LEGACY_RECOMMENDATIONS["mercaptopurine"][0]),
            preserved_added,
        ],
        "removed": [
            _diff_entry("thioguanine", LEGACY_RECOMMENDATIONS["thioguanine"][1]),
            preserved_removed,
        ],
        "counts": {"changed": 99, "added": 99, "removed": 99},
    }
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 21"))

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

    assert finding_count == 0  # A diff entry must never synthesize a finding.
    assert stored == {
        **{
            key: value
            for key, value in diff.items()
            if key not in {"changed", "added", "removed", "counts"}
        },
        "changed": [preserved_changed, "malformed entry"],
        "added": [preserved_added],
        "removed": [preserved_removed],
        "counts": {"changed": 2, "added": 1, "removed": 1},
    }


def test_v22_leaves_malformed_diff_json_untouched(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": "{not-json"},
        )
        conn.execute(sa.text("PRAGMA user_version = 21"))

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


def test_v22_repairs_legacy_alert_when_provenance_column_is_absent() -> None:
    engine = sa.create_engine("sqlite://")
    legacy = LEGACY_RECOMMENDATIONS["azathioprine"][0]
    detail = json.dumps(
        {
            "recommendation": legacy,
            "classification": "A",
            "guideline_url": (
                "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/"
            ),
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
                "(1, 'pharmacogenomics', 'prescribing_alert', 'TPMT', "
                "'Poor Metabolizer', 'azathioprine', :finding_text, :detail_json)"
            ),
            {
                "finding_text": _finding_text("azathioprine", legacy),
                "detail_json": detail,
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 21"))

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
        "azathioprine", CURRENT_RECOMMENDATIONS["azathioprine"]
    )
    assert json.loads(row.detail_json)["recommendation"] == CURRENT_RECOMMENDATIONS["azathioprine"]
    assert version == SAMPLE_SCHEMA_VERSION


def test_v22_locks_before_reading_or_writing_findings_and_diff(
    sample_engine: sa.Engine,
) -> None:
    legacy = LEGACY_RECOMMENDATIONS["azathioprine"][0]
    diff = {
        "changed": [_diff_entry("azathioprine", legacy)],
        "added": [],
        "removed": [],
        "counts": {"changed": 1, "added": 0, "removed": 0},
    }
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), _alert("azathioprine", legacy))
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 21"))

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
            "SELECT FINDINGS.ID, FINDINGS.DRUG, FINDINGS.FINDING_TEXT, FINDINGS.DETAIL_JSON"
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


def test_v22_rolls_back_all_repairs_when_one_update_fails(sample_engine: sa.Engine) -> None:
    rows = [
        _alert("azathioprine", LEGACY_RECOMMENDATIONS["azathioprine"][0], row_id=1),
        _alert("mercaptopurine", LEGACY_RECOMMENDATIONS["mercaptopurine"][0], row_id=2),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_mercaptopurine_repair "
                "BEFORE UPDATE OF finding_text ON findings "
                "WHEN OLD.drug = 'mercaptopurine' "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 21"))

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)

    with sample_engine.connect() as conn:
        after = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == 21
