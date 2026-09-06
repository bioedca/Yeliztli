"""Tests for the v27 → v28 repair of persisted doubled-gene finding text (#2044).

Eight categorical modules built per-SNP finding text as ``f"{gene} {variant_name}"``
while about half of the curated variant names already lead with their gene, so
existing sample databases persist rows such as ``FTO FTO intron 1 (AA) — …``. The
module GET handlers return the stored text verbatim, so the formatter fix alone
repairs nothing an upgraded sample shows.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings

_DIFF_KEY = "last_finding_diff_json"


def _snp_row(
    *, row_id: int, module: str, gene: str, text: str, category: str = "snp_finding"
) -> dict[str, object]:
    return {
        "id": row_id,
        "module": module,
        "category": category,
        "evidence_level": 3,
        "gene_symbol": gene,
        "rsid": f"rs{row_id}",
        "finding_text": text,
        "detail_json": json.dumps({"variant_name": text.split(" (")[0]}),
    }


def _texts(engine: sa.Engine) -> dict[int, str]:
    with engine.connect() as conn:
        return {
            r.id: r.finding_text
            for r in conn.execute(sa.select(findings.c.id, findings.c.finding_text)).fetchall()
        }


def _diff(engine: sa.Engine) -> dict[str, object]:
    with engine.connect() as conn:
        return json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(annotation_state.c.key == _DIFF_KEY)
            ).scalar_one()
        )


def _user_version(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text("PRAGMA user_version")).scalar_one()


DOUBLED = {
    1: ("fitness", "FTO", "FTO FTO intron 1 (AA) — Higher-risk genotype; appetite"),
    2: ("nutrigenomics", "MCM6/LCT", "MCM6/LCT LCT -13910C>T (CC) — lactase non-persistence"),
    3: ("gene_health", "CAV1/CAV2", "CAV1/CAV2 CAV1-CAV2 intergenic (GG) — moderate"),
    4: ("skin", "HLA-B", "HLA-B HLA-B*57:01 proxy (AG) — carrier"),
}
REPAIRED = {
    1: "FTO intron 1 (AA) — Higher-risk genotype; appetite",
    2: "LCT -13910C>T (CC) — lactase non-persistence",
    3: "CAV1-CAV2 intergenic (GG) — moderate",
    4: "HLA-B*57:01 proxy (AG) — carrier",
}
UNTOUCHED = {
    11: ("fitness", "MTHFR", "MTHFR C677T (TT) — reduced activity"),  # descriptor-only name
    12: ("traits", "IL2", "IL2 IL2RA intron 1 (AA) — different gene, shared prefix"),
    13: ("sleep", "CLOCK", "CLOCK 3111T/C (CC) — already correct"),
    14: ("pharmacogenomics", "CYP2D6", "CYP2D6 CYP2D6*4 (AA) — other module, left alone"),
    15: ("fitness", None, "FTO FTO intron 1 (AA) — no gene column"),
    16: ("methylation", "MTHFR", "A1298C (CC) — text does not start with the gene"),
}


def _seed(engine: sa.Engine) -> None:
    rows = [
        _snp_row(row_id=i, module=m, gene=g, text=t)
        for i, (m, g, t) in {**DOUBLED, **UNTOUCHED}.items()
    ]
    diff = {
        "schema_version": 1,
        "changed": [
            {
                "module": "fitness",
                "category": "snp_finding",
                "gene_symbol": "FTO",
                "rsid": "rs1",
                "finding_text": DOUBLED[1][2],
            },
            {
                "module": "fitness",
                "category": "snp_finding",
                "gene_symbol": "MTHFR",
                "rsid": "rs11",
                "finding_text": UNTOUCHED[11][2],
            },
        ],
        "added": [
            {
                "module": "nutrigenomics",
                "category": "snp_finding",
                "gene_symbol": "MCM6/LCT",
                "rsid": "rs2",
                "finding_text": DOUBLED[2][2],
            },
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2D6",
                "finding_text": UNTOUCHED[14][2],
            },
        ],
        "removed": [],
        "counts": {"changed": 2, "added": 2, "removed": 0},
    }
    with engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        conn.execute(annotation_state.insert(), {"key": _DIFF_KEY, "value": json.dumps(diff)})
        conn.execute(sa.text("PRAGMA user_version = 27"))


def test_v28_repairs_exactly_the_doubled_rows(sample_engine: sa.Engine) -> None:
    _seed(sample_engine)
    before = _texts(sample_engine)

    assert ensure_sample_schema_current(sample_engine) is True

    after = _texts(sample_engine)
    for row_id, expected in REPAIRED.items():
        assert after[row_id] == expected, row_id
    for row_id, (_module, _gene, text) in UNTOUCHED.items():
        assert after[row_id] == text == before[row_id], row_id
    assert _user_version(sample_engine) == SAMPLE_SCHEMA_VERSION == 28

    snapshot = after
    assert ensure_sample_schema_current(sample_engine) is False
    assert _texts(sample_engine) == snapshot


def test_v28_repairs_banner_text_without_changing_counts(sample_engine: sa.Engine) -> None:
    _seed(sample_engine)
    before = _diff(sample_engine)

    assert ensure_sample_schema_current(sample_engine) is True

    after = _diff(sample_engine)
    assert after["counts"] == before["counts"] == {"changed": 2, "added": 2, "removed": 0}
    assert [e["finding_text"] for e in after["changed"]] == [REPAIRED[1], UNTOUCHED[11][2]]
    assert [e["finding_text"] for e in after["added"]] == [REPAIRED[2], UNTOUCHED[14][2]]
    assert after["removed"] == []
    assert after["schema_version"] == 1


def test_v28_rolls_back_every_repair_when_one_update_fails(sample_engine: sa.Engine) -> None:
    _seed(sample_engine)
    before_texts = _texts(sample_engine)
    before_diff = _diff(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_second_dedouble "
                "BEFORE UPDATE OF finding_text ON findings "
                "WHEN OLD.id = 2 "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)

    assert _texts(sample_engine) == before_texts
    assert _diff(sample_engine) == before_diff
    assert _user_version(sample_engine) == 27


def test_v28_tolerates_a_malformed_banner(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert(), _snp_row(row_id=1, module="fitness", gene="FTO", text=DOUBLED[1][2])
        )
        conn.execute(annotation_state.insert(), {"key": _DIFF_KEY, "value": "{not-json"})
        conn.execute(sa.text("PRAGMA user_version = 27"))

    assert ensure_sample_schema_current(sample_engine) is True

    assert _texts(sample_engine)[1] == REPAIRED[1]
    with sample_engine.connect() as conn:
        raw = conn.execute(
            sa.select(annotation_state.c.value).where(annotation_state.c.key == _DIFF_KEY)
        ).scalar_one()
    assert raw == "{not-json"
