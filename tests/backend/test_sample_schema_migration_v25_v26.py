"""Tests for the v25 → v26 re-screen of persisted XXY false clean negatives (#2040).

Before the screen learned the classifier's ambiguous X band, a sample whose X-het
rate sat between the calibrated bounds with a chrY signal above the noise floor
was stored as an affirmative ``no_aneuploidy_signal``. Nothing recomputes a stored
screen on its own, so the migration has to reach those rows.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.sex_aneuploidy import (
    CATEGORY,
    MANUAL_REVIEW,
    MODULE,
    NO_SIGNAL,
    POSSIBLE_XXY,
)
from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    _add_missing_columns,
    ensure_sample_schema_current,
)
from backend.db.tables import annotation_state, findings, raw_variants

LEGACY_NEGATIVE_TEXT = (
    "No XXY (Klinefelter) genotype signature was detected. Note this screen can "
    "only detect the XXY pattern from genotype data; it cannot detect Turner "
    "syndrome (45,X) or XYY, which require DNA-quantity data this array does not "
    "provide. A negative screen is not a karyotype."
)


def _x_probes(n_het: int, n_hom: int) -> list[dict]:
    rows = []
    pos = 5_000_000
    for i in range(n_het):
        rows.append({"rsid": f"x_het{i}", "chrom": "X", "pos": pos, "genotype": "AG"})
        pos += 137
    for i in range(n_hom):
        rows.append({"rsid": f"x_hom{i}", "chrom": "X", "pos": pos, "genotype": "AA"})
        pos += 137
    return rows


def _y_probes(n_typed: int, n_nocall: int = 0) -> list[dict]:
    rows = []
    pos = 6_000_000
    for i in range(n_typed):
        rows.append({"rsid": f"y_t{i}", "chrom": "Y", "pos": pos, "genotype": "GG"})
        pos += 137
    for i in range(n_nocall):
        rows.append({"rsid": f"y_n{i}", "chrom": "Y", "pos": pos, "genotype": "--"})
        pos += 137
    return rows


def _screen_row(*, row_id: int, detail: dict[str, object], text: str) -> dict[str, object]:
    return {
        "id": row_id,
        "module": MODULE,
        "category": CATEGORY,
        "evidence_level": 1,
        "finding_text": text,
        "conditions": f"Sex-chromosome aneuploidy screen: {detail['outcome']}",
        "clinvar_significance": None,
        "detail_json": json.dumps(detail),
    }


def _legacy_false_negative_detail() -> dict[str, object]:
    # Exactly what the pre-#2040 screen persisted for an ambiguous-X sample with
    # a chrY signal above the noise floor: no ``y_typed`` key, ``no_aneuploidy_signal``.
    return {
        "outcome": NO_SIGNAL,
        "x_nonpar_typed": 220,
        "x_nonpar_het": 20,
        "y_total": 60,
        "y_rate": 0.2,
        "x_evaluable": True,
        "y_evaluable": True,
    }


def _diff_state(entries: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "changed": [],
            "added": entries,
            "removed": [],
            "counts": {"changed": 0, "added": len(entries), "removed": 0},
        }
    )


def _screen_rows(engine: sa.Engine) -> list[dict[str, object]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                sa.select(
                    findings.c.id,
                    findings.c.module,
                    findings.c.category,
                    findings.c.conditions,
                    findings.c.finding_text,
                    findings.c.detail_json,
                ).order_by(findings.c.id)
            ).mappings()
        ]


def _user_version(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text("PRAGMA user_version")).scalar_one()


def test_v26_rescreens_the_exact_false_clean_negative(sample_engine: sa.Engine) -> None:
    """The persisted negative is replaced by what the screen now says from the raw data."""
    unrelated_entry = {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "finding_text": "Keep me",
    }
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), _x_probes(20, 200) + _y_probes(12, 48))
        conn.execute(
            findings.insert(),
            _screen_row(
                row_id=1, detail=_legacy_false_negative_detail(), text=LEGACY_NEGATIVE_TEXT
            ),
        )
        conn.execute(
            annotation_state.insert(),
            {
                "key": "last_finding_diff_json",
                "value": _diff_state(
                    [
                        {
                            "module": MODULE,
                            "category": CATEGORY,
                            "finding_text": LEGACY_NEGATIVE_TEXT,
                        },
                        unrelated_entry,
                    ]
                ),
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 25"))

    assert ensure_sample_schema_current(sample_engine) is True

    rows = _screen_rows(sample_engine)
    assert len(rows) == 1, "exactly one screen row per sample, the stale one replaced"
    detail = json.loads(rows[0]["detail_json"])
    assert detail["outcome"] == MANUAL_REVIEW
    assert detail["y_typed"] == 12 and detail["y_total"] == 60
    assert rows[0]["conditions"] == f"Sex-chromosome aneuploidy screen: {MANUAL_REVIEW}"
    assert "needs manual review" in rows[0]["finding_text"]
    assert "No XXY" not in rows[0]["finding_text"]
    assert _user_version(sample_engine) == SAMPLE_SCHEMA_VERSION == 28

    with sample_engine.connect() as conn:
        diff = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json"
                )
            ).scalar_one()
        )
    assert diff["added"] == [unrelated_entry], "only the superseded negative leaves the banner"
    assert diff["counts"] == {"changed": 0, "added": 1, "removed": 0}

    snapshot = rows
    assert ensure_sample_schema_current(sample_engine) is False
    assert _screen_rows(sample_engine) == snapshot


def test_v26_leaves_legitimate_and_unrelated_stored_results_untouched(
    sample_engine: sa.Engine,
) -> None:
    """A negative with no chrY signal, a positive, and other modules are not the bug."""
    legitimate_negative = {**_legacy_false_negative_detail(), "y_rate": 0.0}
    diploid_negative = {
        **_legacy_false_negative_detail(),
        "x_nonpar_het": 60,
        "x_nonpar_typed": 120,
        "y_rate": 0.05,
    }
    positive = {
        **_legacy_false_negative_detail(),
        "outcome": POSSIBLE_XXY,
        "x_nonpar_het": 60,
        "x_nonpar_typed": 120,
        "y_rate": 1.0,
    }
    banner = _diff_state(
        [{"module": MODULE, "category": CATEGORY, "finding_text": LEGACY_NEGATIVE_TEXT}]
    )
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), _x_probes(20, 200) + _y_probes(0, 60))
        conn.execute(
            findings.insert(),
            [
                _screen_row(row_id=1, detail=legitimate_negative, text=LEGACY_NEGATIVE_TEXT),
                _screen_row(row_id=2, detail=diploid_negative, text=LEGACY_NEGATIVE_TEXT),
                _screen_row(row_id=3, detail=positive, text="Screen suggests a possible ..."),
                {
                    "id": 4,
                    "module": "pharmacogenomics",
                    "category": "prescribing_alert",
                    "evidence_level": 4,
                    "finding_text": "unrelated",
                    "conditions": None,
                    "clinvar_significance": None,
                    "detail_json": json.dumps({"outcome": NO_SIGNAL}),
                },
            ],
        )
        conn.execute(annotation_state.insert(), {"key": "last_finding_diff_json", "value": banner})
        conn.execute(sa.text("PRAGMA user_version = 25"))
    before = _screen_rows(sample_engine)

    assert ensure_sample_schema_current(sample_engine) is False

    assert _screen_rows(sample_engine) == before
    with sample_engine.connect() as conn:
        after_banner = conn.execute(
            sa.select(annotation_state.c.value).where(
                annotation_state.c.key == "last_finding_diff_json"
            )
        ).scalar_one()
    assert after_banner == banner, "a legitimate negative keeps its banner entry"
    assert _user_version(sample_engine) == SAMPLE_SCHEMA_VERSION == 28


def test_v26_quarantines_when_the_row_cannot_be_rescreened(sample_engine: sa.Engine) -> None:
    """With no raw variants to read, the false negative is removed rather than kept.

    ``ensure_sample_schema_current`` recreates every table first, so the
    no-raw-variants path is only reachable through the restore workflow, which
    calls the column-migration helper directly.
    """
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert(),
            _screen_row(
                row_id=1, detail=_legacy_false_negative_detail(), text=LEGACY_NEGATIVE_TEXT
            ),
        )
        conn.execute(sa.text("DROP TABLE raw_variants"))

    assert _add_missing_columns(sample_engine, 25) is True

    assert _screen_rows(sample_engine) == []


def test_v26_ignores_malformed_screen_payloads(sample_engine: sa.Engine) -> None:
    """A row the predicate cannot read is neither re-screened nor deleted."""
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), _x_probes(20, 200) + _y_probes(12, 48))
        conn.execute(
            findings.insert(),
            [
                {
                    "id": 1,
                    "module": MODULE,
                    "category": CATEGORY,
                    "evidence_level": 1,
                    "finding_text": LEGACY_NEGATIVE_TEXT,
                    "detail_json": "{not-json",
                },
                {
                    "id": 2,
                    "module": MODULE,
                    "category": CATEGORY,
                    "evidence_level": 1,
                    "finding_text": LEGACY_NEGATIVE_TEXT,
                    "detail_json": None,
                },
            ],
        )
        conn.execute(sa.text("PRAGMA user_version = 25"))
    before = _screen_rows(sample_engine)

    assert ensure_sample_schema_current(sample_engine) is False

    assert _screen_rows(sample_engine) == before


def test_v26_rolls_back_the_rescreen_when_the_banner_update_fails(
    sample_engine: sa.Engine,
) -> None:
    """The replacement row and the banner scrub commit together or not at all.

    Committed separately, a crash between them would leave a ``manual_review``
    row the legacy predicate no longer matches, so the next open would stamp v26
    without ever scrubbing the superseded negative from the banner.
    """
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), _x_probes(20, 200) + _y_probes(12, 48))
        conn.execute(
            findings.insert(),
            _screen_row(
                row_id=1, detail=_legacy_false_negative_detail(), text=LEGACY_NEGATIVE_TEXT
            ),
        )
        conn.execute(
            annotation_state.insert(),
            {
                "key": "last_finding_diff_json",
                "value": _diff_state(
                    [
                        {
                            "module": MODULE,
                            "category": CATEGORY,
                            "finding_text": LEGACY_NEGATIVE_TEXT,
                        }
                    ]
                ),
            },
        )
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_banner_scrub "
                "BEFORE UPDATE OF value ON annotation_state "
                "WHEN OLD.key = 'last_finding_diff_json' "
                "BEGIN SELECT RAISE(ABORT, 'blocked banner scrub'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 25"))
    before = _screen_rows(sample_engine)

    with pytest.raises(sa.exc.IntegrityError, match="blocked banner scrub"):
        ensure_sample_schema_current(sample_engine)

    assert _screen_rows(sample_engine) == before, "the replacement row must roll back too"
    assert _user_version(sample_engine) == 25
