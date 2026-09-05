"""Tests for the v23 -> v24 persisted LRRK2 wording repair."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings

RISK_CLASSIFICATION = "LRRK2 G2019S — Parkinson's disease risk factor (reduced penetrance)"
LEGACY_TEMPLATES = (
    "LRRK2 G2019S (rs34637584 {genotype}) detected. This is the most common known "
    "genetic risk factor for Parkinson's disease, but its penetrance is reduced and "
    "age-dependent: lifetime risk for carriers is estimated at roughly 25-42.5% by "
    "age 80, so the majority of carriers never develop Parkinson's. Risk also varies "
    "by ancestry (the variant is more common in Ashkenazi Jewish and North African "
    "Berber populations) and is modified by other genetic and environmental factors. "
    "A positive result is not a diagnosis and not a prediction that you will develop "
    "Parkinson's.",
    "LRRK2 G2019S (rs34637584 {genotype}) detected. This is the most common known "
    "genetic risk factor for Parkinson's disease, but its penetrance is reduced and "
    "age-dependent. Published age-80 estimates vary by cohort design, ancestry, and "
    "modifier burden: recent cohorts report roughly 24-49%, with kin-cohort estimates "
    "around 25-42.5%, so many carriers never develop Parkinson's. Risk also varies by "
    "ancestry (the variant is more common in Ashkenazi Jewish and North African Berber "
    "populations) and is modified by other genetic and environmental factors. A "
    "positive result is not a diagnosis and not a prediction that you will develop "
    "Parkinson's.",
)
CURRENT_TEMPLATE = (
    "LRRK2 G2019S (rs34637584 {genotype}) detected. This is the most common known "
    "genetic risk factor for Parkinson's disease, but its penetrance is reduced and "
    "age-dependent. Published age-80 estimates vary by cohort design, ancestry, and "
    "modifier burden: recent cohorts report roughly 24-49%, with kin-cohort estimates "
    "around 25-42.5%. By age 80, most carriers in these cohorts had not developed "
    "Parkinson's disease. Risk also varies by ancestry (the variant is more common in "
    "Ashkenazi Jewish and North African Berber populations) and is modified by other "
    "genetic and environmental factors. A positive result is not a diagnosis and not "
    "a prediction that you will develop Parkinson's."
)
CURRENT_PMIDS = ["26062626", "28639421", "38804604", "40926580"]


def _render(template: str, call: str) -> str:
    """The text v24 wrote: the template's own rsID plus the caller's ``rsid call``."""
    return template.format(genotype=f"rs34637584 {call}")


def _repaired(template: str, call: str) -> str:
    """The same finding after v26 (#2051) drops the rsID the template repeated."""
    return _render(template, call).replace("(rs34637584 rs34637584 ", "(rs34637584 ", 1)


def _detail(call: str, **overrides: object) -> str:
    payload: dict[str, object] = {
        "model_id": "lrrk2_g2019s",
        "classification": RISK_CLASSIFICATION,
        "genotype_calls": {"rs34637584": call},
        "future_metadata": {"preserve": True},
    }
    payload.update(overrides)
    return json.dumps(payload)


def _finding(
    row_id: int,
    template: str,
    call: str,
    **overrides: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": row_id,
        "module": "parkinsons",
        "category": "risk_genotype",
        "evidence_level": 2,
        "gene_symbol": "LRRK2",
        "rsid": "rs34637584",
        "conditions": RISK_CLASSIFICATION,
        "finding_text": _render(template, call),
        "pmid_citations": json.dumps(
            ["31958187"] if template == LEGACY_TEMPLATES[0] else CURRENT_PMIDS
        ),
        "detail_json": _detail(call),
        "provenance": json.dumps({"pipeline_version": "legacy"}),
    }
    row.update(overrides)
    return row


def _diff_entry(template: str, call: str, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "module": "parkinsons",
        "category": "risk_genotype",
        "gene_symbol": "LRRK2",
        "rsid": "rs34637584",
        "drug": None,
        "diplotype": None,
        "pathway": None,
        "trait": None,
        "finding_text": _render(template, call),
        "clinvar_significance": None,
        "evidence_level": 2,
        "metabolizer_status": None,
        "pathway_level": None,
    }
    entry.update(overrides)
    return entry


def test_v24_repairs_both_exact_historical_findings(sample_engine: sa.Engine) -> None:
    created_at = datetime(2026, 7, 31, 8, 30)
    rows = [
        _finding(101, LEGACY_TEMPLATES[0], "GA", created_at=created_at),
        _finding(102, LEGACY_TEMPLATES[1], "AA", created_at=created_at),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        conn.execute(sa.text("PRAGMA user_version = 23"))

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        migrated = list(
            conn.execute(
                sa.select(
                    findings.c.id,
                    findings.c.finding_text,
                    findings.c.pmid_citations,
                    findings.c.detail_json,
                    findings.c.provenance,
                    findings.c.created_at,
                ).order_by(findings.c.id)
            ).mappings()
        )
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert version == SAMPLE_SCHEMA_VERSION == 26
    assert [row["finding_text"] for row in migrated] == [
        _repaired(CURRENT_TEMPLATE, "GA"),
        _repaired(CURRENT_TEMPLATE, "AA"),
    ]
    assert [json.loads(row["pmid_citations"]) for row in migrated] == [
        CURRENT_PMIDS,
        CURRENT_PMIDS,
    ]
    assert [json.loads(row["detail_json"]) for row in migrated] == [
        json.loads(_detail("GA")),
        json.loads(_detail("AA")),
    ]
    assert [row["provenance"] for row in migrated] == [None, None]
    assert [row["created_at"] for row in migrated] == [created_at, created_at]

    snapshot = migrated
    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        rerun = list(
            conn.execute(
                sa.select(
                    findings.c.id,
                    findings.c.finding_text,
                    findings.c.pmid_citations,
                    findings.c.detail_json,
                    findings.c.provenance,
                    findings.c.created_at,
                ).order_by(findings.c.id)
            ).mappings()
        )
    assert rerun == snapshot


def test_v24_leaves_current_custom_malformed_and_near_match_rows_untouched(
    sample_engine: sa.Engine,
) -> None:
    legacy = LEGACY_TEMPLATES[1]
    rows = [
        _finding(1, CURRENT_TEMPLATE, "GA"),
        _finding(2, legacy, "GA", module="custom"),
        _finding(3, legacy, "GA", category="custom"),
        _finding(4, legacy, "GA", gene_symbol="CUSTOM"),
        _finding(5, legacy, "GA", rsid="rs1"),
        _finding(6, legacy, "GA", conditions=f"{RISK_CLASSIFICATION} "),
        _finding(7, legacy, "GA", detail_json="{not-json"),
        _finding(8, legacy, "GA", detail_json=_detail("GA", model_id="custom")),
        _finding(
            9,
            legacy,
            "GA",
            detail_json=_detail("GA", genotype_calls={"rs34637584": "GA", "rs1": "AA"}),
        ),
        _finding(10, legacy, "GA", detail_json=_detail("G/A")),
        _finding(11, legacy, "GA", finding_text=f"{_render(legacy, 'GA')} Custom suffix."),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        conn.execute(sa.text("PRAGMA user_version = 23"))

    # v24 leaves every row's legacy wording alone. v26 (#2051) then removes the
    # duplicated rsID wherever its producer fingerprint is present — module
    # parkinsons, category risk_genotype, stored rsid rs34637584, and one
    # "(rs34637584 rs34637584 GA)" group agreeing with the recorded call —
    # regardless of gene, classification, model id, or extra structured calls.
    dedoubled_ids = {1, 4, 6, 8, 9, 11}
    assert ensure_sample_schema_current(sample_engine) is True
    with sample_engine.connect() as conn:
        after = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    expected = [
        {
            **row,
            "finding_text": row["finding_text"].replace(
                "(rs34637584 rs34637584 ", "(rs34637584 ", 1
            ),
        }
        if row["id"] in dedoubled_ids
        else dict(row)
        for row in before
    ]
    assert [dict(row) for row in after] == expected
    assert all(
        _render(legacy, "GA") not in row["finding_text"] or row["id"] not in dedoubled_ids
        for row in after
    )
    assert version == SAMPLE_SCHEMA_VERSION


def test_v24_repairs_exact_finding_diff_text_without_changing_counts(
    sample_engine: sa.Engine,
) -> None:
    preserved = _diff_entry(CURRENT_TEMPLATE, "GA")
    malformed = "malformed entry"
    diff = {
        "schema_version": 1,
        "before_releases": {"pipeline": "legacy"},
        "after_releases": {"pipeline": "current"},
        "release_deltas": [],
        "generated_at": "2026-07-31T13:00:00Z",
        "dismissed": False,
        "future_metadata": {"preserve": True},
        "changed": [_diff_entry(LEGACY_TEMPLATES[0], "GA"), preserved],
        "added": [_diff_entry(LEGACY_TEMPLATES[1], "AA")],
        "removed": [_diff_entry(LEGACY_TEMPLATES[1], "TT"), malformed],
        "counts": {"changed": 2, "added": 1, "removed": 2},
    }
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 23"))

    assert ensure_sample_schema_current(sample_engine) is True
    with sample_engine.connect() as conn:
        stored = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json"
                )
            ).scalar_one()
        )

    assert stored["counts"] == diff["counts"]
    assert stored["future_metadata"] == {"preserve": True}
    repaired_preserved = {**preserved, "finding_text": _repaired(CURRENT_TEMPLATE, "GA")}
    assert stored["changed"] == [
        _diff_entry(CURRENT_TEMPLATE, "GA", finding_text=_repaired(CURRENT_TEMPLATE, "GA")),
        repaired_preserved,
    ]
    assert stored["added"] == [
        _diff_entry(CURRENT_TEMPLATE, "AA", finding_text=_repaired(CURRENT_TEMPLATE, "AA"))
    ]
    assert stored["removed"] == [
        _diff_entry(CURRENT_TEMPLATE, "TT", finding_text=_repaired(CURRENT_TEMPLATE, "TT")),
        malformed,
    ]


def test_v24_repairs_legacy_finding_without_provenance_column() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE findings ("
                "id INTEGER PRIMARY KEY, module TEXT, category TEXT, gene_symbol TEXT, "
                "rsid TEXT, conditions TEXT, finding_text TEXT, detail_json TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO findings "
                "(id, module, category, gene_symbol, rsid, conditions, finding_text, detail_json) "
                "VALUES (1, 'parkinsons', 'risk_genotype', 'LRRK2', 'rs34637584', "
                ":conditions, :finding_text, :detail_json)"
            ),
            {
                "conditions": RISK_CLASSIFICATION,
                "finding_text": _render(LEGACY_TEMPLATES[1], "GA"),
                "detail_json": _detail("GA"),
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 23"))

    assert ensure_sample_schema_current(engine) is True

    assert "provenance" not in {
        column["name"] for column in sa.inspect(engine).get_columns("findings")
    }
    with engine.connect() as conn:
        text = conn.execute(sa.text("SELECT finding_text FROM findings WHERE id = 1")).scalar_one()
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()
    assert text == _repaired(CURRENT_TEMPLATE, "GA")
    assert version == SAMPLE_SCHEMA_VERSION


def test_v24_locks_before_reading_or_writing_findings_and_diff(
    sample_engine: sa.Engine,
) -> None:
    diff = {
        "changed": [_diff_entry(LEGACY_TEMPLATES[0], "GA")],
        "added": [],
        "removed": [],
        "counts": {"changed": 1, "added": 0, "removed": 0},
    }
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), _finding(1, LEGACY_TEMPLATES[1], "GA"))
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 23"))

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
        if statement.startswith("SELECT FINDINGS.ID, FINDINGS.FINDING_TEXT, FINDINGS.DETAIL_JSON")
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


def test_v24_rolls_back_all_repairs_when_one_update_fails(
    sample_engine: sa.Engine,
) -> None:
    rows = [
        _finding(1, LEGACY_TEMPLATES[0], "GA"),
        _finding(2, LEGACY_TEMPLATES[1], "AA"),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_second_parkinsons_repair "
                "BEFORE UPDATE OF finding_text ON findings "
                "WHEN OLD.id = 2 "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 23"))

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)

    with sample_engine.connect() as conn:
        after = list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == 23
