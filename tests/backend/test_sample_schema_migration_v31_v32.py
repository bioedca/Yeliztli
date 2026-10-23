"""Persisted withheld-percentile caveats follow the corrected PRS producer (#2380)."""

import json

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import sample_metadata_obj

UNCERTAIN = (
    "Ancestry could not be confidently inferred (insufficient data), so the match "
    "between your background and this score's development population cannot be assessed."
)
ADMIXED = (
    "Your genotype did not resolve to a single top ancestry (admixed composition), so it "
    "cannot be matched to this score's development population. PRS portability depends on "
    "ancestry composition, linkage disequilibrium, and allele-frequency differences"
)
SINGLE = (
    "This PRS was derived from a single-ancestry (EUR) population study. Your inferred "
    "ancestry (AFR) differs from the source population."
)
MULTI = (
    "This PRS was developed across multiple ancestries (EUR, EAS), none matching "
    "your inferred ancestry (AFR)."
)
GUIDANCE = " Percentile estimates may be less accurate for your genetic background."
CLAUSE = (
    " Your ancestry composition is admixed (top ancestry 62%). "
    "PRS accuracy may be reduced for admixed genetic backgrounds."
)
LEGACY = [
    (UNCERTAIN + " Interpret the percentile with caution.", UNCERTAIN),
    (
        ADMIXED + ", so percentile estimates may be less accurate for an admixed background.",
        ADMIXED + ".",
    ),
    (SINGLE + GUIDANCE, SINGLE),
    (MULTI + GUIDANCE, MULTI),
]


def _detail(warning: object, **overrides: object) -> str:
    return json.dumps(
        {
            "ancestry_warning_text": warning,
            "is_sufficient": True,
            "calibrated": True,
            "percentile": 71.0,
            "future_metadata": {"preserve": True},
            **overrides,
        }
    )


def _engine(rows: list[tuple[str, str]]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE findings (id INTEGER PRIMARY KEY, category TEXT, detail_json TEXT)"
        )
        conn.execute(
            sa.text("INSERT INTO findings VALUES (:id, :category, :detail)"),
            [
                {"id": i, "category": category, "detail": detail}
                for i, (category, detail) in enumerate(rows, 1)
            ],
        )
        conn.exec_driver_sql("PRAGMA user_version = 31")
    sample_metadata_obj.create_all(engine)
    return engine


def _rows(engine: sa.Engine) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.exec_driver_sql("SELECT * FROM findings ORDER BY id")]


@pytest.mark.parametrize(("old", "new"), LEGACY)
@pytest.mark.parametrize(
    "state", [{"is_sufficient": False}, {"calibrated": False}, {"percentile": None}]
)
@pytest.mark.parametrize("suffix", ["", CLAUSE])
def test_v32_repairs_each_legacy_shape_only_when_percentile_withheld(old, new, state, suffix):
    # UNCERTAIN's obsolete composition suffix is repaired by v29 on older DBs;
    # a v31 DB can still contain it, so retain it here without rewriting history.
    detail = _detail(old + suffix, **state)
    engine = _engine([("prs", detail)])
    assert ensure_sample_schema_current(engine) is True
    after = _rows(engine)
    assert len(after) == 1
    assert json.loads(after[0][2]) == {**json.loads(detail), "ancestry_warning_text": new + suffix}
    with engine.connect() as conn:
        assert (
            conn.exec_driver_sql("PRAGMA user_version").scalar_one() == SAMPLE_SCHEMA_VERSION == 32
        )
    assert ensure_sample_schema_current(engine) is False
    assert _rows(engine) == after


def test_v32_preserves_reported_custom_malformed_and_non_prs_rows():
    old = LEGACY[0][0]
    rows = [("prs", _detail(warning)) for warning, _ in LEGACY]
    rows += [
        ("prs", _detail(old + " Custom suffix.", percentile=None)),
        ("prs", _detail("Custom prefix. " + old, calibrated=False)),
        ("prs", _detail(UNCERTAIN, percentile=None)),
        ("prs", _detail(None, percentile=None)),
        ("prs", _detail([old], percentile=None)),
        ("prs", _detail(old, is_sufficient=0, calibrated="false")),
        ("prs", json.dumps({"ancestry_warning_text": old})),
        ("risk_genotype", _detail(old, percentile=None)),
        ("prs", "{oops"),
        ("prs", "[]"),
        ("prs", "null"),
    ]
    engine = _engine(rows)
    before = _rows(engine)
    assert len(before) == len(rows)
    assert ensure_sample_schema_current(engine) is False
    assert _rows(engine) == before


def test_v32_locks_before_read_and_fingerprints_each_update():
    engine = _engine([("prs", _detail(LEGACY[0][0], percentile=None))])
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.split()).upper())

    sa.event.listen(engine, "before_cursor_execute", record)
    try:
        assert ensure_sample_schema_current(engine) is True
    finally:
        sa.event.remove(engine, "before_cursor_execute", record)
    read = next(i for i, s in enumerate(statements) if s.startswith("SELECT FINDINGS.ID,"))
    update = next(i for i, s in enumerate(statements) if s.startswith("UPDATE FINDINGS SET"))
    assert statements.index("BEGIN IMMEDIATE") < read < update
    assert "FINDINGS.ID =" in statements[update]
    assert "FINDINGS.CATEGORY =" in statements[update]
    assert "FINDINGS.DETAIL_JSON =" in statements[update].split("WHERE")[1]


def test_v32_rolls_back_all_repairs_and_version_when_a_write_fails():
    engine = _engine([("prs", _detail(old, percentile=None)) for old, _ in LEGACY])
    before = _rows(engine)
    assert len(before) == 4
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TRIGGER reject_second BEFORE UPDATE ON findings WHEN OLD.id = 2 "
            "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
        )
    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(engine)
    assert _rows(engine) == before
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == 31


@pytest.mark.parametrize("module", ["fh", "cancer", "metabolic", "traits"])
def test_v32_repairs_real_findings_without_changing_other_columns(sample_engine, module):
    from backend.db.tables import findings

    detail = _detail(LEGACY[0][0] + CLAUSE, percentile=None)
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert(),
            {
                "id": 1,
                "module": module,
                "category": "prs",
                "evidence_level": 2,
                "finding_text": "Synthetic score: population percentile not reported",
                "detail_json": detail,
                "provenance": '{"pipeline_version":"legacy"}',
            },
        )
        before = dict(conn.execute(sa.select(findings)).mappings().one())
        # Exercise v29's composition repair followed by v32's guidance repair.
        conn.exec_driver_sql("PRAGMA user_version = 28")
    assert ensure_sample_schema_current(sample_engine) is True
    with sample_engine.connect() as conn:
        after = dict(conn.execute(sa.select(findings)).mappings().one())
    assert json.loads(after["detail_json"]) == {
        **json.loads(detail),
        "ancestry_warning_text": UNCERTAIN,
    }
    assert {**after, "detail_json": None} == {**before, "detail_json": None}
