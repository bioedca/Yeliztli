"""Narrow v36 repair of PRS rows produced before the ancestry gate (#2474).

This migration applies the existing scorer's withholding policy. It neither
recomputes a score nor establishes scientific validity for a reference model.
"""

import json
import math
from collections.abc import Mapping

import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    _parse_unambiguous_finding_payload,
    is_patient_presentable_finding_payload,
)
from backend.db.tables import annotation_state, findings
from backend.services.lai_production_coverage import policy_qualified_finding_clause

_MODULES = frozenset({"cancer", "metabolic", "ebmd", "fh", "traits"})
_DIFF_KEY = "last_finding_diff_json"
_IDENTITY = ("module", "category", "gene_symbol", "rsid", "drug", "diplotype", "pathway")
# Frozen released producer wording, deliberately independent of future changes
# to the scorer's prose. A custom finding is not a known migration fingerprint.
_PROTECTIVE_NOTE = (
    "Higher percentiles are protective; lower percentiles indicate higher risk context."
)
_CLEAR_FIELDS = (
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
)


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _normalized_fractions(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict) or not value:
        return None
    if any(
        not isinstance(k, str) or not k or not _finite_number(v) or v < 0 for k, v in value.items()
    ):
        return None
    total = sum(v for v in value.values() if v > 0)
    if not _finite_number(total) or total <= 0:
        return None
    # Match the historical scorer's exact positive-value normalization; do not
    # round or accept an approximately matching source as proof of identity.
    return {k: v / total for k, v in value.items() if v > 0}


def _uncertain_source_fractions(rows: list[Mapping]) -> dict[str, float] | None:
    # Same category preference and descending-ID order as the scorer. Once a
    # presentable row names a population, lower-priority rows cannot override it.
    for category in ("nnls_admixture", "pca_projection", None):
        for row in rows:
            if category is not None and row["category"] != category:
                continue
            if not is_patient_presentable_finding_payload(row):
                continue
            detail = _parse_unambiguous_finding_payload(row["detail_json"])
            if detail is None:
                continue
            top = detail.get("top_population") or detail.get("inferred_ancestry")
            if not isinstance(top, str) or not top:
                continue
            if not (
                top == "UNCERTAIN"
                or detail.get("is_sufficient") is False
                or detail.get("classification_status") == "uncertain"
            ):
                return None
            return _normalized_fractions(
                detail.get("nnls_fractions") or detail.get("admixture_fractions")
            )
    return None


def _repair(row: Mapping, fractions: dict[str, float]) -> tuple[str, dict] | None:
    detail = _parse_unambiguous_finding_payload(row["detail_json"])
    if detail is None or not is_patient_presentable_finding_payload(row):
        return None
    if (
        detail.get("calibrated") is not True
        or detail.get("is_sufficient") is not True
        or detail.get("calibration_method") != "ancestry_continuous"
    ):
        return None
    used = detail.get("calibration_ancestry_fractions")
    if (
        not isinstance(used, dict)
        or used != fractions
        or any(not _finite_number(v) or v <= 0 for v in used.values())
    ):
        return None
    percentile, z_score = detail.get("percentile"), detail.get("z_score")
    if (
        not _finite_number(percentile)
        or not 0 <= percentile <= 100
        or not _finite_number(row["prs_percentile"])
        or row["prs_percentile"] != percentile
        or not _finite_number(z_score)
        or not _finite_number(row["prs_score"])
    ):
        return None
    name, trait = detail.get("name"), detail.get("trait")
    orientation = detail.get("higher_is", "risk")
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(trait, str)
        or not trait.strip()
        or orientation not in ("risk", "protective")
    ):
        return None
    suffix = f" — {_PROTECTIVE_NOTE}" if orientation == "protective" else ""
    expected = (
        f"{name}: {percentile:.0f}th percentile [z = {z_score:.2f}]{suffix} — Research Use Only"
    )
    if row["finding_text"] != expected:
        return None
    clause = f"{_PROTECTIVE_NOTE} — " if orientation == "protective" else ""
    text = (
        f"{name}: population percentile not reported — score lacks a validated "
        f"reference distribution (uncalibrated) — {clause}Research Use Only"
    )
    detail.update({key: None for key in _CLEAR_FIELDS})
    detail.update(calibrated=False, bootstrap_iterations=0, calibration_pmids=[])
    # Reuse the exact released-caveat recognizer, after changing the reporting
    # state; custom caveats and all unrelated metadata remain untouched.
    from backend.db.sample_schema import _withheld_prs_warning_without_percentile_guidance

    warning = _withheld_prs_warning_without_percentile_guidance(detail)
    if warning is not None:
        detail["ancestry_warning_text"] = warning
    return text, detail


def _text_identity(entry: Mapping, trait: object) -> tuple | None:
    values = [entry.get(k) for k in _IDENTITY] + [trait, entry.get("finding_text")]
    if not all(v is None or isinstance(v, str) for v in values):
        return None
    if not isinstance(trait, str) or not trait or not isinstance(values[-1], str):
        return None
    return tuple(values)


def _repair_diff(conn: sa.Connection, text_repairs: dict[tuple, str | None]) -> int:
    row = conn.execute(
        sa.select(annotation_state.c.value).where(annotation_state.c.key == _DIFF_KEY)
    ).first()
    if row is None:
        return 0
    diff = _parse_unambiguous_finding_payload(row.value)
    if diff is None:
        return 0
    count = 0
    # Removed entries are historical rows, with no current calibration record
    # to prove their source. Only current added/changed entries can be repaired.
    for bucket in ("added", "changed"):
        entries = diff.get(bucket)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = _text_identity(entry, entry.get("trait"))
            text = text_repairs.get(key)
            if text is not None:
                entry["finding_text"] = text
                count += 1
    if count:
        conn.execute(
            annotation_state.update()
            .where(
                annotation_state.c.key == _DIFF_KEY,
                annotation_state.c.value == row.value,
            )
            .values(value=json.dumps(diff))
        )
    return count


def repair_uncertain_ancestry_prs(engine: sa.Engine) -> tuple[int, int]:
    """Atomically repair identifiable current findings and matching diff text."""
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    columns = (
        {c["name"] for c in inspector.get_columns("findings")} if "findings" in tables else set()
    )
    required = {
        "id",
        "module",
        "category",
        "detail_json",
        "finding_text",
        "prs_score",
        "prs_percentile",
    }
    if not required <= columns:
        return 0, 0
    state_columns = (
        {c["name"] for c in inspector.get_columns("annotation_state")}
        if "annotation_state" in tables
        else set()
    )
    # Partial legacy fixtures need only their actual columns; full production
    # rows carry all fields through the existing presentation-policy check.
    selected = [c for c in findings.c if c.name in columns]
    repaired = 0
    diff_count = 0
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            ancestry = list(
                conn.execute(
                    sa.select(*selected)
                    .where(
                        findings.c.module == "ancestry",
                        policy_qualified_finding_clause(findings.c.category),
                    )
                    .order_by(findings.c.id.desc())
                ).mappings()
            )
            fractions = _uncertain_source_fractions(ancestry)
            if fractions is not None:
                candidates = (
                    conn.execute(
                        sa.select(*selected)
                        .where(
                            findings.c.module.in_(_MODULES),
                            findings.c.category == "prs",
                        )
                        .order_by(findings.c.id)
                    )
                    .mappings()
                    .all()
                )
                text_repairs: dict[tuple, str | None] = {}
                for row in candidates:
                    repair = _repair(row, fractions)
                    detail = _parse_unambiguous_finding_payload(row["detail_json"])
                    key = _text_identity(row, detail.get("trait") if detail else None)
                    text = repair[0] if repair else None
                    if key is not None:
                        text_repairs[key] = text if text_repairs.get(key, text) == text else None
                    if repair is None:
                        continue
                    text, detail = repair
                    result = conn.execute(
                        findings.update()
                        .where(
                            findings.c.id == row["id"],
                            findings.c.module == row["module"],
                            findings.c.category == "prs",
                            findings.c.detail_json == row["detail_json"],
                            findings.c.finding_text == row["finding_text"],
                            findings.c.prs_percentile == row["prs_percentile"],
                        )
                        .values(
                            prs_percentile=None, finding_text=text, detail_json=json.dumps(detail)
                        )
                    )
                    if result.rowcount != 1:
                        raise RuntimeError("Legacy PRS repair fingerprint changed")
                    repaired += 1
                if repaired and {"key", "value"} <= state_columns:
                    diff_count = _repair_diff(conn, text_repairs)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return repaired, diff_count
