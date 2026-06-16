"""Suite-wide guard: a single-base SNP's ``genotype_effects`` map must be COMPLETE.

A categorical locus scores a sample by looking its genotype up in
``genotype_effects`` (:func:`backend.analysis.genotype_lookup.lookup_by_genotype`).
When the map omits a genotype the sample actually **has**, the lookup returns
``None`` and the categorical ``_score_snp`` defaults that carrier — to the green
``Standard`` ("no risk") category for most modules, or (fitness/methylation, #608)
to a withheld ``Indeterminate``. Either way an *omitted curated row* is a silent,
falsely-reassuring miscall: a carrier of an omitted heterozygote is told "no
effect" instead of Moderate/Elevated, and only a ``logger.warning`` is emitted —
nothing surfaces to the user or to CI.

This is the third self-discovering panel-data-integrity guard, alongside the
``risk != ref`` guard (#600, ``test_panel_risk_ref_invariant.py``) and the
indel-polarity guard (#508/PR #554, ``test_indel_polarity_provenance.py``). It
walks every ``backend/data/panels/*.json`` locus, so a new or edited single-base
SNP missing its ref-homozygote, risk-homozygote, or heterozygote row fails
immediately — there is no hand-maintained allow-list to forget, which is the exact
gap that lets a curation slip ship green. Distinct from #600 (the *degenerate*
``risk == ref`` case): here the alleles are fine but a genotype ROW is absent, and
the failure mode is worse (a falsely-reassuring miscall, not broken scoring).

Loci whose ``{risk, ref}`` are not both single A/C/G/T bases are out of frame and
skipped: indel ``D/I`` loci (covered by the indel-polarity guard) and proxies with
a ``None`` allele (e.g. ``sleep_panel.json::rs2858884``, scored outside the
``{risk, ref}`` dosage frame).
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.analysis.gene_health as gene_health_mod

_PANELS = Path(gene_health_mod.__file__).resolve().parent.parent / "data" / "panels"
_BASES = frozenset("ACGT")


def _walk_dicts(node: object):
    """Yield every dict nested anywhere inside a parsed-JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _is_single_base(allele: object) -> bool:
    """Whether ``allele`` is a single A/C/G/T base (so it forms an ACGT dosage pair).

    Filters out indel ``D``/``I`` tokens, ``None`` proxy alleles, and any
    multi-character value — those are not scored in the single-base ``{risk, ref}``
    genotype frame this guard checks.
    """
    return isinstance(allele, str) and len(allele) == 1 and allele.upper() in _BASES


def _missing_genotype_rows(risk: str, ref: str, keys: set[str]) -> list[str]:
    """Genotype rows the ``{risk, ref}`` dosage pair requires but ``keys`` omits.

    Returns a list naming each absent row among the ref-homozygote, risk-homozygote,
    and heterozygote. The heterozygote counts as covered if *either* allele order is
    present, since :func:`lookup_by_genotype` harmonizes allele order.
    """
    risk_b, ref_b = risk.upper(), ref.upper()
    present = {k.upper() for k in keys}
    missing = [g for g in (ref_b + ref_b, risk_b + risk_b) if g not in present]
    het_orders = {ref_b + risk_b, risk_b + ref_b}
    if not (het_orders & present):
        missing.append(f"het {ref_b + risk_b}/{risk_b + ref_b}")
    return missing


def _discover_single_base_snps() -> dict[str, dict]:
    """``{f'{panel}::{rsid}': snp}`` for every locus with single-base
    ``risk_allele != ref_allele`` and a dict ``genotype_effects`` map, all panels."""
    found: dict[str, dict] = {}
    for path in sorted(_PANELS.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for node in _walk_dicts(raw):
            risk, ref = node.get("risk_allele"), node.get("ref_allele")
            effects = node.get("genotype_effects")
            if (
                isinstance(effects, dict)
                and _is_single_base(risk)
                and _is_single_base(ref)
                and risk.upper() != ref.upper()
            ):
                found[f"{path.name}::{node.get('rsid')}"] = node
    return found


def test_discovery_finds_single_base_snps() -> None:
    """Guard the guard: the walker must find a substantial set of loci, so the
    completeness assertion below cannot pass vacuously if discovery ever breaks
    (e.g. a panel schema change)."""
    snps = _discover_single_base_snps()
    assert len(snps) >= 100, f"single-base SNP discovery regressed; found only {len(snps)}"


def test_every_single_base_snp_has_complete_genotype_effects() -> None:
    """SELF-DISCOVERING durable guard (#609): every single-base ``risk != ref``
    locus's ``genotype_effects`` must cover the ref-homozygote, risk-homozygote, and
    heterozygote — otherwise a carrier of the omitted genotype is silently scored
    ``Standard`` (or withheld), a falsely-reassuring miscall CI never sees."""
    offenders = []
    for label, snp in sorted(_discover_single_base_snps().items()):
        missing = _missing_genotype_rows(
            snp["risk_allele"], snp["ref_allele"], set(snp["genotype_effects"])
        )
        if missing:
            offenders.append(f"{label} missing {missing}")
    assert not offenders, (
        "incomplete genotype_effects (a carrier of the omitted genotype is silently "
        "scored Standard / withheld instead of Moderate/Elevated): " + "; ".join(offenders)
    )


def test_completeness_check_detects_missing_rows() -> None:
    """Prove the check actually fires, so the suite-wide assertion is not vacuous.

    Uses a C/T pair (risk=T, ref=C): the complete map has no missing rows, a
    single-order heterozygote still counts as covered, and dropping the het or a
    homozygote is flagged.
    """
    assert _missing_genotype_rows("T", "C", {"CC", "CT", "TC", "TT"}) == []
    assert _missing_genotype_rows("T", "C", {"CC", "CT", "TT"}) == []  # one het order is enough
    assert _missing_genotype_rows("T", "C", {"CC", "TT"}) == ["het CT/TC"]
    assert _missing_genotype_rows("T", "C", {"CC"}) == ["TT", "het CT/TC"]
