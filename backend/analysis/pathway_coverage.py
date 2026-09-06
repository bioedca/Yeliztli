"""Coverage-aware wording for categorical pathway summaries."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

STANDARD_LEVEL = "Standard"
NO_CALL_STATUS = "no_call"


def _label_aliases(gene: str) -> list[str]:
    """Every spelling of ``gene`` that a curated ``variant_name`` may open with.

    A plain symbol is its own only alias. A composite label such as
    ``"CAV1/CAV2"`` or ``"MCM6/LCT"`` is also recognised by any single
    component ("LCT -13910C>T") and by the components joined with ``-`` or
    ``/`` ("CAV1-CAV2 intergenic"), because curators write the same locus both
    ways (#2044).
    """
    parts = [part for part in gene.split("/") if part]
    if len(parts) < 2:
        return [gene]
    return list(dict.fromkeys([gene, *parts, "-".join(parts), "/".join(parts)]))


def _leads_with(variant_name: str, token: str) -> bool:
    """``variant_name`` opens with ``token`` at a word boundary.

    Case-insensitive, and the character after the token must be missing or
    non-alphanumeric, so a gene that merely shares a prefix ("GC" vs
    "GCH1 …", "IL2" vs "IL2RA …") does not count as a match.
    """
    return variant_name.lower().startswith(token.lower()) and (
        len(variant_name) == len(token) or not variant_name[len(token)].isalnum()
    )


def variant_label(gene: str | None, variant_name: str | None) -> str:
    """Render "<gene> <variant_name>" without doubling the gene symbol.

    Roughly a third of curated panel entries already name the gene inside
    ``variant_name`` ("FTO intron 1", "HLA-B*57:01 proxy"), so the naive
    ``f"{gene} {variant_name}"`` produced cards reading "FTO FTO intron 1"
    (#2021). The match is anchored on a word boundary so a gene that merely
    shares a prefix with the variant name is still prepended. A composite gene
    label ("CAV1/CAV2", "MCM6/LCT") is matched by any of its equivalent
    spellings — one component, or the components joined with ``-`` or ``/`` —
    so "CAV1-CAV2 intergenic" and "LCT -13910C>T" are not prefixed either
    (#2044).
    """
    gene = (gene or "").strip()
    variant_name = (variant_name or "").strip()
    if not gene:
        return variant_name
    if not variant_name:
        return gene
    if any(_leads_with(variant_name, alias) for alias in _label_aliases(gene)):
        return variant_name
    return f"{gene} {variant_name}"


def _rsid(snp: Any) -> str:
    return str(getattr(snp, "rsid"))


def _coverage_status(snp: Any) -> str | None:
    status = getattr(snp, "coverage_status", None)
    return str(status) if status is not None else None


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def missing_rsid_groups(missing_snps: Iterable[Any]) -> tuple[list[str], list[str], list[str]]:
    """Return all missing, on-array no-call, and off-chip rsids.

    The categorical modules historically expose ``missing_snps`` as the union of
    off-chip variants and on-array no-calls. Keep that contract, but make the
    off-chip subset explicit for summary wording and downstream UI.
    """
    missing = list(missing_snps)
    all_missing = [_rsid(snp) for snp in missing]
    no_call = [_rsid(snp) for snp in missing if _coverage_status(snp) == NO_CALL_STATUS]
    off_chip = [_rsid(snp) for snp in missing if _coverage_status(snp) != NO_CALL_STATUS]
    return all_missing, no_call, off_chip


def pathway_level_display_label(level: str | None, detail: dict[str, Any] | None) -> str | None:
    """Coverage-aware display label for a categorical pathway level.

    Mirrors the frontend ``pathwayLevelDisplayLabel``
    (``frontend/src/lib/pathwayCoverage.ts``): a **Standard** pathway with at least
    one *missing* tracked SNP is not a clean whole-panel negative, so it is shown as
    ``Tested Standard`` (some tracked SNPs were called) or ``Not Assessed`` (none
    were), never a plain ``Standard`` badge — the same false-reassurance guard the
    interactive cards use (#1091/#1582), now on the static report/export path
    (#1651). Non-Standard levels and fully-covered Standard pathways keep their raw
    label. ``detail`` is the finding's parsed ``detail_json`` (``called_snps`` /
    ``missing_snps``); a missing ``called_snps`` conservatively yields ``Not
    Assessed`` rather than a plain ``Standard``.
    """
    if not level:
        return level
    detail = detail if isinstance(detail, dict) else {}
    if level != STANDARD_LEVEL:
        return level
    called = detail.get("called_snps") or 0
    # `called_snps` counts every observed SNP, including ones withheld from
    # interpretation. If all of them are indeterminate nothing was actually
    # scored, so a plain Standard badge would be false reassurance on the report
    # and export paths even at full coverage (#2178) — the same guard the missing
    # SNP branch below applies, for a different reason.
    indeterminate = len(detail.get("indeterminate_snps") or [])
    if called > 0 and called - indeterminate <= 0:
        return "Not Assessed"
    missing = detail.get("missing_snps") or []
    if not missing:
        return level
    return "Not Assessed" if called == 0 else "Tested Standard"


def format_not_assessed(missing_snps: Iterable[Any]) -> str:
    """Format a compact off-chip/no-call count for user-facing summaries."""
    all_missing, no_call, off_chip = missing_rsid_groups(missing_snps)
    total = len(all_missing)
    if total == 0:
        return "0 tracked SNPs"

    parts: list[str] = []
    if off_chip:
        parts.append(f"{len(off_chip)} off-chip")
    if no_call:
        parts.append(f"{len(no_call)} no-call")

    base = _plural(total, "tracked SNP")
    return f"{base} ({', '.join(parts)})" if parts else base


def coverage_interpretation(
    *,
    level: str,
    called_count: int,
    missing_snps: Iterable[Any],
    indeterminate_count: int = 0,
    standard_limited_phrase: str = "No variants of concern among tested SNPs",
) -> str | None:
    """Return a concise caveat when pathway coverage is incomplete."""
    missing = list(missing_snps)
    if not missing:
        return None

    not_assessed = format_not_assessed(missing)
    if level == STANDARD_LEVEL and indeterminate_count > 0:
        return f"Standard result is based on interpreted SNPs only; {not_assessed} not assessed."
    if called_count <= 0:
        return f"No tracked SNPs assessed; {not_assessed} not assessed."
    if level == STANDARD_LEVEL:
        return f"{standard_limited_phrase}; {not_assessed} not assessed."
    return f"{level} result is based on tested SNPs only; {not_assessed} not assessed."


def pathway_summary_text(
    *,
    pathway_name: str,
    level: str,
    called_count: int,
    missing_snps: Iterable[Any],
    indeterminate_count: int = 0,
    standard_complete_phrase: str = "Standard (no variants of concern)",
    standard_limited_phrase: str = "No variants of concern among tested SNPs",
) -> str:
    """Build the stored pathway-summary finding text.

    ``pathway_level`` remains the raw categorical category for compatibility.
    The human-readable headline is qualified when some tracked SNPs were not
    assessed, so a Standard category cannot read as a whole-panel negative.
    """
    if level != STANDARD_LEVEL:
        return f"{pathway_name} — {level} consideration"

    interpretation = coverage_interpretation(
        level=level,
        called_count=called_count,
        missing_snps=missing_snps,
        indeterminate_count=indeterminate_count,
        standard_limited_phrase=standard_limited_phrase,
    )
    if interpretation is None:
        return f"{pathway_name} — {standard_complete_phrase}"
    return f"{pathway_name} — {interpretation}"


def coverage_detail(
    *,
    level: str,
    called_count: int,
    missing_snps: Iterable[Any],
    indeterminate_count: int = 0,
    standard_limited_phrase: str = "No variants of concern among tested SNPs",
) -> dict[str, Any]:
    """Build detail_json fields shared by pathway summary findings."""
    missing = list(missing_snps)
    all_missing, no_call, off_chip = missing_rsid_groups(missing)
    detail: dict[str, Any] = {
        "missing_snps": all_missing,
        "no_call_snps": no_call,
        "off_chip_snps": off_chip,
    }
    interpretation = coverage_interpretation(
        level=level,
        called_count=called_count,
        missing_snps=missing,
        indeterminate_count=indeterminate_count,
        standard_limited_phrase=standard_limited_phrase,
    )
    if interpretation is not None:
        detail["coverage_interpretation"] = interpretation
    return detail
