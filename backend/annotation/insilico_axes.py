"""Shared in-silico evidence-axis assessment helpers."""

from __future__ import annotations

from typing import Any

SIFT_THRESHOLD = 0.05
POLYPHEN_PROBABLY_DAMAGING_THRESHOLD = 0.909
CADD_PHRED_THRESHOLD = 20.0
REVEL_THRESHOLD = 0.5
METALR_THRESHOLD = 0.5


def _get(variant: dict[str, Any] | Any, key: str) -> Any:
    if isinstance(variant, dict):
        return variant.get(key)
    return getattr(variant, key, None)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_pred(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _sift_axis(variant: dict[str, Any] | Any) -> bool | None:
    """SIFT axis: numeric score first, categorical prediction as fixture fallback."""
    score = _as_float(_get(variant, "sift_score"))
    if score is not None:
        return score < SIFT_THRESHOLD
    pred = _as_pred(_get(variant, "sift_pred"))
    if pred is None:
        return None
    return pred == "d"


def _polyphen_axis(variant: dict[str, Any] | Any) -> bool | None:
    """PolyPhen-2 HVAR axis: numeric score first, categorical prediction as fallback."""
    score = _as_float(_get(variant, "polyphen2_hsvar_score"))
    if score is not None:
        return score > POLYPHEN_PROBABLY_DAMAGING_THRESHOLD
    pred = _as_pred(_get(variant, "polyphen2_hsvar_pred"))
    if pred is None:
        return None
    return pred in {"d", "probably_damaging", "probably damaging"}


def _cadd_axis(variant: dict[str, Any] | Any) -> bool | None:
    score = _as_float(_get(variant, "cadd_phred"))
    if score is None:
        return None
    return score >= CADD_PHRED_THRESHOLD


def _meta_predictor_votes(variant: dict[str, Any] | Any) -> list[tuple[str, bool]]:
    votes: list[tuple[str, bool]] = []
    revel = _as_float(_get(variant, "revel"))
    if revel is not None:
        votes.append(("REVEL", revel >= REVEL_THRESHOLD))
    metasvm = _as_float(_get(variant, "metasvm"))
    if metasvm is not None:
        votes.append(("MetaSVM", metasvm > 0))
    metalr = _as_float(_get(variant, "metalr"))
    if metalr is not None:
        votes.append(("MetaLR", metalr > METALR_THRESHOLD))
    return votes


def _meta_axis(variant: dict[str, Any] | Any) -> bool | None:
    votes = [vote for _, vote in _meta_predictor_votes(variant)]
    if not votes:
        return None
    return sum(votes) * 2 > len(votes)


def assess_insilico_axes(variant: dict[str, Any] | Any) -> tuple[int, int]:
    """Return ``(deleterious_axes, assessed_axes)`` over four independent axes.

    The canonical F24/F25 model counts SIFT, PolyPhen-2, CADD, and a collapsed
    META axis for REVEL/MetaSVM/MetaLR. The denominator is axes with data.
    """
    axes = [
        _sift_axis(variant),
        _polyphen_axis(variant),
        _cadd_axis(variant),
        _meta_axis(variant),
    ]
    assessed = [axis for axis in axes if axis is not None]
    return sum(1 for axis in assessed if axis), len(assessed)


def deleterious_predictor_names(variant: dict[str, Any] | Any) -> list[str]:
    """List individual predictors that crossed their deleterious thresholds."""
    names: list[str] = []
    if _sift_axis(variant) is True:
        names.append("SIFT")
    if _polyphen_axis(variant) is True:
        names.append("PolyPhen-2")
    if _cadd_axis(variant) is True:
        names.append("CADD")
    names.extend(name for name, vote in _meta_predictor_votes(variant) if vote)
    return names
