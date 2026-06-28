"""Shared finding-surfacing gate (validation strategy D2; F8).

A genotyping chip reports a call at *every* probe regardless of biology, so a
finding can be surfaced that is impossible for the individual — most starkly a
Y-chromosome "Pathogenic SRY" finding on a female (XX) sample. ``is_surfaceable``
is the shared predicate a finding generator consults before emitting a finding
that carries a chromosome, so the chromosome/sex rule lives in one place rather
than being re-derived (or forgotten) per module.

**Scope today (#851).** Only ``rare_variant_finder`` — the one generator that
works directly on raw variants carrying a ``chrom`` — currently wires this gate;
any other generator that could emit a sex-chromosome finding must opt in
explicitly with the same ``is_surfaceable(chrom, inferred_sex)`` filter. The
practical surface is empty for now: the curated panels carry only autosomal / X /
MT loci (no chrY), the Y-haplogroup call is itself gated to ``inferred_sex == "XY"``
(:func:`backend.analysis.ancestry.assign_haplogroups`), and ``sex_aneuploidy`` /
``kinship`` are *intentionally* about the sex chromosomes / relatedness and are
exempt by design (gating them on ``"XX"`` would defeat their purpose). Wiring a
new chrY-capable generator needs the same per-module chrY×XX-dropped / XY-kept
test (#711). A guard in ``tests/backend/test_finding_gate.py`` pins this caller
set so the doc and code can't drift apart again.

Biological sex is inferred once per run via
:func:`backend.services.sex_inference.infer_biological_sex` and threaded in, so
this module stays a pure predicate with no DB access.

**Firewall gate (SW-C6).** :func:`imputed_variant_surfaceable` is the sibling gate
for *imputed*-backed findings: an imputed variant may back a finding only when it
clears the SW-C3 MAF/r² firewall (:func:`backend.analysis.imputation_firewall.assess_variant`).
Its caller set (``imputed_findings`` only) is pinned by the same
``tests/backend/test_finding_gate.py`` guard, so this doc and the code can't drift.
Like :func:`is_surfaceable` it stays a pure predicate (the firewall has no DB access).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.analysis.imputation_runner import ImputedVariant

_Y_CHROMS: frozenset[str] = frozenset({"Y", "CHRY"})


def is_surfaceable(chrom: str | None, inferred_sex: str | None) -> bool:
    """Return ``False`` for a finding that contradicts the inferred sex.

    Conservative by design: a finding is dropped only when the contradiction is
    unambiguous *and* sex is confidently known. Today that is a Y-chromosome
    finding on a confidently-``"XX"`` sample (biologically impossible — F8). When
    sex is ``"manual_review"`` / ``"unknown"`` / ``None`` nothing is dropped,
    because we cannot be sure (a false drop would hide a real finding).

    Args:
        chrom: the finding's chromosome (``"Y"``/``"chrY"`` etc.).
        inferred_sex: ``"XX"`` / ``"XY"`` / ``"manual_review"`` / ``"unknown"``.

    Returns:
        ``True`` if the finding may surface, ``False`` if it must be suppressed.
    """
    chrom_norm = (chrom or "").strip().upper()
    if chrom_norm in _Y_CHROMS and inferred_sex == "XX":
        return False
    return True


def imputed_variant_surfaceable(variant: ImputedVariant) -> bool:
    """Enforce the SW-C3 MAF/r² firewall at the finding gate (SW-C6).

    An *imputed* variant may back a finding only when it clears the firewall
    (:func:`backend.analysis.imputation_firewall.assess_variant` — well-imputed
    ``DR2 >= 0.8`` **and** common ``MAF >= 1%``). This is the shared chokepoint a
    generator that surfaces imputed variants (``imputed_findings``) consults before
    emitting a finding, so the firewall rule lives in one place rather than being
    re-derived per module — the same discipline :func:`is_surfaceable` follows for
    the sex/chromosome rule. A genotyped (non-imputed) variant always passes: the
    firewall does not apply to a directly observed call.

    **Defense in depth.** ``imputed_variants`` only ever stores firewall-cleared
    rows (:func:`backend.analysis.imputation_persist.persist_imputed_variants` drops
    quarantined markers), so in the normal path every row already clears this gate.
    Re-asserting here means a finding can *never* rest on an imputed variant that
    fails the firewall — even if a future code path builds one from another source,
    or a stale/out-of-range row somehow slips past persistence.

    Args:
        variant: the imputed (or genotyped) marker backing a candidate finding.

    Returns:
        ``True`` if the variant may back a finding, ``False`` if the firewall
        quarantines it (imputed and rare / low-DR2 / missing DR2 or AF).
    """
    from backend.analysis.imputation_firewall import assess_variant

    return assess_variant(variant).reportable
