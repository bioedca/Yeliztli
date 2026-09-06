"""Tests for the v26 -> v27 persisted rsID-doubling repair (#2051)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    _dedoubled_rsid_finding_text,
    ensure_sample_schema_current,
)
from backend.db.tables import annotation_state, findings

# One persisted row per affected module: (module, gene, stored rsid column, stored text,
# recorded genotype_calls, repaired text). The calls cover diploid, haploid, and untyped.
DOUBLED_ROWS = [
    (
        "gout",
        "SLC2A9",
        "rs13129697",
        "SLC2A9 urate-raising allele, one copy (rs13129697 rs13129697 GT) — one copy.",
        {"rs13129697": "GT"},
        "SLC2A9 urate-raising allele, one copy (rs13129697 GT) — one copy.",
    ),
    (
        "amd",
        "CFH",
        "rs1061170",
        "CFH Y402H homozygous (rs1061170 rs1061170 CC) — both copies carry the risk allele.",
        {"rs1061170": "CC"},
        "CFH Y402H homozygous (rs1061170 CC) — both copies carry the risk allele.",
    ),
    (
        "thrombophilia",
        "F5",
        "rs6025",
        "Factor V Leiden heterozygous (rs6025 rs6025 GA) — you carry one copy.",
        {"rs6025": "GA"},
        "Factor V Leiden heterozygous (rs6025 GA) — you carry one copy.",
    ),
    (
        "lhon",
        "MT-ND4",
        "rs199476112",
        "A primary LHON mutation, MT-ND4 m.11778G>A (rs199476112 rs199476112 A), was detected.",
        {"rs199476112": "A"},
        "A primary LHON mutation, MT-ND4 m.11778G>A (rs199476112 A), was detected.",
    ),
    (
        "mt_rnr1",
        "MT-RNR1",
        "rs267606617",
        "MT-RNR1 m.1555A>G detected (rs267606617 rs267606617 n/a). Preliminary context.",
        {"rs267606617": None},
        "MT-RNR1 m.1555A>G detected (rs267606617 n/a). Preliminary context.",
    ),
    (
        "parkinsons",
        "LRRK2",
        "rs34637584",
        "LRRK2 G2019S (rs34637584 rs34637584 GA) detected. Not a diagnosis.",
        {"rs34637584": "GA"},
        "LRRK2 G2019S (rs34637584 GA) detected. Not a diagnosis.",
    ),
]
COMPOUND_TEXT = (
    "You carry one copy each of Factor V Leiden (rs6025) and Prothrombin G20210A (rs1799963) "
    "(rs6025 GA; rs1799963 GA) — double heterozygous."
)
GOUT_TEXT = DOUBLED_ROWS[0][3]
GOUT_CALLS = DOUBLED_ROWS[0][4]


def _detail(genotype_calls: object, **overrides: object) -> str:
    payload: dict[str, object] = {
        "model_id": "slc2a9_urate_heterozygous",
        "classification": "SLC2A9 urate-raising allele carrier",
        "genotype_calls": genotype_calls,
        "future_metadata": {"preserve": True},
    }
    payload.update(overrides)
    return json.dumps(payload)


def _finding(
    row_id: int,
    module: str,
    gene: str,
    rsid: str,
    text: str,
    genotype_calls: object,
    **overrides: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": row_id,
        "module": module,
        "category": "risk_genotype",
        "evidence_level": 2,
        "gene_symbol": gene,
        "rsid": rsid,
        "conditions": f"{gene} risk genotype",
        "finding_text": text,
        "pmid_citations": json.dumps(["12345678"]),
        "detail_json": _detail(genotype_calls),
        "provenance": json.dumps({"pipeline_version": "legacy"}),
    }
    row.update(overrides)
    return row


def _diff_entry(module: str, rsid: str, text: str, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "module": module,
        "category": "risk_genotype",
        "gene_symbol": "GENE",
        "rsid": rsid,
        "drug": None,
        "diplotype": None,
        "pathway": None,
        "trait": None,
        "finding_text": text,
        "clinvar_significance": None,
        "evidence_level": 2,
        "metabolizer_status": None,
        "pathway_level": None,
    }
    entry.update(overrides)
    return entry


def _rows(conn: sa.Connection) -> list[dict[str, object]]:
    return [
        dict(row) for row in conn.execute(sa.select(findings).order_by(findings.c.id)).mappings()
    ]


@pytest.mark.parametrize(
    ("text", "stored_rsids", "genotype_calls", "expected"),
    [
        (GOUT_TEXT, "rs13129697", GOUT_CALLS, DOUBLED_ROWS[0][5]),
        # A diff entry carries no structured calls: the rsid column still has to agree.
        (GOUT_TEXT, "rs13129697", None, DOUBLED_ROWS[0][5]),
        (GOUT_TEXT, "rs2231142", None, None),
        # The ';' lookahead lets a doubled first locus of a multi-locus group be repaired.
        (
            "Compound (rs6025 rs6025 GA; rs1799963 GA) — double heterozygous.",
            "rs6025,rs1799963",
            {"rs6025": "GA", "rs1799963": "GA"},
            "Compound (rs6025 GA; rs1799963 GA) — double heterozygous.",
        ),
        (COMPOUND_TEXT, "rs6025,rs1799963", {"rs6025": "GA", "rs1799963": "GA"}, None),
        (GOUT_TEXT, "rs13129697", {"rs13129697": "TT"}, None),
        (GOUT_TEXT, "rs13129697", {"rs2231142": "GT"}, None),
        (GOUT_TEXT, "rs13129697", {"rs13129697": ["G", "T"]}, None),
        ("Two groups (rs6025 rs6025 GA) and (rs6025 rs6025 GA).", "rs6025", None, None),
        ("Distinct loci (rs6025 rs1799963 GA).", "rs6025,rs1799963", None, None),
        ("Already repaired (rs13129697 GT).", "rs13129697", GOUT_CALLS, None),
        (None, "rs13129697", GOUT_CALLS, None),
        (GOUT_TEXT, None, GOUT_CALLS, None),
    ],
)
def test_dedoubled_rsid_finding_text_matches_only_the_producer_fingerprint(
    text: object, stored_rsids: object, genotype_calls: object, expected: object
) -> None:
    assert _dedoubled_rsid_finding_text(text, stored_rsids, genotype_calls) == expected


def test_v27_repairs_the_doubled_rsid_in_every_affected_module(
    sample_engine: sa.Engine,
) -> None:
    created_at = datetime(2026, 7, 16, 11, 38)
    rows = [
        _finding(index + 1, module, gene, rsid, text, calls, created_at=created_at)
        for index, (module, gene, rsid, text, calls, _repaired) in enumerate(DOUBLED_ROWS)
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = _rows(conn)
        conn.execute(sa.text("PRAGMA user_version = 26"))
    assert len(before) == len(DOUBLED_ROWS)

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        after = _rows(conn)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert version == SAMPLE_SCHEMA_VERSION == 27
    assert [row["finding_text"] for row in after] == [row[5] for row in DOUBLED_ROWS]
    assert [{**row, "finding_text": None} for row in after] == [
        {**row, "finding_text": None} for row in before
    ]
    assert all("(rs" in row["finding_text"] for row in after)

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        assert _rows(conn) == after


def test_v27_leaves_rows_without_the_fingerprint_untouched(sample_engine: sa.Engine) -> None:
    rows = [
        # Other module, other category, and a row from a panel that never doubled.
        _finding(1, "hemochromatosis", "HFE", "rs1799945", GOUT_TEXT, GOUT_CALLS),
        _finding(2, "gout", "SLC2A9", "rs13129697", GOUT_TEXT, GOUT_CALLS, category="custom"),
        _finding(3, "gout", "SLC2A9", "rs13129697", DOUBLED_ROWS[0][5], GOUT_CALLS),
        # Stored rsid, recorded call, or structured evidence disagreeing with the text.
        _finding(4, "gout", "ABCG2", "rs2231142", GOUT_TEXT, GOUT_CALLS),
        _finding(5, "gout", "SLC2A9", "rs13129697", GOUT_TEXT, {"rs13129697": "TT"}),
        _finding(6, "gout", "SLC2A9", "rs13129697", GOUT_TEXT, {"rs2231142": "GT"}),
        _finding(7, "gout", "SLC2A9", "rs13129697", GOUT_TEXT, "not-a-mapping"),
        _finding(8, "gout", "SLC2A9", "rs13129697", GOUT_TEXT, GOUT_CALLS, detail_json="{oops"),
        _finding(9, "gout", "SLC2A9", "rs13129697", GOUT_TEXT, GOUT_CALLS, detail_json="[]"),
        # Text shapes that are not the single doubled group the producer emitted.
        _finding(
            10,
            "gout",
            "SLC2A9",
            "rs13129697",
            "Twice (rs13129697 rs13129697 GT) and (rs13129697 rs13129697 GT).",
            GOUT_CALLS,
        ),
        _finding(
            11,
            "thrombophilia",
            "F5",
            "rs6025,rs1799963",
            COMPOUND_TEXT,
            {"rs6025": "GA", "rs1799963": "GA"},
        ),
        _finding(
            12,
            "thrombophilia",
            "F5",
            "rs6025,rs1799963",
            "Distinct loci (rs6025 rs1799963 GA).",
            {"rs6025": "GA", "rs1799963": "GA"},
        ),
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = _rows(conn)
        conn.execute(sa.text("PRAGMA user_version = 26"))
    assert len(before) == 12

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        after = _rows(conn)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == SAMPLE_SCHEMA_VERSION


def test_v27_repairs_finding_diff_entries_without_changing_counts(
    sample_engine: sa.Engine,
) -> None:
    preserved = _diff_entry("hemochromatosis", "rs1799945", GOUT_TEXT)
    compound = _diff_entry("thrombophilia", "rs6025,rs1799963", COMPOUND_TEXT)
    malformed = "malformed entry"
    diff = {
        "schema_version": 1,
        "before_releases": {"pipeline": "legacy"},
        "after_releases": {"pipeline": "current"},
        "release_deltas": [],
        "generated_at": "2026-07-16T13:00:00Z",
        "dismissed": False,
        "future_metadata": {"preserve": True},
        "changed": [_diff_entry("gout", "rs13129697", GOUT_TEXT), preserved],
        "added": [_diff_entry("amd", "rs1061170", DOUBLED_ROWS[1][3]), compound],
        "removed": [_diff_entry("parkinsons", "rs34637584", DOUBLED_ROWS[5][3]), malformed],
        "counts": {"changed": 2, "added": 2, "removed": 2},
    }
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 26"))

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
    assert stored["changed"] == [
        _diff_entry("gout", "rs13129697", DOUBLED_ROWS[0][5]),
        preserved,
    ]
    assert stored["added"] == [_diff_entry("amd", "rs1061170", DOUBLED_ROWS[1][5]), compound]
    assert stored["removed"] == [
        _diff_entry("parkinsons", "rs34637584", DOUBLED_ROWS[5][5]),
        malformed,
    ]


def test_v27_repairs_findings_without_optional_columns() -> None:
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
                "VALUES (1, 'amd', 'risk_genotype', 'CFH', 'rs1061170', 'CFH Y402H', "
                ":finding_text, :detail_json)"
            ),
            {"finding_text": DOUBLED_ROWS[1][3], "detail_json": _detail(DOUBLED_ROWS[1][4])},
        )
        conn.execute(sa.text("PRAGMA user_version = 26"))

    assert ensure_sample_schema_current(engine) is True

    columns = {column["name"] for column in sa.inspect(engine).get_columns("findings")}
    assert {"provenance", "pmid_citations"}.isdisjoint(columns)
    with engine.connect() as conn:
        text = conn.execute(sa.text("SELECT finding_text FROM findings WHERE id = 1")).scalar_one()
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()
    assert text == DOUBLED_ROWS[1][5]
    assert version == SAMPLE_SCHEMA_VERSION


def test_v27_locks_before_reading_or_writing_findings_and_diff(
    sample_engine: sa.Engine,
) -> None:
    diff = {
        "changed": [_diff_entry("gout", "rs13129697", GOUT_TEXT)],
        "added": [],
        "removed": [],
        "counts": {"changed": 1, "added": 0, "removed": 0},
    }
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), _finding(1, *DOUBLED_ROWS[0][:5]))
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 26"))

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
            "SELECT FINDINGS.ID, FINDINGS.RSID, FINDINGS.FINDING_TEXT, FINDINGS.DETAIL_JSON"
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


def test_v27_rolls_back_all_repairs_when_one_update_fails(sample_engine: sa.Engine) -> None:
    rows = [_finding(1, *DOUBLED_ROWS[0][:5]), _finding(2, *DOUBLED_ROWS[1][:5])]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = _rows(conn)
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_second_rsid_repair "
                "BEFORE UPDATE OF finding_text ON findings "
                "WHEN OLD.id = 2 "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 26"))

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)

    with sample_engine.connect() as conn:
        after = _rows(conn)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert after == before
    assert version == 26
