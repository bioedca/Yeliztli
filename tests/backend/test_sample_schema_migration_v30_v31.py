"""Tests for the v30 -> v31 persisted traits pathway-summary repair (#2207).

Before #2206 a traits pathway summary that held an Indeterminate call (a
strand-unresolved homozygote withheld from interpretation) was persisted as the
clean negative "Standard (no variants of note)". Raw ``finding_text`` consumers and
the report badge, which reads the then-absent ``indeterminate_snps`` key, kept
serving that to every sample analysed before the fix.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.pathway_coverage import (
    coverage_detail,
    pathway_level_display_label,
    pathway_summary_text,
)
from backend.analysis.traits import (
    INDETERMINATE,
    STANDARD,
    PathwayResult,
    SNPResult,
    TraitsResult,
    store_traits_findings,
)
from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    _repaired_traits_indeterminate_summary,
    create_sample_tables,
    ensure_sample_schema_current,
)
from backend.db.tables import annotation_state, findings

PATHWAY = "Behavioral Traits"
COMPLETE = f"{PATHWAY} — Standard (no variants of note)"
LIMITED_PHRASE = "No variants of note among tested SNPs"
QUALIFIED = (
    f"{PATHWAY} — Standard for scored variants; 1 variant observed but not "
    "interpreted (indeterminate) — see SNP details"
)
NOTHING_SCORED = (
    f"{PATHWAY} — not assessed: 1 variant observed but not interpreted "
    "(indeterminate), no variant scored — see SNP details"
)
DIFF_KEY = "last_finding_diff_json"


def _snp(rsid: str, category: str, *, present: bool = True, status: str = "called") -> SNPResult:
    return SNPResult(
        rsid=rsid,
        gene="DRD4" if rsid == "rs747302" else "CADM2",
        variant_name="DRD4 exon III VNTR proxy" if rsid == "rs747302" else "CADM2 proxy",
        genotype=("CC" if rsid == "rs747302" else "TT") if present else None,
        category=category,
        effect_summary="Strand-ambiguous homozygote." if category == INDETERMINATE else "None.",
        evidence_level=1,
        pmids=["30643258"],
        recommendation_text="",
        present_in_sample=present,
        coverage_status=status,
    )


def _pathway(*, interpreted: bool, missing: int) -> PathwayResult:
    """A Standard behavioural pathway holding one Indeterminate DRD4 call."""
    snps = [_snp("rs747302", INDETERMINATE)]
    if interpreted:
        snps.insert(0, _snp("rs993137", STANDARD))
    statuses = ["not_on_array", "no_call", "not_on_array"]
    snps += [
        _snp(f"rs10000{i}", STANDARD, present=False, status=statuses[i]) for i in range(missing)
    ]
    return PathwayResult(
        pathway_id="behavioral_traits", pathway_name=PATHWAY, level=STANDARD, snp_results=snps
    )


def _summary_row(conn: sa.Connection) -> dict[str, object]:
    return dict(
        conn.execute(sa.select(findings).where(findings.c.category == "pathway_summary"))
        .mappings()
        .one()
    )


def _legacy_detail(pr: PathwayResult, current: dict[str, object]) -> tuple[str, dict]:
    """What the pre-#2206 producer stored for ``pr``: no indeterminate count anywhere."""
    called = len(pr.called_snps)
    text = pathway_summary_text(
        pathway_name=pr.pathway_name,
        level=pr.level,
        called_count=called,
        missing_snps=pr.missing_snps,
        standard_complete_phrase="Standard (no variants of note)",
        standard_limited_phrase=LIMITED_PHRASE,
    )
    coverage = coverage_detail(
        level=pr.level,
        called_count=called,
        missing_snps=pr.missing_snps,
        standard_limited_phrase=LIMITED_PHRASE,
    )
    detail = {k: v for k, v in current.items() if k not in ("indeterminate_snps",)}
    detail.pop("coverage_interpretation", None)
    detail.update(coverage)
    return text, detail


def _stored_legacy(engine: sa.Engine, pr: PathwayResult) -> tuple[dict, dict]:
    """Store ``pr`` with today's producer, then rewrite it to its pre-#2206 form.

    Returns ``(producer_row, legacy_row)`` so the repair can be compared with what
    the producer itself writes for the same scoring state.
    """
    store_traits_findings(TraitsResult(pathway_results=[pr]), engine)
    with engine.begin() as conn:
        producer = _summary_row(conn)
        legacy_text, legacy_detail = _legacy_detail(pr, json.loads(producer["detail_json"]))
        conn.execute(
            findings.update()
            .where(findings.c.id == producer["id"])
            .values(finding_text=legacy_text, detail_json=json.dumps(legacy_detail))
        )
        legacy = _summary_row(conn)
        conn.execute(sa.text("PRAGMA user_version = 30"))
    return producer, legacy


def _rows(conn: sa.Connection) -> list[dict[str, object]]:
    return [
        dict(row) for row in conn.execute(sa.select(findings).order_by(findings.c.id)).mappings()
    ]


@pytest.mark.parametrize(
    ("interpreted", "missing"),
    [(True, 0), (False, 0), (True, 1), (True, 3), (False, 2)],
    ids=[
        "scored-complete",
        "nothing-scored-complete",
        "scored-1-missing",
        "scored-3-missing",
        "nothing-scored-2-missing",
    ],
)
def test_v31_rewrites_legacy_rows_to_what_the_producer_now_stores(
    interpreted: bool, missing: int
) -> None:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    pr = _pathway(interpreted=interpreted, missing=missing)
    producer, legacy = _stored_legacy(engine, pr)
    # Premise: the fixture really is the clean-negative legacy shape, and differs
    # from what the producer writes now, so the comparison below can fail.
    assert "no variants of note" in legacy["finding_text"].lower()
    assert "indeterminate_snps" not in json.loads(legacy["detail_json"])
    assert legacy["finding_text"] != producer["finding_text"]

    assert ensure_sample_schema_current(engine) is True

    with engine.connect() as conn:
        repaired = _summary_row(conn)
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()
    assert version == SAMPLE_SCHEMA_VERSION == 32
    assert repaired["finding_text"] == producer["finding_text"]
    assert json.loads(repaired["detail_json"]) == json.loads(producer["detail_json"])
    # No other column moves.
    assert {**repaired, "finding_text": None, "detail_json": None} == {
        **legacy,
        "finding_text": None,
        "detail_json": None,
    }


def test_v31_fixes_the_report_badge_for_a_pathway_where_nothing_was_scored() -> None:
    """The report/export badge reads ``indeterminate_snps``; legacy rows lacked it."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    _producer, legacy = _stored_legacy(engine, _pathway(interpreted=False, missing=0))
    assert pathway_level_display_label("Standard", json.loads(legacy["detail_json"])) == "Standard"

    ensure_sample_schema_current(engine)

    with engine.connect() as conn:
        repaired = _summary_row(conn)
    assert repaired["finding_text"] == NOTHING_SCORED
    detail = json.loads(repaired["detail_json"])
    assert pathway_level_display_label("Standard", detail) == "Not Assessed"


def test_v31_leaves_a_genuine_clean_negative_alone() -> None:
    """Discriminating control: the same pathway with a truly Standard DRD4 call.

    Its stored text is the very sentence the repair targets, so only the
    Indeterminate evidence in ``snp_details`` can tell the two apart.
    """
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    pr = PathwayResult(
        pathway_id="behavioral_traits",
        pathway_name=PATHWAY,
        level=STANDARD,
        snp_results=[_snp("rs993137", STANDARD), _snp("rs747302", STANDARD)],
    )
    store_traits_findings(TraitsResult(pathway_results=[pr]), engine)
    with engine.begin() as conn:
        row = _summary_row(conn)
        # Drop the key as a pre-#2206 row would, so only snp_details discriminates.
        detail = json.loads(row["detail_json"])
        detail.pop("indeterminate_snps")
        conn.execute(
            findings.update()
            .where(findings.c.id == row["id"])
            .values(detail_json=json.dumps(detail))
        )
        before = _rows(conn)
        conn.execute(sa.text("PRAGMA user_version = 30"))
    assert before[0]["finding_text"] == COMPLETE

    assert ensure_sample_schema_current(engine) is False
    with engine.connect() as conn:
        assert _rows(conn) == before


def _legacy_detail_dict(
    *,
    categories: tuple[str, ...] = (STANDARD, INDETERMINATE),
    missing: list[str] | None = None,
    interpretation: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    detail: dict[str, object] = {
        "pathway_id": "behavioral_traits",
        "called_snps": len(categories),
        "total_snps": len(categories) + len(missing or []),
        "prs_primary": False,
        "missing_snps": list(missing or []),
        "no_call_snps": [],
        "off_chip_snps": list(missing or []),
        "snp_details": [
            {"rsid": f"rs{900 + i}", "category": category} for i, category in enumerate(categories)
        ],
    }
    if interpretation is not None:
        detail["coverage_interpretation"] = interpretation
    detail.update(overrides)
    return detail


LIMITED_TEXT = f"{PATHWAY} — {LIMITED_PHRASE}; 2 tracked SNPs (2 off-chip) not assessed."
LIMITED_INTERPRETATION = f"{LIMITED_PHRASE}; 2 tracked SNPs (2 off-chip) not assessed."


@pytest.mark.parametrize(
    ("pathway", "text", "detail"),
    [
        # The producer had already qualified it: the key is present.
        (PATHWAY, COMPLETE, _legacy_detail_dict(indeterminate_snps=[])),
        # Nothing was withheld.
        (PATHWAY, COMPLETE, _legacy_detail_dict(categories=(STANDARD, STANDARD))),
        # Text that is not the producer's exact sentence.
        (PATHWAY, COMPLETE + " Reviewed.", _legacy_detail_dict()),
        (PATHWAY, "Other Pathway — Standard (no variants of note)", _legacy_detail_dict()),
        (PATHWAY, f"{PATHWAY} — Standard (no variants of concern)", _legacy_detail_dict()),
        # The complete-coverage sentence on a row that recorded missing SNPs.
        (PATHWAY, COMPLETE, _legacy_detail_dict(missing=["rs1", "rs2"])),
        # The limited sentence on a row that recorded none missing.
        (PATHWAY, LIMITED_TEXT, _legacy_detail_dict(interpretation=LIMITED_INTERPRETATION)),
        # A missing count that disagrees with the stored list.
        (
            PATHWAY,
            LIMITED_TEXT,
            _legacy_detail_dict(missing=["rs1"], interpretation=LIMITED_INTERPRETATION),
        ),
        # A stored coverage interpretation that is not the one the text was built from.
        (
            PATHWAY,
            LIMITED_TEXT,
            _legacy_detail_dict(missing=["rs1", "rs2"], interpretation="Hand-edited."),
        ),
        (PATHWAY, LIMITED_TEXT, _legacy_detail_dict(missing=["rs1", "rs2"])),
        # A called count that disagrees with the per-SNP detail.
        (PATHWAY, COMPLETE, _legacy_detail_dict(called_snps=5)),
        (PATHWAY, COMPLETE, _legacy_detail_dict(called_snps="2")),
        # Malformed or absent structure.
        (PATHWAY, COMPLETE, _legacy_detail_dict(snp_details="rs900")),
        (PATHWAY, COMPLETE, _legacy_detail_dict(missing_snps=None)),
        (
            PATHWAY,
            COMPLETE,
            _legacy_detail_dict(snp_details=[{"rsid": None, "category": INDETERMINATE}]),
        ),
        (PATHWAY, COMPLETE, ["not", "a", "dict"]),
        (None, COMPLETE, _legacy_detail_dict()),
        (PATHWAY, None, _legacy_detail_dict()),
    ],
)
def test_helper_repairs_only_the_exact_legacy_fingerprint(
    pathway: object, text: object, detail: object
) -> None:
    assert _repaired_traits_indeterminate_summary(pathway, text, detail) is None


def test_helper_rewrites_the_limited_coverage_shape() -> None:
    detail = _legacy_detail_dict(missing=["rs1", "rs2"], interpretation=LIMITED_INTERPRETATION)
    repaired = _repaired_traits_indeterminate_summary(PATHWAY, LIMITED_TEXT, detail)

    assert repaired is not None
    text, new_detail = repaired
    assert text == f"{QUALIFIED}; 2 tracked SNPs (2 off-chip) not assessed"
    assert new_detail["indeterminate_snps"] == ["rs901"]
    assert new_detail["coverage_interpretation"] == (
        "Standard result is based on interpreted SNPs only; "
        "2 tracked SNPs (2 off-chip) not assessed."
    )
    # The input is not mutated, and nothing else in the detail changes.
    assert "indeterminate_snps" not in detail
    assert {**new_detail, "indeterminate_snps": None, "coverage_interpretation": None} == {
        **detail,
        "indeterminate_snps": None,
        "coverage_interpretation": None,
    }


def _diff(changed: list, added: list, removed: list) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "before_releases": {},
            "after_releases": {},
            "release_deltas": [],
            "changed": changed,
            "added": added,
            "removed": removed,
            "counts": {"changed": len(changed), "added": len(added), "removed": len(removed)},
        }
    )


def _diff_entry(text: str, *, module: str = "traits", pathway: str = PATHWAY) -> dict:
    return {
        "module": module,
        "category": "pathway_summary",
        "gene_symbol": None,
        "rsid": None,
        "drug": None,
        "diplotype": None,
        "pathway": pathway,
        "trait": None,
        "finding_text": text,
        "clinvar_significance": None,
        "evidence_level": 1,
        "metabolizer_status": None,
        "pathway_level": "Standard",
    }


def test_v31_repairs_the_banner_only_where_a_repaired_row_speaks_for_it() -> None:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    producer, legacy = _stored_legacy(engine, _pathway(interpreted=True, missing=0))
    changed = [{**_diff_entry(COMPLETE), "changes": [{"field": "pathway_level"}]}]
    added = [
        _diff_entry(COMPLETE),
        _diff_entry(COMPLETE, module="fitness"),
        _diff_entry("Other Pathway — Standard (no variants of note)", pathway="Other Pathway"),
    ]
    # A removed entry describes a prior-run row; no current row speaks for it.
    removed = [_diff_entry(COMPLETE)]
    with engine.begin() as conn:
        conn.execute(
            annotation_state.insert().values(key=DIFF_KEY, value=_diff(changed, added, removed))
        )

    assert ensure_sample_schema_current(engine) is True

    with engine.connect() as conn:
        diff = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(annotation_state.c.key == DIFF_KEY)
            ).scalar_one()
        )
    assert diff["changed"][0]["finding_text"] == producer["finding_text"]
    assert diff["changed"][0]["changes"] == [{"field": "pathway_level"}]
    assert [entry["finding_text"] for entry in diff["added"]] == [
        producer["finding_text"],
        COMPLETE,
        "Other Pathway — Standard (no variants of note)",
    ]
    assert diff["removed"][0]["finding_text"] == COMPLETE
    assert diff["counts"] == {"changed": 1, "added": 3, "removed": 1}
    assert legacy["finding_text"] == COMPLETE


def test_v31_leaves_other_modules_and_levels_untouched(sample_engine: sa.Engine) -> None:
    base = {
        "evidence_level": 1,
        "gene_symbol": None,
        "rsid": None,
        "pmid_citations": "[]",
        "category": "pathway_summary",
        "pathway": PATHWAY,
        "finding_text": COMPLETE,
        "detail_json": json.dumps(_legacy_detail_dict()),
    }
    rows = [
        {**base, "id": 1, "module": "fitness", "pathway_level": "Standard"},
        {**base, "id": 2, "module": "traits", "pathway_level": "Moderate"},
        {**base, "id": 3, "module": "traits", "pathway_level": "Standard", "category": "snp"},
        {**base, "id": 4, "module": "traits", "pathway_level": "Standard", "detail_json": "{oops"},
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)
        before = _rows(conn)
        conn.execute(sa.text("PRAGMA user_version = 30"))

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        assert _rows(conn) == before
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == SAMPLE_SCHEMA_VERSION


def test_v31_skips_a_findings_table_without_the_pathway_columns() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE findings (id INTEGER PRIMARY KEY, module TEXT, category TEXT, "
                "finding_text TEXT, detail_json TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO findings (id, module, category, finding_text, detail_json) "
                "VALUES (1, 'traits', 'pathway_summary', :t, :d)"
            ),
            {"t": COMPLETE, "d": json.dumps(_legacy_detail_dict())},
        )
        conn.execute(sa.text("PRAGMA user_version = 30"))

    ensure_sample_schema_current(engine)

    with engine.connect() as conn:
        text = conn.execute(sa.text("SELECT finding_text FROM findings")).scalar_one()
    assert text == COMPLETE


def test_v31_locks_before_reading_or_writing_findings() -> None:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    _stored_legacy(engine, _pathway(interpreted=True, missing=0))

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.split()).upper())

    sa.event.listen(engine, "before_cursor_execute", record_statement)
    try:
        assert ensure_sample_schema_current(engine) is True
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_statement)

    begin_index = statements.index("BEGIN IMMEDIATE")
    candidate_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT FINDINGS.ID, FINDINGS.PATHWAY")
    )
    update_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("UPDATE FINDINGS SET")
    )
    assert begin_index < candidate_index < update_index


def test_v31_rolls_back_every_repair_when_one_update_fails() -> None:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    pathways = [
        _pathway(interpreted=True, missing=0),
        PathwayResult(
            pathway_id="sensory_traits",
            pathway_name="Sensory Traits",
            level=STANDARD,
            snp_results=[_snp("rs747302", INDETERMINATE)],
        ),
    ]
    store_traits_findings(TraitsResult(pathway_results=pathways), engine)
    with engine.begin() as conn:
        summaries = conn.execute(
            sa.select(findings.c.id, findings.c.pathway, findings.c.detail_json)
            .where(findings.c.category == "pathway_summary")
            .order_by(findings.c.id)
        ).fetchall()
        for row in summaries:
            detail = json.loads(row.detail_json)
            detail.pop("indeterminate_snps")
            conn.execute(
                findings.update()
                .where(findings.c.id == row.id)
                .values(
                    finding_text=f"{row.pathway} — Standard (no variants of note)",
                    detail_json=json.dumps(detail),
                )
            )
        before = _rows(conn)
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_second_summary_repair "
                "BEFORE UPDATE OF finding_text ON findings "
                f"WHEN OLD.id = {summaries[1].id} "
                "BEGIN SELECT RAISE(ABORT, 'blocked repair'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 30"))

    with pytest.raises(sa.exc.IntegrityError, match="blocked repair"):
        ensure_sample_schema_current(engine)

    with engine.connect() as conn:
        assert _rows(conn) == before
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == 30
