"""Repair legacy continuous PRS calibration from uncertain ancestry (#2474).

All scores, ancestry fractions and samples here are synthetic. The real storage
producer supplies the historical calibrated-row shape, without asserting that
these synthetic values have any scientific meaning.
"""

import json

import pytest
import sqlalchemy as sa

from backend.analysis.prs import PRSResult, store_prs_findings
from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings

MODULES = ("cancer", "metabolic", "ebmd", "fh", "traits")
FRACTIONS = {"EUR": 0.62, "AFR": 0.38}
WARNING = (
    "Ancestry could not be confidently inferred (insufficient data), so the match "
    "between your background and this score's development population cannot be assessed."
)


def _ancestry(engine, *, category="pca_projection", **overrides):
    detail = {
        "top_population": "UNCERTAIN",
        "is_sufficient": False,
        "classification_status": "uncertain",
        "admixture_fractions": FRACTIONS,
        **overrides,
    }
    with engine.begin() as conn:
        conn.execute(
            findings.insert().values(
                module="ancestry",
                category=category,
                finding_text="Synthetic ancestry result",
                detail_json=json.dumps(detail),
            )
        )


def _legacy_prs(engine, module="cancer", *, higher_is="risk", **overrides):
    result = PRSResult(
        weight_set_name="Synthetic legacy PRS",
        trait="synthetic_legacy",
        module=module,
        source_ancestry="EUR",
        source_study="Synthetic fixture",
        source_pmid="99999999",
        sample_size=1000,
        raw_score=1.5,
        z_score=1.29,
        percentile=90.09,
        snps_used=100,
        snps_total=100,
        coverage_fraction=1.0,
        calibrated=True,
        higher_is=higher_is,
        calibration_method="ancestry_continuous",
        calibration_reference_mean=0.4,
        calibration_reference_std=0.85,
        calibration_variants_used=100,
        calibration_variants_total=100,
        calibration_ancestry_fractions=FRACTIONS,
        calibration_pmids=["37198491", "38374346"],
        ancestry_mismatch=True,
        ancestry_warning_text=WARNING + " Interpret the percentile with caution.",
    )
    assert store_prs_findings([result], engine, module=module) == 1
    with engine.begin() as conn:
        row = conn.execute(sa.select(findings).where(findings.c.module == module)).mappings().one()
        detail = json.loads(row["detail_json"])
        detail.update({"future_metadata": {"preserve": True}, "higher_is": higher_is, **overrides})
        conn.execute(
            findings.update()
            .where(findings.c.id == row["id"])
            .values(
                detail_json=json.dumps(detail),
                provenance='{"pipeline_version":"legacy"}',
            )
        )
        conn.exec_driver_sql("PRAGMA user_version = 35")


def _rows(engine):
    with engine.connect() as conn:
        return list(conn.execute(sa.select(findings).order_by(findings.c.id)).mappings())


def _prs_rows(engine):
    return [dict(row) for row in _rows(engine) if row["category"] == "prs"]


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("higher_is", ["risk", "protective"])
def test_v36_repairs_real_stored_prs_and_preserves_raw_score_coverage_and_provenance(
    sample_engine,
    module,
    higher_is,
):
    _ancestry(sample_engine)
    _legacy_prs(
        sample_engine,
        module,
        higher_is=higher_is,
        bootstrap_ci_lower=70.0,
        bootstrap_ci_upper=95.0,
        bootstrap_iterations=100,
    )
    before = _prs_rows(sample_engine)
    assert len(before) == 1 and before[0]["prs_percentile"] == 90.09
    old = before[0]
    assert ensure_sample_schema_current(sample_engine) is True
    after = _prs_rows(sample_engine)
    assert len(after) == 1
    new = after[0]
    detail = json.loads(new["detail_json"])
    assert new["prs_percentile"] is None
    for key in (
        "percentile",
        "z_score",
        "bootstrap_ci_lower",
        "bootstrap_ci_upper",
        "calibration_method",
        "calibration_reference_mean",
        "calibration_reference_std",
        "calibration_variants_used",
        "calibration_variants_total",
        "calibration_ancestry_fractions",
    ):
        assert detail[key] is None, key
    assert detail["calibrated"] is False
    assert detail["bootstrap_iterations"] == 0
    assert detail["calibration_pmids"] == []
    assert detail["ancestry_warning_text"] == WARNING
    note = (
        "Higher percentiles are protective; lower percentiles indicate higher risk context. — "
        if higher_is == "protective"
        else ""
    )
    assert new["finding_text"] == (
        "Synthetic legacy PRS: population percentile not reported — score lacks a validated "
        f"reference distribution (uncalibrated) — {note}Research Use Only"
    )
    changed_detail = {
        "calibrated",
        "percentile",
        "z_score",
        "bootstrap_ci_lower",
        "bootstrap_ci_upper",
        "bootstrap_iterations",
        "calibration_method",
        "calibration_reference_mean",
        "calibration_reference_std",
        "calibration_variants_used",
        "calibration_variants_total",
        "calibration_ancestry_fractions",
        "calibration_pmids",
        "ancestry_warning_text",
    }
    assert {k: v for k, v in detail.items() if k not in changed_detail} == {
        k: v for k, v in json.loads(old["detail_json"]).items() if k not in changed_detail
    }
    assert {
        k: v for k, v in new.items() if k not in {"prs_percentile", "finding_text", "detail_json"}
    } == {
        k: v for k, v in old.items() if k not in {"prs_percentile", "finding_text", "detail_json"}
    }
    assert new["prs_score"] == 1.5 and detail["coverage_fraction"] == 1.0
    with sample_engine.connect() as conn:
        assert (
            conn.exec_driver_sql("PRAGMA user_version").scalar_one() == SAMPLE_SCHEMA_VERSION == 36
        )
    assert ensure_sample_schema_current(sample_engine) is False
    assert _prs_rows(sample_engine) == after


@pytest.mark.parametrize(
    "uncertain_flag",
    [
        {"top_population": "UNCERTAIN"},
        {"is_sufficient": False},
        {"classification_status": "uncertain"},
    ],
)
def test_v36_recognizes_each_uncertainty_gate_and_normalized_nnls_fractions(
    sample_engine, uncertain_flag
):
    _ancestry(
        sample_engine,
        category="nnls_admixture",
        top_population="EUR",
        is_sufficient=True,
        classification_status="confident",
    )
    with sample_engine.begin() as conn:
        row = conn.execute(sa.select(findings)).mappings().one()
        detail = {
            **json.loads(row["detail_json"]),
            **uncertain_flag,
            "nnls_fractions": {"EUR": 62.0, "AFR": 38.0, "EAS": 0.0},
            "admixture_fractions": {"EUR": 1.0},
        }
        conn.execute(findings.update().values(detail_json=json.dumps(detail)))
    _legacy_prs(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is True
    assert _prs_rows(sample_engine)[0]["prs_percentile"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"calibrated": False},
        {"calibrated": "true"},
        {"is_sufficient": False},
        {"calibration_method": "static_reference"},
        {"calibration_method": None},
        {"calibration_ancestry_fractions": {"EUR": 1.0}},
        {"calibration_ancestry_fractions": [0.62, 0.38]},
        {"calibration_ancestry_fractions": {"EUR": True, "AFR": 0.38}},
        {"percentile": None},
        {"percentile": 12.0},
        {"percentile": True},
        {"z_score": "1.29"},
        {"z_score": float("nan")},
        {"name": "Customized name"},
        {"higher_is": "custom"},
    ],
)
def test_v36_preserves_nonlegacy_or_inconsistent_prs_payloads(sample_engine, overrides):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine, **overrides)
    before = _rows(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is False
    assert _rows(sample_engine) == before


@pytest.mark.parametrize("raw", ["{oops", "[]", "null", '{"calibrated":true,"calibrated":false}'])
def test_v36_preserves_malformed_or_ambiguous_json(sample_engine, raw):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(findings.update().where(findings.c.category == "prs").values(detail_json=raw))
    before = _rows(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is False
    assert _rows(sample_engine) == before


@pytest.mark.parametrize(
    "change",
    [
        {"finding_text": "Custom interpretation: 90th percentile"},
        {"module": "unrelated"},
        {"category": "custom_score"},
        {"prs_percentile": None},
    ],
)
def test_v36_preserves_custom_or_unrelated_rows(sample_engine, change):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(findings.update().where(findings.c.category == "prs").values(**change))
    before = _rows(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is False
    assert _rows(sample_engine) == before


@pytest.mark.parametrize("category", ["pca_projection", "nnls_admixture", "legacy_ancestry"])
@pytest.mark.parametrize("top,status", [("EUR", "confident"), ("ADMIXED", "admixed")])
def test_v36_preserves_confident_and_sufficient_admixed_ancestry(
    sample_engine, category, top, status
):
    _ancestry(
        sample_engine,
        category=category,
        top_population=top,
        is_sufficient=True,
        classification_status=status,
    )
    _legacy_prs(sample_engine)
    before = _rows(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is False
    assert _rows(sample_engine) == before


@pytest.mark.parametrize(
    "source", [None, [], {"EUR": True}, {"EUR": "0.62"}, {"EUR": float("inf")}, {"EUR": -1.0}, {}]
)
def test_v36_preserves_unmatchable_ancestry(sample_engine, source):
    _ancestry(sample_engine, admixture_fractions=source)
    _legacy_prs(sample_engine)
    before = _rows(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is False
    assert _rows(sample_engine) == before


@pytest.mark.parametrize("preferred_category", ["nnls_admixture", "pca_projection"])
def test_v36_uses_ancestry_producer_precedence_and_latest_id(sample_engine, preferred_category):
    _ancestry(sample_engine)
    _ancestry(
        sample_engine,
        category=preferred_category,
        top_population="EUR",
        is_sufficient=True,
        classification_status="confident",
    )
    _legacy_prs(sample_engine)
    before = _rows(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is False
    assert _rows(sample_engine) == before


def test_v36_ignores_quarantined_local_ancestry(sample_engine):
    _ancestry(sample_engine, category="local_ancestry")
    _legacy_prs(sample_engine)
    before = _rows(sample_engine)
    assert ensure_sample_schema_current(sample_engine) is False
    assert _rows(sample_engine) == before


def test_v36_repairs_matching_current_diff_entries_atomically(sample_engine):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine)
    old = _prs_rows(sample_engine)[0]
    entry = {
        "module": "cancer",
        "category": "prs",
        "trait": "synthetic_legacy",
        "finding_text": old["finding_text"],
        "evidence_level": 1,
    }
    custom = {**entry, "finding_text": "Custom text"}
    unrelated = {**entry, "module": "unrelated"}
    payload = {
        "added": [entry, custom, unrelated],
        "changed": [entry],
        "removed": [entry],
        "added_count": 3,
        "changed_count": 1,
        "removed_count": 1,
        "future": True,
    }
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert().values(
                key="last_finding_diff_json", value=json.dumps(payload)
            )
        )
    assert ensure_sample_schema_current(sample_engine) is True
    repaired_text = _prs_rows(sample_engine)[0]["finding_text"]
    with sample_engine.connect() as conn:
        actual = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json"
                )
            ).scalar_one()
        )
    assert actual == {
        **payload,
        "added": [{**entry, "finding_text": repaired_text}, custom, unrelated],
        "changed": [{**entry, "finding_text": repaired_text}],
    }


def test_v36_rolls_back_all_rows_and_version_if_any_update_fails(sample_engine):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine, "cancer")
    _legacy_prs(sample_engine, "traits")
    before = _rows(sample_engine)
    assert len(before) == 3
    with sample_engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TRIGGER reject_repair BEFORE UPDATE ON findings "
            "WHEN OLD.module = 'traits' BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
        )
    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(sample_engine)
    assert _rows(sample_engine) == before
    with sample_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == 35


def test_v36_locks_before_source_read_and_fingerprints_updates(sample_engine):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine)
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(" ".join(statement.split()).upper())

    sa.event.listen(sample_engine, "before_cursor_execute", record)
    try:
        assert ensure_sample_schema_current(sample_engine) is True
    finally:
        sa.event.remove(sample_engine, "before_cursor_execute", record)
    source_read = next(i for i, s in enumerate(statements) if s.startswith("SELECT FINDINGS.ID,"))
    update = next(i for i, s in enumerate(statements) if s.startswith("UPDATE FINDINGS SET"))
    assert statements.index("BEGIN IMMEDIATE") < source_read < update
    for column in ("ID", "MODULE", "CATEGORY", "DETAIL_JSON", "FINDING_TEXT", "PRS_PERCENTILE"):
        assert f"FINDINGS.{column} =" in statements[update].split("WHERE")[1]


def test_v36_restore_content_hook_and_custom_caveat(sample_engine):
    from backend.db.sample_schema import _add_missing_columns

    _ancestry(sample_engine)
    _legacy_prs(sample_engine, ancestry_warning_text="Custom reviewed caveat")
    assert _add_missing_columns(sample_engine, 35) is True
    row = _prs_rows(sample_engine)[0]
    assert row["prs_percentile"] is None
    assert json.loads(row["detail_json"])["ancestry_warning_text"] == "Custom reviewed caveat"
    assert _add_missing_columns(sample_engine, 35) is False
    assert _prs_rows(sample_engine) == [row]


def test_v36_does_not_rewrite_ambiguous_current_diff_identity(sample_engine):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine)
    old = _prs_rows(sample_engine)[0]
    other = {k: v for k, v in old.items() if k not in {"id", "created_at"}}
    detail = json.loads(other["detail_json"])
    detail["calibration_method"] = "static_reference"
    other["detail_json"] = json.dumps(detail)
    payload = {
        "added": [
            {
                "module": "cancer",
                "category": "prs",
                "trait": "synthetic_legacy",
                "finding_text": old["finding_text"],
            }
        ]
    }
    with sample_engine.begin() as conn:
        conn.execute(findings.insert().values(**other))
        conn.execute(
            annotation_state.insert().values(
                key="last_finding_diff_json", value=json.dumps(payload)
            )
        )
    assert ensure_sample_schema_current(sample_engine) is True
    rows = _prs_rows(sample_engine)
    assert len(rows) == 2
    assert [r["prs_percentile"] for r in rows] == [None, 90.09]
    with sample_engine.connect() as conn:
        assert conn.execute(sa.select(annotation_state.c.value)).scalar_one() == json.dumps(
            payload
        )


def test_v36_diff_write_failure_rolls_back_finding_repair(sample_engine):
    _ancestry(sample_engine)
    _legacy_prs(sample_engine)
    before = _rows(sample_engine)
    old = _prs_rows(sample_engine)[0]
    payload = json.dumps(
        {
            "added": [
                {
                    "module": "cancer",
                    "category": "prs",
                    "trait": "synthetic_legacy",
                    "finding_text": old["finding_text"],
                }
            ]
        }
    )
    with sample_engine.begin() as conn:
        conn.execute(annotation_state.insert().values(key="last_finding_diff_json", value=payload))
        conn.exec_driver_sql(
            "CREATE TRIGGER reject_diff BEFORE UPDATE ON annotation_state "
            "BEGIN SELECT RAISE(ABORT, 'blocked diff'); END"
        )
    with pytest.raises(sa.exc.IntegrityError, match="blocked diff"):
        ensure_sample_schema_current(sample_engine)
    assert _rows(sample_engine) == before
    with sample_engine.connect() as conn:
        assert conn.execute(sa.select(annotation_state.c.value)).scalar_one() == payload
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == 35


@pytest.mark.parametrize("uncertain", [True, False])
@pytest.mark.parametrize("first_reader", ["api", "report"])
def test_v36_first_open_repairs_api_and_export_without_rescoring(
    test_client,
    tmp_data_dir,
    uncertain,
    first_reader,
):
    from backend.db.connection import get_registry
    from backend.db.sample_schema import create_sample_tables
    from backend.db.tables import samples

    registry = get_registry()
    path = tmp_data_dir / "samples" / "sample_2474.db"
    with registry.reference_engine.begin() as conn:
        sample_id = conn.execute(
            samples.insert().values(
                name="Synthetic legacy PRS",
                db_path="samples/sample_2474.db",
                file_format="23andme_v5",
                file_hash="synthetic-2474",
            )
        ).lastrowid
    engine = sa.create_engine(f"sqlite:///{path}")
    create_sample_tables(engine)
    if uncertain:
        _ancestry(engine)
    else:
        _ancestry(
            engine, top_population="EUR", is_sufficient=True, classification_status="confident"
        )
    for module in MODULES:
        _legacy_prs(engine, module)
    before = _prs_rows(engine)
    assert len(before) == 5 and all(r["prs_percentile"] == 90.09 for r in before)
    engine.dispose()

    def report():
        response = test_client.post(
            "/api/reports/preview",
            json={
                "sample_id": sample_id,
                "modules": list(MODULES),
            },
        )
        assert response.status_code == 200, response.text
        return response.text

    if first_reader == "report":
        assert report().count("Synthetic legacy PRS:") == 5
    expected_percentile = None if uncertain else 90.09
    routes = {
        "cancer": ("prs", "items"),
        "metabolic": ("prs", "items"),
        "traits": ("prs", "items"),
        "ebmd": ("prs", "prs"),
        "fh": ("assessment", "ldl_prs"),
    }
    for module, (endpoint, field) in routes.items():
        response = test_client.get(
            f"/api/analysis/{module}/{endpoint}", params={"sample_id": sample_id}
        )
        assert response.status_code == 200, response.text
        body = response.json()[field]
        if field == "items":
            assert len(body) == 1
            body = body[0]
        assert body is not None
        assert body["percentile"] == expected_percentile
        assert body["calibrated"] is (not uncertain)
        assert body["coverage_fraction"] == 1.0
        if "z_score" in body:
            assert body["z_score"] == (None if uncertain else 1.29)
    # This loader and rendered HTML are the production PDF export's input,
    # preserving the existing raw-SQL findings quarantine below.
    from backend.reports.generator import _load_findings

    sample_engine = registry.get_sample_engine(path)
    exported = _load_findings(sample_engine, list(MODULES))
    stored = {row["module"]: row for row in _prs_rows(sample_engine)}
    assert len(exported) == 5 and {r["module"] for r in exported} == set(MODULES)
    for row in exported:
        detail = json.loads(stored[row["module"]]["detail_json"])
        assert row["prs_score"] == 1.5
        assert row["prs_percentile"] == detail["percentile"] == expected_percentile
        assert detail["calibrated"] is (not uncertain)
        assert detail["z_score"] == (None if uncertain else 1.29)
        assert ("90th percentile" in row["finding_text"]) is (not uncertain)
    html = report()
    assert html.count("Synthetic legacy PRS:") == 5
    assert html.count("90th percentile") == (0 if uncertain else 5)
    assert html.count("population percentile not reported") == (5 if uncertain else 0)
    guarded = test_client.post(
        "/api/export/sql",
        json={
            "sample_id": sample_id,
            "format": "json",
            "sql": "SELECT * FROM findings",
        },
    )
    assert guarded.status_code == 403
    assert "audit-only clinical finding data" in guarded.json()["detail"]
    response = test_client.get("/api/analysis/findings", params={"sample_id": sample_id})
    assert response.status_code == 200, response.text
    visible = [r for r in response.json() if r["category"] == "prs"]
    assert len(visible) == 5
    assert {r["module"]: r["finding_text"] for r in visible} == {
        r["module"]: r["finding_text"] for r in exported
    }
    with sa.create_engine(f"sqlite:///{path}").connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == 36
