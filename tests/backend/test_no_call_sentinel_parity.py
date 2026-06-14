"""Parity tests locking the ``??``/``--`` equivalence across analysis modules.

Step 61 (MRG-01a part 2; Plan §10.3, §11.1, §11.2) adopts the shared
``backend.analysis.zygosity.is_no_call`` helper at the no-call filter site of
every analysis module that reads ``raw_variants.genotype``. This test asserts,
per module, that the ``??`` merge-ambiguity sentinel (emitted by
``backend/services/sample_merge.py`` ``flag_only`` strategy in Step 65)
produces identical finding emission/suppression as the legacy ``--`` no-call.

Coverage is the thirteen modules enumerated in Step 61:

    traits, gene_health, sleep, nutrigenomics, fitness, methylation,
    skin, allergy, pharmacogenomics (_count_alt_alleles),
    prs (_count_effect_allele), apoe (determine_apoe_genotype),
    ancestry (haplogroup tree-walk), lai_runner (pre-VCF-write drop filter).

If any module's no-call filter drifts from the shared helper, exactly one of
these parametrize cases will fail — that's the lock the cross-cutting
adoption depends on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlalchemy as sa

from backend.analysis import (
    allergy,
    fitness,
    gene_health,
    methylation,
    nutrigenomics,
    skin,
    sleep,
    traits,
)
from backend.analysis.ancestry import (
    HaplogroupNode,
    HaplogroupSNP,
    _classify_node_match,
)
from backend.analysis.apoe import (
    APOE_RS7412,
    APOE_RS429358,
    APOEStatus,
    determine_apoe_genotype,
)
from backend.analysis.lai_runner import LAIRunner
from backend.analysis.pharmacogenomics import _count_alt_alleles
from backend.analysis.prs import _count_effect_allele
from backend.analysis.risk_genotype import (
    _TRUE_NO_CALLS as _RISK_GENOTYPE_TRUE_NO_CALLS,
)
from backend.analysis.zygosity import _NO_CALL_SENTINELS
from backend.api.routes.variants import _classify_genotype
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants

# ── Trait-style modules (8 modules sharing the _normalize_genotype contract) ─

_TRAIT_NORMALIZE: list[tuple[str, Callable[[str | None], str | None]]] = [
    ("traits", traits._normalize_genotype),
    ("gene_health", gene_health._normalize_genotype),
    ("sleep", sleep._normalize_genotype),
    ("nutrigenomics", nutrigenomics._normalize_genotype),
    ("fitness", fitness._normalize_genotype),
    ("methylation", methylation._normalize_genotype),
    ("skin", skin._normalize_genotype),
    ("allergy", allergy._normalize_genotype),
]


@pytest.mark.parametrize(
    "_module_name,normalize_fn",
    _TRAIT_NORMALIZE,
    ids=[name for name, _ in _TRAIT_NORMALIZE],
)
def test_trait_module_normalize_genotype_parity(
    _module_name: str,
    normalize_fn: Callable[[str | None], str | None],
) -> None:
    """``--`` and ``??`` both yield ``None`` from ``_normalize_genotype``.

    Locks suppression parity for the eight trait-association modules:
    a ``??`` row from a merged-sample ``flag_only`` discordance is treated
    identically to a legacy ``--`` no-call — no finding is emitted from
    either input.
    """
    assert normalize_fn("--") is None
    assert normalize_fn("??") is None
    assert normalize_fn("--") == normalize_fn("??")


# ── pharmacogenomics._count_alt_alleles ──────────────────────────────────────


def test_pharmacogenomics_count_alt_alleles_parity() -> None:
    """``--`` and ``??`` both yield ``None`` from ``_count_alt_alleles``.

    CPIC star-allele counting suppresses both legacy and merge-ambiguity
    no-calls identically.
    """
    assert _count_alt_alleles("--", "A", "T") is None
    assert _count_alt_alleles("??", "A", "T") is None
    assert _count_alt_alleles("--", "A", "T") == _count_alt_alleles("??", "A", "T")


# ── prs._count_effect_allele ─────────────────────────────────────────────────


def test_prs_count_effect_allele_parity() -> None:
    """``--`` and ``??`` both yield ``0`` from ``_count_effect_allele``.

    PRS dosage extraction treats merge-ambiguity rows as missing data, same as
    legacy no-call. (``0`` is the missing-data sentinel for PRS dosage —
    contributes nothing to the score.)
    """
    assert _count_effect_allele("--", "A") == 0
    assert _count_effect_allele("??", "A") == 0
    assert _count_effect_allele("--", "A") == _count_effect_allele("??", "A")


# ── apoe.determine_apoe_genotype ─────────────────────────────────────────────


def _make_apoe_sample_engine(rs429358_gt: str, rs7412_gt: str) -> sa.Engine:
    """In-memory sample DB seeded with the two APOE rsIDs at given genotypes."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {
                    "rsid": APOE_RS429358,
                    "chrom": "19",
                    "pos": 44908684,
                    "genotype": rs429358_gt,
                },
                {
                    "rsid": APOE_RS7412,
                    "chrom": "19",
                    "pos": 44908822,
                    "genotype": rs7412_gt,
                },
            ],
        )
    return engine


def test_apoe_determine_genotype_parity() -> None:
    """``--`` and ``??`` at APOE rsIDs both yield ``APOEStatus.NO_CALL``.

    The APOE gate suppresses diplotype determination identically for legacy
    no-call and merge-ambiguity rows at either rs429358 or rs7412.
    """
    result_dash = determine_apoe_genotype(_make_apoe_sample_engine("--", "--"))
    result_q = determine_apoe_genotype(_make_apoe_sample_engine("??", "??"))

    assert result_dash.status == APOEStatus.NO_CALL
    assert result_q.status == APOEStatus.NO_CALL
    assert result_dash.status == result_q.status


# ── ancestry._classify_node_match (haplogroup tree-walk) ─────────────────────


def test_ancestry_haplogroup_tree_walk_parity() -> None:
    """``--`` and ``??`` at a defining SNP both yield ``snps_present == 0``.

    Haplogroup tree-walk evidence accumulation treats merge-ambiguity rows as
    missing evidence, same as legacy no-call.
    """
    snp = HaplogroupSNP(rsid="rs123", pos=1000, allele="A")
    node = HaplogroupNode(haplogroup="H1", defining_snps=[snp], children=[])

    present_dash, _conf_dash, total_dash = _classify_node_match(node, {"rs123": "--"})
    present_q, _conf_q, total_q = _classify_node_match(node, {"rs123": "??"})

    assert present_dash == 0
    assert present_q == 0
    assert (present_dash, total_dash) == (present_q, total_q)


# ── lai_runner._filter_genotypes (pre-VCF-write drop filter) ─────────────────


def test_lai_runner_filter_genotypes_parity() -> None:
    """``--`` and ``??`` both get dropped by the pre-VCF-write filter.

    ``_filter_genotypes`` doesn't reference ``self``, so we call it as an
    unbound function to avoid materialising a real LAI bundle.
    """
    base_row = {"rsid": "rs1", "chrom": "1", "pos": 100}
    gts_dash = [{**base_row, "genotype": "--"}]
    gts_q = [{**base_row, "genotype": "??"}]

    filter_fn = LAIRunner._filter_genotypes
    assert filter_fn(None, gts_dash) == []
    assert filter_fn(None, gts_q) == []
    assert filter_fn(None, gts_dash) == filter_fn(None, gts_q)


# ── variants._classify_genotype QC reclassification (Step 62 / Plan §11.3) ──
#
# Unlike every other site in this file, ``_classify_genotype`` is *not*
# byte-identical under the MRG-01a adoption: ``"00"`` and the indel codes
# (``"DD"``/``"II"``/``"DI"``/``"ID"``) reclassify from het/hom to nocall so
# AncestryDNA + merged-sample QC denominators stop double-counting unscoreable
# rows as het/hom. The parametrize block below locks the new mapping; the
# `??` merge sentinel additionally rides the same nocall path so the QC bucket
# stays transparent to Step 65's flag_only output.

_CLASSIFY_GENOTYPE_NOCALL_INPUTS: list[str | None] = [
    # Step 62 reclassification — these were het/hom pre-MRG-01a.
    "00",
    "DD",
    "II",
    "DI",
    "ID",
    # Existing nocall rows — were already nocall via the legacy `not genotype
    # or genotype == "--"` short-circuit OR fall newly into nocall via
    # is_no_call() (e.g. "-" / "0", which used to be classified `hom` because
    # `len(genotype) == 1`). Listed here so the contract reads as a single
    # union of every no-call sentinel.
    "--",
    "??",
    "-",
    "0",
    "",
    None,
]


@pytest.mark.parametrize(
    "genotype",
    _CLASSIFY_GENOTYPE_NOCALL_INPUTS,
    ids=lambda g: "None" if g is None else (g or "empty"),
)
def test_classify_genotype_reclassifies_to_nocall(genotype: str | None) -> None:
    """``_classify_genotype`` returns ``"nocall"`` for every no-call sentinel.

    Plan §11.3: the QC-stats classifier adopts ``is_no_call()`` so AncestryDNA's
    ``"00"`` row, the indel codes, and the ``??`` merge sentinel each bucket
    into the no-call denominator instead of inflating het/hom counts.
    """
    assert _classify_genotype(genotype) == "nocall"


_CLASSIFY_GENOTYPE_CALLED_INPUTS: list[tuple[str, str]] = [
    # Diploid het / hom rows are unaffected by the reclassification — locking
    # them here proves the change is scoped to the no-call boundary.
    ("AA", "hom"),
    ("GG", "hom"),
    ("AG", "het"),
    ("CT", "het"),
    # Single-char haploid SNP calls stay hom — they are not in the no-call set.
    ("A", "hom"),
    ("G", "hom"),
    # Single-char indel alleles also stay hom — they're scoreable haploid
    # calls; only the *two-char* indel codes flip to nocall.
    ("D", "hom"),
    ("I", "hom"),
]


@pytest.mark.parametrize(
    "genotype,expected",
    _CLASSIFY_GENOTYPE_CALLED_INPUTS,
    ids=[g for g, _ in _CLASSIFY_GENOTYPE_CALLED_INPUTS],
)
def test_classify_genotype_called_rows_unchanged(genotype: str, expected: str) -> None:
    """Called rows (het / hom) retain their pre-MRG-01a classification."""
    assert _classify_genotype(genotype) == expected


# ── risk_genotype._TRUE_NO_CALLS (second hand-maintained sentinel set) ───────
#
# risk_genotype keeps its OWN no-call set rather than delegating to
# ``is_no_call`` because it must *score* I/D calls at indel-typed loci (e.g.
# APOL1 G2) instead of discarding them — so it recognises every genuine no-call
# sentinel EXCEPT the indel codes. The set is now derived from
# ``_NO_CALL_SENTINELS`` so it cannot drift, but this guard pins the invariant
# with an INDEPENDENT indel-code literal: it fails if a new sentinel added to
# the shared set fails to reach risk_genotype, or if the set of excluded indel
# codes changes (#525).

_INDEL_NO_CALL_CODES = frozenset({"II", "DD", "DI", "ID"})


def test_risk_genotype_true_no_calls_match_shared_sentinels_minus_indels() -> None:
    """``risk_genotype._TRUE_NO_CALLS`` == shared sentinels minus the indel codes.

    Every *genuine* no-call recognised by ``is_no_call`` must also be a no-call
    for the by-rsID risk modules (gout, AMD, hemochromatosis, APOL1, …). The
    only intended difference is the four indel codes, which risk_genotype scores
    at indel-typed loci. If the two sets drift apart, this fails — the lock the
    issue (#525) asked for, mirroring the per-module cases above.
    """
    assert _RISK_GENOTYPE_TRUE_NO_CALLS == _NO_CALL_SENTINELS - _INDEL_NO_CALL_CODES
    # Every risk-genotype no-call is also a shared-recognition no-call: no extra
    # sentinel unknown to is_no_call leaks in.
    assert _RISK_GENOTYPE_TRUE_NO_CALLS <= _NO_CALL_SENTINELS
    # …and the shared set differs from it by exactly the indel codes.
    assert _NO_CALL_SENTINELS - _RISK_GENOTYPE_TRUE_NO_CALLS == _INDEL_NO_CALL_CODES
