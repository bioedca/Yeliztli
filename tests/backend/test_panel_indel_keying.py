"""Suite-wide guard: an indel locus must be keyed on vendor I/D tokens, not
nucleotide strings.

The indel-polarity guard (``test_indel_polarity_provenance.py``, #508) validates
loci that ALREADY use I/D tokens, but it cannot catch the inverse — and more
dangerous — slip: an insertion/deletion locus curated with **nucleotide-string**
``genotype_effects`` keys instead of I/D. That is exactly how #610 shipped: MMP1
``rs1799750`` (1G/2G) was keyed ``GG``/``GGG``/``GGGG``, so the ``II``/``ID``/
``DD`` tokens 23andMe actually emits never matched and the variant was silently
scored "not genotyped" for every 23andMe user — the panel-data corner of the
recurring "genotyped variant silently dropped" class (cf. #610, #498, #528, and
the completeness guard #609).

The signal is detectable from the panel alone (no external data): an indel written
as nucleotides has ACGT ``risk_allele``/``ref_allele`` of **different lengths**,
and/or ``genotype_effects`` keys that are pure-ACGT but of **non-uniform length**
(``GG`` / ``GGG`` / ``GGGG``). A true SNP locus has equal-length alleles and
uniform-length 2-char keys, so this signal is specific to indels-as-nucleotides.

This is a SELF-DISCOVERING guard (mirrors #508 / #609 /
``test_panel_risk_ref_invariant.py``): it walks every ``backend/data/panels/*.json``
locus, so a new or edited indel locus curated with nucleotide-string keys fails
immediately — there is no hand-maintained allow-list to forget to update, which is
the precise gap that silently dropped MMP1. It dovetails with the indel-polarity
guard, which then validates the I/D provenance once the locus is correctly keyed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.analysis.gene_health as gene_health_mod

_PANELS = Path(gene_health_mod.__file__).resolve().parent.parent / "data" / "panels"
_ACGT = frozenset("ACGT")


def _is_acgt(value: object) -> bool:
    """True for a non-empty, pure-A/C/G/T string (case-insensitive)."""
    return isinstance(value, str) and len(value) > 0 and set(value.upper()) <= _ACGT


def _walk_snps(node: object):
    """Yield every dict that carries a ``genotype_effects`` map, anywhere inside a
    parsed panel-JSON structure."""
    if isinstance(node, dict):
        if isinstance(node.get("genotype_effects"), dict):
            yield node
        for value in node.values():
            yield from _walk_snps(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_snps(item)


def _nucleotide_indel_offense(snp: dict) -> str | None:
    """Describe why ``snp`` looks like a nucleotide-string indel, or ``None``.

    Two independent, panel-local signals (#736):
      - ``risk_allele``/``ref_allele`` are both pure-ACGT but of differing length
        (e.g. ``"G"`` vs ``"GG"``), or
      - at least two ``genotype_effects`` keys are pure-ACGT but of non-uniform
        length (e.g. ``GG`` / ``GGG`` / ``GGGG``).

    Correctly-keyed indels use I/D tokens (``risk``/``ref`` ∈ {``D``, ``I``} or
    ``"delG"``, and ``DD``/``DI``/``ID``/``II`` keys), none of which are pure-ACGT,
    so they do not trip either signal.
    """
    risk, ref = snp.get("risk_allele"), snp.get("ref_allele")
    diff_len_alleles = _is_acgt(risk) and _is_acgt(ref) and len(risk) != len(ref)

    acgt_keys = [k for k in snp.get("genotype_effects", {}) if _is_acgt(k)]
    nonuniform_keys = len(acgt_keys) >= 2 and len({len(k) for k in acgt_keys}) > 1

    if diff_len_alleles or nonuniform_keys:
        return (
            f"alleles {risk!r}/{ref!r}, acgt_keys {sorted(acgt_keys)} — curate as "
            "I/D indel tokens (DD/DI/ID/II + indel_genotype_map), not nucleotide strings"
        )
    return None


def _discover_genotype_effect_loci() -> list[tuple[str, dict]]:
    """``[(f'{panel}::{rsid}', snp_dict), ...]`` for every locus with a
    ``genotype_effects`` map, across all panels."""
    found: list[tuple[str, dict]] = []
    for path in sorted(_PANELS.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for snp in _walk_snps(raw):
            found.append((f"{path.name}::{snp.get('rsid')}", snp))
    return found


def test_discovery_finds_genotype_effect_loci() -> None:
    """Sanity: the walker must find the curated loci, so the guard below cannot
    pass vacuously if panel discovery ever breaks (e.g. a schema change)."""
    loci = _discover_genotype_effect_loci()
    assert len(loci) >= 100, f"genotype_effects locus discovery regressed; found only {len(loci)}"


def test_no_indel_locus_is_keyed_on_nucleotide_strings() -> None:
    """SELF-DISCOVERING durable guard (#736): no panel indel locus may be keyed on
    nucleotide strings instead of vendor I/D tokens.

    0 offenders today (MMP1 rs1799750 was the only one; fixed in #610), so this
    passes and locks the invariant — the moment a new/edited indel locus is curated
    with nucleotide-string keys (the slip that silently dropped MMP1 for every
    I/D-reporting vendor), it fails.
    """
    offenders = [
        f"{label} ({offense})"
        for label, snp in _discover_genotype_effect_loci()
        if (offense := _nucleotide_indel_offense(snp)) is not None
    ]
    assert not offenders, (
        "indel locus keyed on nucleotide strings (silently dropped for I/D-reporting "
        "vendors like 23andMe — see #610): " + "; ".join(offenders)
    )


@pytest.mark.parametrize(
    "snp",
    [
        # MMP1 rs1799750 (1G/2G) as it was mis-keyed in #610: non-uniform ACGT keys.
        {"rsid": "rsMMP1LIKE", "genotype_effects": {"GG": {}, "GGG": {}, "GGGG": {}}},
        # A deletion written as differing-length ACGT risk/ref instead of D/I.
        {
            "rsid": "rsDELLIKE",
            "risk_allele": "G",
            "ref_allele": "GG",
            "genotype_effects": {"GG": {}, "G": {}},
        },
    ],
)
def test_guard_fires_on_nucleotide_string_indel(snp: dict) -> None:
    """The guard must FIRE on a nucleotide-string indel map (both signals), so it
    cannot pass vacuously — acceptance criterion 2 of #736."""
    assert _nucleotide_indel_offense(snp) is not None


def test_guard_passes_a_normal_snp_locus() -> None:
    """Negative control: a real SNP locus (equal-length alleles, uniform 2-char
    keys) is NOT flagged, so the guard does not over-fire on the common case."""
    snp = {
        "rsid": "rsSNPLIKE",
        "risk_allele": "A",
        "ref_allele": "G",
        "genotype_effects": {"AA": {}, "AG": {}, "GG": {}},
    }
    assert _nucleotide_indel_offense(snp) is None
