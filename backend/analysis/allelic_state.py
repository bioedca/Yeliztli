"""Shared LOINC allelic-state labeling helpers.

The stored ``zygosity`` values are compact analysis states
(``het``/``hom_alt``). Display and exchange surfaces need a biological
allelic-state label that accounts for single-copy loci.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.services.sex_inference import is_grch37_x_par_position

LOINC_SYSTEM = "http://loinc.org"
LOINC_ALLELIC_STATE = "53034-5"  # Allelic state

# LOINC answer list LL381-5, verified at loinc.org.
ALLELIC_STATE_MAP: dict[str, dict[str, str]] = {
    "het": {
        "system": LOINC_SYSTEM,
        "code": "LA6706-1",
        "display": "Heterozygous",
    },
    "hom_alt": {
        "system": LOINC_SYSTEM,
        "code": "LA6705-3",
        "display": "Homozygous",
    },
}

ALLELIC_STATE_HEMIZYGOUS: dict[str, str] = {
    "system": LOINC_SYSTEM,
    "code": "LA6707-9",
    "display": "Hemizygous",
}
ALLELIC_STATE_HOMOPLASMIC: dict[str, str] = {
    "system": LOINC_SYSTEM,
    "code": "LA6704-6",
    "display": "Homoplasmic",
}


def norm_chrom_label(chrom: Any) -> str | None:
    """Normalize chromosome labels for X/Y/MT comparisons."""
    if chrom is None:
        return None
    c = str(chrom).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    c = c.upper()
    return "MT" if c == "M" else c


def allelic_state_coding_for_call(
    *,
    chrom: Any,
    pos: Any,
    zygosity: str | None,
    sex: str | None,
) -> dict[str, str] | None:
    """Return a LOINC allelic-state coding for a carried variant call.

    ``Homozygous`` and ``Heterozygous`` are diploid labels. Carried chrMT calls
    are labelled ``Homoplasmic`` by default, and XY non-PAR chrX/chrY
    ``hom_alt`` calls are labelled ``Hemizygous``.
    """
    if not zygosity or zygosity not in ALLELIC_STATE_MAP:
        return None

    chrom_label = norm_chrom_label(chrom)
    if chrom_label == "MT":
        return ALLELIC_STATE_HOMOPLASMIC

    if sex == "XY" and zygosity == "hom_alt" and chrom_label in ("X", "Y"):
        if chrom_label == "Y":
            return ALLELIC_STATE_HEMIZYGOUS
        if pos is not None and not is_grch37_x_par_position(int(pos)):
            return ALLELIC_STATE_HEMIZYGOUS

    return ALLELIC_STATE_MAP[zygosity]


def allelic_state_coding(row: Mapping[str, Any], sex: str | None) -> dict[str, str] | None:
    """Return a LOINC allelic-state coding for a row-like variant object."""
    return allelic_state_coding_for_call(
        chrom=row.get("chrom"),
        pos=row.get("pos"),
        zygosity=row.get("zygosity"),
        sex=sex,
    )


def allelic_state_label_for_call(
    *,
    chrom: Any,
    pos: Any,
    zygosity: str | None,
    sex: str | None,
) -> str | None:
    """Return the display label for a carried variant call."""
    coding = allelic_state_coding_for_call(chrom=chrom, pos=pos, zygosity=zygosity, sex=sex)
    return None if coding is None else coding["display"]


def allelic_state_label(row: Mapping[str, Any], sex: str | None) -> str | None:
    """Return the display label for a row-like variant object."""
    coding = allelic_state_coding(row, sex)
    return None if coding is None else coding["display"]
