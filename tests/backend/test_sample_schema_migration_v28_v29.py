"""Tests for the v28 -> v29 persisted PRS ancestry-caveat repair (#2056)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    _uncertain_ancestry_warning_without_admixture,
    ensure_sample_schema_current,
)
from backend.db.tables import findings

UNCERTAIN = (
    "Ancestry could not be confidently inferred (insufficient data), so the match "
    "between your background and this score's development population cannot be assessed"
)
ADMIXED = (
    "Your genotype did not resolve to a single top ancestry (admixed composition), so it "
    "cannot be matched to this score's development population. PRS portability depends on "
    "ancestry composition, linkage disequilibrium, and allele-frequency differences"
)
CLAUSE = (
    " Your ancestry composition is admixed (top ancestry {pct}%). "
    "PRS accuracy may be reduced for admixed genetic backgrounds."
)
WITHHELD = f"{UNCERTAIN}." + CLAUSE.format(pct=62)
REPORTED = f"{UNCERTAIN}. Interpret the percentile with caution." + CLAUSE.format(pct=58)


def _detail(warning: object, **overrides: object) -> str:
    payload: dict[str, object] = {
        "trait": "ldl_c",
        "name": "LDL-C PRS",
        "source_ancestry": "EUR",
        "ancestry_mismatch": True,
        "ancestry_warning_text": warning,
        "future_metadata": {"preserve": True},
    }
    payload.update(overrides)
    return json.dumps(payload)


def _finding(row_id: int, module: str, detail: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": row_id,
        "module": module,
        "category": "prs",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "conditions": None,
        "finding_text": "LDL-C PRS: population percentile not reported — Research Use Only",
        "pmid_citations": json.dumps(["12345678"]),
        "detail_json": detail,
        "provenance": json.dumps({"pipeline_version": "legacy"}),
    }
    row.update(overrides)
    return row


def _rows(conn: sa.Connection) -> list[dict[str, object]]:
    return [
        dict(row) for row in conn.execute(sa.select(findings).order_by(findings.c.id)).mappings()
    ]


@pytest.mark.parametrize(
    ("warning", "expected"),
    [
        (WITHHELD, f"{UNCERTAIN}."),
        (REPORTED, f"{UNCERTAIN}. Interpret the percentile with caution."),
        (f"{UNCERTAIN}.", None),
        (f"{ADMIXED}." + CLAUSE.format(pct=55), None),
        (
            "This PRS was derived from a single-ancestry (EUR) population study. Your inferred "
            "ancestry (AFR) differs from the source population." + CLAUSE.format(pct=62),
            None,
        ),
        (WITHHELD + " Custom suffix.", None),
        (None, None),
        (["not", "a-string"], None),
    ],
)
def test_helper_repairs_only_the_uncertain_producer_shape(
    warning: object, expected: object
) -> None:
    assert _uncertain_ancestry_warning_without_admixture(warning) == expected


def test_v29_strips_the_admixture_clause_from_stored_uncertain_caveats(
    sample_engine: sa.Engine,
) -> None:
    created_at = datetime(2026, 7, 16, 12, 2)
    rows = [
        _finding(1, "fh", _detail(WITHHELD), created_at=created_at),
        _finding(2, "cancer", _detail(REPORTED), created_at=created_at),
        _finding(3, "metabolic", _detail(WITHHELD, coverage_fraction=0.42), created_at=created_at),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = _rows(conn)
        conn.execute(sa.text("PRAGMA user_version = 28"))
    assert len(before) == 3

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        after = _rows(conn)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert version == SAMPLE_SCHEMA_VERSION == 29
    warnings = [json.loads(row["detail_json"])["ancestry_warning_text"] for row in after]
    assert warnings == [
        f"{UNCERTAIN}.",
        f"{UNCERTAIN}. Interpret the percentile with caution.",
        f"{UNCERTAIN}.",
    ]
    # Every other detail key survives, and no other column moves.
    for old, new in zip(before, after, strict=True):
        old_detail = json.loads(old["detail_json"])
        new_detail = json.loads(new["detail_json"])
        old_detail.pop("ancestry_warning_text")
        new_detail.pop("ancestry_warning_text")
        assert new_detail == old_detail
        assert {**new, "detail_json": None} == {**old, "detail_json": None}

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        assert _rows(conn) == after


def test_v29_leaves_rows_without_the_fingerprint_untouched(sample_engine: sa.Engine) -> None:
    rows = [
        _finding(1, "fh", _detail(f"{ADMIXED}." + CLAUSE.format(pct=55))),
        _finding(2, "fh", _detail(f"{UNCERTAIN}.")),
        _finding(3, "fh", _detail(f"{UNCERTAIN}. Interpret the percentile with caution.")),
        _finding(4, "fh", _detail(None)),
        _finding(5, "fh", _detail(WITHHELD + " Custom suffix.")),
        _finding(6, "fh", _detail(WITHHELD), category="risk_genotype"),
        _finding(7, "fh", "{oops"),
        _finding(8, "fh", "[]"),
        _finding(
            9,
            "traits",
            _detail(
                "This PRS was derived from a single-ancestry (EUR) population study. Your "
                "inferred ancestry (AFR) differs from the source population."
                + CLAUSE.format(pct=62)
            ),
        ),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = _rows(conn)
        conn.execute(sa.text("PRAGMA user_version = 28"))
    assert len(before) == 9

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        after = _rows(conn)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == SAMPLE_SCHEMA_VERSION


def test_v29_repairs_findings_with_only_the_minimal_columns() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE findings (id INTEGER PRIMARY KEY, category TEXT, detail_json TEXT)"
            )
        )
        conn.execute(
            sa.text("INSERT INTO findings (id, category, detail_json) VALUES (1, 'prs', :d)"),
            {"d": _detail(WITHHELD)},
        )
        conn.execute(sa.text("PRAGMA user_version = 28"))

    assert ensure_sample_schema_current(engine) is True

    with engine.connect() as conn:
        detail = json.loads(
            conn.execute(sa.text("SELECT detail_json FROM findings WHERE id = 1")).scalar_one()
        )
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()
    assert detail["ancestry_warning_text"] == f"{UNCERTAIN}."
    assert detail["future_metadata"] == {"preserve": True}
    assert version == SAMPLE_SCHEMA_VERSION


def test_v29_locks_before_reading_or_writing_findings(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), _finding(1, "fh", _detail(WITHHELD)))
        conn.execute(sa.text("PRAGMA user_version = 28"))

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
        if statement.startswith("SELECT FINDINGS.ID, FINDINGS.DETAIL_JSON")
    )
    update_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("UPDATE FINDINGS SET")
    )
    assert begin_index < candidate_index < update_index


def test_v29_rolls_back_all_repairs_when_one_update_fails(sample_engine: sa.Engine) -> None:
    rows = [_finding(1, "fh", _detail(WITHHELD)), _finding(2, "cancer", _detail(REPORTED))]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = _rows(conn)
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_second_caveat_repair "
                "BEFORE UPDATE OF detail_json ON findings "
                "WHEN OLD.id = 2 "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 28"))

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)

    with sample_engine.connect() as conn:
        after = _rows(conn)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == 28
