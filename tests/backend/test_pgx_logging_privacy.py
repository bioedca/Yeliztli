"""Synthetic regression coverage for structured PGx diagnostics."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
import structlog

from backend.analysis import pharmacogenomics as pgx
from backend.db.tables import (
    LOG_ENTRY_PRESENTATION_POLICY_VERSION,
    log_entries,
    reference_metadata,
)
from backend.logging_config import (
    _REDACTED_LOG_VALUE,
    _redact_sensitive_log_fields,
    configure_logging,
)

PRIVATE_FIELDS = {
    "phenotype": "synthetic-private-phenotype",
    "involved_rsids": ["synthetic-carried-marker"],
    "indeterminate_alleles": ["synthetic-unresolved-allele"],
    "confidence_note": "synthetic-private-confidence-note",
    "recommendation": "synthetic-private-guidance",
}
SAFE_FIELDS = {
    "call_confidence": "synthetic-confidence-category",
    "involved_variant_count": 1,
    "indeterminate_allele_count": 1,
    "phenotype_count": 2,
    "cpic_rsids": 7,
}


@pytest.mark.parametrize(
    "key",
    [
        "phenotype",
        "PHENOTYPE",
        " phenotype ",
        "phenotypes",
        "called_phenotype",
        "covered_phenotypes",
        "involved_rsids",
        "called_involved_rsids",
        "indeterminate_alleles",
        "possible_indeterminate_alleles",
        "confidence_note",
        "result_confidence_note",
        "recommendation",
        "recommendations",
        "source_recommendation",
    ],
)
def test_sensitive_result_fields_are_redacted_in_nested_containers(key: str) -> None:
    private = "synthetic-private-keyed-value"
    original = {key: private, **SAFE_FIELDS}
    event = {
        "event": "synthetic_privacy_event",
        **original,
        "mapping": deepcopy(original),
        "sequence": [deepcopy(original)],
        "tuple": (deepcopy(original),),
    }

    redacted = _redact_sensitive_log_fields(None, "info", event)

    expected = {key: _REDACTED_LOG_VALUE, **SAFE_FIELDS}
    assert redacted == {
        "event": "synthetic_privacy_event",
        **expected,
        "mapping": expected,
        "sequence": [expected],
        "tuple": (expected,),
    }
    assert original[key] == private


@pytest.mark.parametrize("path", ["native", "stdlib"])
def test_configured_log_sinks_keep_only_safe_structured_values(tmp_path, capsys, path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(engine)
    original = {**deepcopy(PRIVATE_FIELDS), **SAFE_FIELDS}
    payload = {
        **deepcopy(original),
        "nested": {"rows": [deepcopy(original), (deepcopy(original),)]},
    }
    event = f"synthetic_privacy_{path}"
    try:
        configure_logging(engine_getter=lambda: engine)
        if path == "native":
            structlog.get_logger("backend.synthetic_privacy").info(event, **payload)
        else:
            logging.getLogger("backend.synthetic_privacy").info(event, extra=payload)

        captured = capsys.readouterr()
        console = captured.out + captured.err
        with engine.connect() as conn:
            row = (
                conn.execute(sa.select(log_entries).where(log_entries.c.message == event))
                .mappings()
                .one()
            )

        expected = {**dict.fromkeys(PRIVATE_FIELDS, _REDACTED_LOG_VALUE), **SAFE_FIELDS}
        assert json.loads(row["event_data"]) == {
            **expected,
            "nested": {"rows": [expected, [expected]]},
        }
        assert row["presentation_policy_version"] == LOG_ENTRY_PRESENTATION_POLICY_VERSION
        assert row["logger"] == "backend.synthetic_privacy"
        assert row["level"] == "INFO"
        assert event in console and _REDACTED_LOG_VALUE in console
        assert "involved_variant_count" in console and "cpic_rsids" in console
        sentinels = [v if isinstance(v, str) else v[0] for v in PRIVATE_FIELDS.values()]
        assert sentinels
        for value in sentinels:
            assert value not in row["event_data"]
            assert value not in console
        assert payload["phenotype"] == original["phenotype"]
    finally:
        engine.dispose()


def _result() -> pgx.StarAlleleResult:
    return pgx.StarAlleleResult(
        gene="SYNTHETIC_GENE",
        allele1="synthetic-private-allele-one",
        allele2="synthetic-private-allele-two",
        diplotype="synthetic-private-diplotype",
        phenotype=PRIVATE_FIELDS["phenotype"],
        involved_rsids=set(PRIVATE_FIELDS["involved_rsids"]),
        missing_rsids={"synthetic-missing-marker"},
        confidence_note=PRIVATE_FIELDS["confidence_note"],
        indeterminate_alleles=PRIVATE_FIELDS["indeterminate_alleles"].copy(),
    )


def test_star_allele_producer_logs_counts_without_changing_results(monkeypatch) -> None:
    result = _result()
    log = Mock()
    monkeypatch.setattr(pgx, "logger", log)
    monkeypatch.setattr(
        pgx,
        "_fetch_alleles_for_gene",
        lambda *_: [{"defining_variants": [{"rsid": "synthetic-input-marker"}]}],
    )
    monkeypatch.setattr(pgx, "_fetch_sample_genotypes", lambda *_: {})
    monkeypatch.setattr(pgx, "call_star_alleles_for_gene", lambda *_: result)

    returned = pgx.call_all_star_alleles(None, None, genes=frozenset({result.gene}))

    assert returned == [result]
    assert returned[0].phenotype == PRIVATE_FIELDS["phenotype"]
    assert returned[0].involved_rsids == set(PRIVATE_FIELDS["involved_rsids"])
    log.info.assert_called_once_with(
        "pgx_star_allele_called",
        gene=result.gene,
        call_confidence=result.call_confidence.value,
        involved_variant_count=1,
        missing_variant_count=1,
    )


@pytest.mark.parametrize(
    ("case", "event", "level"),
    [
        ("no_phenotype", "pgx_alert_skipped_no_phenotype", "info"),
        ("insufficient", "pgx_alert_skipped_insufficient", "info"),
        ("covered", "pgx_phenotype_no_guideline_row", "warning"),
        ("uncovered", "pgx_no_guidelines", "debug"),
        ("direction", "pgx_alert_withheld_undetermined_direction", "info"),
        ("prescribing", "pgx_prescribing_alert", "info"),
    ],
)
def test_prescribing_producers_keep_diagnostics_without_result_values(
    monkeypatch, case, event, level
) -> None:
    result = _result()
    if case == "no_phenotype":
        result.phenotype = None
    if case == "insufficient":
        result.call_confidence = pgx.CallConfidence.INSUFFICIENT
    guideline = {
        "drug": "SYNTHETIC_DRUG",
        "recommendation": PRIVATE_FIELDS["recommendation"],
        "classification": "synthetic-classification",
        "guideline_url": "https://example.invalid/synthetic-guideline",
    }
    log = Mock()
    monkeypatch.setattr(pgx, "logger", log)
    monkeypatch.setattr(pgx, "_infer_conservative_phenotype", lambda *_: None)
    monkeypatch.setattr(
        pgx,
        "_fetch_guidelines_for_gene_phenotype",
        lambda *_, **__: [] if case in {"covered", "uncovered"} else [guideline],
    )
    monkeypatch.setattr(
        pgx,
        "_fetch_guideline_phenotypes_for_gene",
        lambda *_: (
            {"synthetic-covered-one", "synthetic-covered-two"} if case == "covered" else set()
        ),
    )
    monkeypatch.setattr(pgx, "WITHHOLD_CROSS_DIRECTION_GENES", frozenset({result.gene}))
    monkeypatch.setattr(
        pgx, "_untyped_marker_spans_conflicting_recommendations", lambda *_: case == "direction"
    )
    monkeypatch.setattr(pgx, "assign_cpic_evidence_level", lambda *_: 1)

    alerts = pgx.generate_prescribing_alerts([result], None)

    expected = {"gene": result.gene}
    if case == "covered":
        expected["covered_phenotype_count"] = 2
    if case in {"insufficient", "direction"}:
        expected["call_confidence"] = result.call_confidence.value
        expected["indeterminate_allele_count"] = 1
    if case == "direction":
        expected["drug"] = guideline["drug"]
    if case == "prescribing":
        expected.update(
            drug=guideline["drug"],
            classification=guideline["classification"],
            call_confidence=result.call_confidence.value,
            evidence_level=1,
            conservative_alert=False,
        )
        assert len(alerts) == 1
        assert alerts[0].phenotype == result.phenotype
        assert alerts[0].recommendation == PRIVATE_FIELDS["recommendation"]
        assert alerts[0].confidence_note == result.confidence_note
        assert alerts[0].involved_rsids == sorted(result.involved_rsids)
    else:
        assert alerts == []
    getattr(log, level).assert_called_once_with(event, **expected)
