"""Validate production LAI coverage telemetry for calibration consumers.

The calibration harness and downstream policy tooling must agree on one strict
interpretation of the production ``lai_coverage_metrics`` schema.  This module
keeps that validation independent of the heavyweight calibration harness so
later consumers can validate authenticated observation records without loading
database, NumPy, or inference dependencies.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

AUTOSOMES = tuple(str(chromosome) for chromosome in range(1, 23))
AUTOSOME_SET = frozenset(AUTOSOMES)


def _jsonable(value: object) -> Any:
    """Convert diagnostic details to the harness's JSON-safe representation."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("cannot serialize a non-finite float")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"cannot serialize value of type {type(value).__name__}")


def coverage_metrics_calibration_exclusion(
    metrics: object,
    *,
    expected_truth_haplotype_windows_by_autosome: Mapping[str, int] | None = None,
    expected_input_markers: int | None = None,
    expected_input_markers_by_autosome: Mapping[str, int] | None = None,
    expected_input_markers_by_source: Mapping[str, int] | None = None,
    expected_model_markers_by_autosome: Mapping[str, int] | None = None,
) -> dict[str, object] | None:
    """Validate the complete coverage schema and its calibration invariants.

    ``expected_truth_haplotype_windows_by_autosome`` is optional because some
    authenticated downstream records do not carry fixture truth geometry.  If
    supplied, it is the expected number of *haplotype* windows on each autosome
    (twice the number of diploid local-truth windows) and enables the harness's
    truth/model-window equality check.  Every other schema and reconciliation
    invariant is enforced regardless.
    """

    def exclusion(kind: str, message: str, **details: object) -> dict[str, object]:
        return {"type": kind, "message": message, **details}

    def nonnegative_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def finite_number(value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )

    def autosome_counts(value: object) -> dict[str, int] | None:
        if not isinstance(value, Mapping) or set(value) != AUTOSOME_SET:
            return None
        if not all(nonnegative_int(value[chromosome]) for chromosome in AUTOSOMES):
            return None
        return {chromosome: int(value[chromosome]) for chromosome in AUTOSOMES}

    if not isinstance(metrics, Mapping):
        return exclusion(
            "missing_lai_coverage_metrics",
            "Production result did not include LAI coverage metrics.",
        )
    schema_version = metrics.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        return exclusion(
            "unsupported_lai_coverage_schema",
            "LAI coverage metrics must use schema version 1.",
        )

    status = metrics.get("model_denominators")
    if not isinstance(status, Mapping):
        return exclusion(
            "missing_model_denominator_status",
            "LAI coverage metrics did not declare denominator completeness.",
        )

    unreadable = status.get("unreadable_autosomes")
    if status.get("complete") is not True or unreadable != []:
        return exclusion(
            "incomplete_model_denominators",
            (
                "At least one model-marker/window denominator was unreadable; "
                "exclude this observation from threshold selection."
            ),
            unreadable_autosomes=_jsonable(unreadable),
        )

    emitted = metrics.get("emitted_markers")
    if not isinstance(emitted, Mapping) or not nonnegative_int(emitted.get("total")):
        return exclusion("invalid_emitted_markers", "Emitted-marker totals are invalid.")
    emitted_by = autosome_counts(emitted.get("by_autosome"))
    if emitted_by is None or sum(emitted_by.values()) != emitted["total"]:
        return exclusion(
            "invalid_emitted_markers",
            "Emitted-marker per-autosome counts do not match the aggregate.",
        )
    if expected_input_markers_by_autosome is not None and emitted_by != dict(
        expected_input_markers_by_autosome
    ):
        return exclusion(
            "emitted_input_mismatch",
            "Emitted-marker per-autosome counts do not match the selected input.",
            expected_by_autosome=dict(expected_input_markers_by_autosome),
            observed_by_autosome=emitted_by,
        )

    model_markers = metrics.get("model_markers")
    if not isinstance(model_markers, Mapping):
        return exclusion("invalid_model_markers", "Model-marker metrics are missing.")
    marker_by = model_markers.get("by_autosome")
    aggregate = model_markers.get("aggregate")
    if not isinstance(marker_by, Mapping) or set(marker_by) != AUTOSOME_SET:
        return exclusion(
            "invalid_model_markers",
            "Model-marker metrics must cover autosomes 1..22 exactly.",
        )
    marker_sums = {"matched": 0, "total": 0, "allele_mismatch": 0}
    for chromosome in AUTOSOMES:
        counts = marker_by[chromosome]
        if not isinstance(counts, Mapping) or not all(
            nonnegative_int(counts.get(field)) for field in marker_sums
        ):
            return exclusion(
                "invalid_model_markers",
                f"Model-marker counts are invalid for chr{chromosome}.",
            )
        if counts["total"] <= 0 or counts["matched"] + counts["allele_mismatch"] > counts["total"]:
            return exclusion(
                "invalid_model_markers",
                f"Model-marker counts violate the denominator for chr{chromosome}.",
            )
        if expected_model_markers_by_autosome is not None and counts[
            "total"
        ] != expected_model_markers_by_autosome.get(chromosome):
            return exclusion(
                "model_marker_denominator_mismatch",
                f"Model-marker denominator does not match pinned C for chr{chromosome}.",
                expected_total=expected_model_markers_by_autosome.get(chromosome),
                observed_total=counts["total"],
            )
        match_rate = counts.get("match_rate")
        expected_rate = round(counts["matched"] / counts["total"], 6)
        if not finite_number(match_rate) or not math.isclose(
            float(match_rate), expected_rate, abs_tol=1e-9
        ):
            return exclusion(
                "invalid_model_markers",
                f"Model-marker match rate is inconsistent for chr{chromosome}.",
            )
        for field in marker_sums:
            marker_sums[field] += int(counts[field])
    if not isinstance(aggregate, Mapping) or any(
        aggregate.get(field) != total for field, total in marker_sums.items()
    ):
        return exclusion(
            "invalid_model_markers",
            "Model-marker aggregate counts do not match per-autosome counts.",
        )
    aggregate_rate = aggregate.get("match_rate")
    expected_aggregate_rate = round(marker_sums["matched"] / marker_sums["total"], 6)
    if not finite_number(aggregate_rate) or not math.isclose(
        float(aggregate_rate), expected_aggregate_rate, abs_tol=1e-9
    ):
        return exclusion(
            "invalid_model_markers",
            "Model-marker aggregate match rate is inconsistent.",
        )

    for field in ("phased_autosomes", "analyzed_autosomes"):
        autosomes = metrics.get(field)
        if not isinstance(autosomes, Mapping):
            return exclusion("invalid_autosome_set", f"{field} metrics are missing.")
        identities = autosomes.get("identities")
        if (
            not isinstance(identities, list)
            or any(
                not isinstance(chromosome, int) or not 1 <= chromosome <= 22
                for chromosome in identities
            )
            or len(identities) != len(set(identities))
            or identities != sorted(identities)
            or autosomes.get("count") != len(identities)
        ):
            return exclusion("invalid_autosome_set", f"{field} metrics are inconsistent.")
    phased_ids = set(metrics["phased_autosomes"]["identities"])
    analyzed_ids = set(metrics["analyzed_autosomes"]["identities"])
    if not analyzed_ids <= phased_ids:
        return exclusion(
            "invalid_autosome_set",
            "Analyzed autosomes are not a subset of phased autosomes.",
        )
    emitted_ids = {int(chromosome) for chromosome, count in emitted_by.items() if count > 0}
    if not phased_ids <= emitted_ids:
        return exclusion(
            "invalid_autosome_set",
            "Phased autosomes are not a subset of autosomes with emitted markers.",
        )

    windows = metrics.get("haplotype_windows")
    if not isinstance(windows, Mapping):
        return exclusion("invalid_haplotype_windows", "Haplotype-window metrics are missing.")
    expected_by = autosome_counts(windows.get("expected_by_autosome"))
    valid_by = autosome_counts(windows.get("valid_assigned_by_autosome"))
    expected = windows.get("expected")
    valid = windows.get("valid_assigned")
    if (
        expected_by is None
        or valid_by is None
        or not nonnegative_int(expected)
        or not nonnegative_int(valid)
        or sum(expected_by.values()) != expected
        or sum(valid_by.values()) != valid
        or valid > expected
        or any(valid_by[chromosome] > expected_by[chromosome] for chromosome in AUTOSOMES)
    ):
        return exclusion(
            "invalid_haplotype_windows",
            "Haplotype-window aggregate/per-autosome counts are inconsistent.",
        )
    if expected_truth_haplotype_windows_by_autosome is not None and expected_by != dict(
        expected_truth_haplotype_windows_by_autosome
    ):
        return exclusion(
            "truth_model_window_mismatch",
            "Local truth does not cover every expected model haplotype window exactly.",
            truth_expected_by_autosome=dict(expected_truth_haplotype_windows_by_autosome),
            model_expected_by_autosome=expected_by,
        )
    if any(
        valid_by[chromosome] > 0 and int(chromosome) not in analyzed_ids
        for chromosome in AUTOSOMES
    ):
        return exclusion(
            "invalid_haplotype_windows",
            "Assigned windows were reported for an unanalyzed autosome.",
        )
    assignment_rate = windows.get("assignment_rate")
    calculated_rate = valid / expected if expected else 0.0
    if not finite_number(assignment_rate) or abs(float(assignment_rate) - calculated_rate) > 1e-6:
        return exclusion(
            "invalid_haplotype_windows",
            "Haplotype-window assignment rate does not match its counts.",
        )
    per_source = metrics.get("per_source")
    if not isinstance(per_source, Mapping) or not per_source:
        return exclusion("invalid_per_source_metrics", "Per-source coverage metrics are missing.")
    source_hits = 0
    source_total = 0
    for source, counts in per_source.items():
        if (
            not isinstance(source, str)
            or not source
            or not isinstance(counts, Mapping)
            or set(counts) != {"hits", "drops"}
            or not nonnegative_int(counts.get("hits"))
            or not nonnegative_int(counts.get("drops"))
        ):
            return exclusion(
                "invalid_per_source_metrics",
                "Per-source coverage counts do not match schema version 1.",
            )
        source_hits += int(counts["hits"])
        source_total += int(counts["hits"]) + int(counts["drops"])
    if source_hits != emitted["total"]:
        return exclusion(
            "invalid_per_source_metrics",
            "Per-source hits do not match emitted-marker totals.",
        )
    if expected_input_markers is not None and source_total != expected_input_markers:
        return exclusion(
            "invalid_per_source_metrics",
            "Per-source hits and drops do not account for every selected marker.",
            expected_input_markers=expected_input_markers,
            observed_input_markers=source_total,
        )
    if expected_input_markers_by_source is not None:
        observed_by_source = {
            str(source): int(counts["hits"]) + int(counts["drops"])
            for source, counts in per_source.items()
        }
        if observed_by_source != dict(expected_input_markers_by_source):
            return exclusion(
                "invalid_per_source_metrics",
                "Per-source totals do not match the selected input markers.",
                expected_by_source=dict(expected_input_markers_by_source),
                observed_by_source=observed_by_source,
            )
    return None
