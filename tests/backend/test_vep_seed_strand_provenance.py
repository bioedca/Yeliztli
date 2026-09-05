"""Offline provenance guard for ``vep_seed.csv``'s annotation columns.

``tests/fixtures/seed_csvs/vep_seed.csv`` seeds ``mini_vep_bundle.db``, whose rows
``backend/annotation/engine.py`` copies into ``annotated_variants`` unchanged. It
shipped a ``strand`` column that was wrong for **19 of its 57 annotated genes**,
and ``rs28399504`` (CYP2C19``*4``) annotated ``missense_variant`` / ``p.Met1Val``
when Ensembl VEP calls it ``start_lost`` / ``p.Met1?`` -- downgrading a HIGH-impact
no-function allele to MODERATE and asserting a residue at a codon that is no
longer a start (#2045).

Three guards, cheapest first:

1. **Internal consistency** needs no network and no snapshot: a gene has exactly
   one strand, so two rows of the same gene disagreeing is provably wrong on its
   own terms. Four genes did (CYP2C19, MTHFR, PON1, CYP2R1).
2. **Snapshot agreement** against ``tests/fixtures/vep_gene_strand_snapshot.json``
   (Ensembl GRCh37 ``lookup/symbol``). Loud like the gene-phenotype guard rather
   than silently skipping, so a new gene cannot slip in unchecked.
3. **Start-loss semantics**: a variant at ``c.1`` of a coding transcript destroys
   the initiator codon, so it cannot be a ``missense_variant`` naming a
   substituted residue.

None of these touches the network at test time.
"""

from __future__ import annotations

import collections
import csv
import json
import re
from pathlib import Path

from scripts.build_vep_strand_snapshot import SYNTHETIC_GENES
from scripts.build_vep_strand_snapshot import _genes as _generator_genes

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "vep_seed.csv"
_SNAPSHOT = _ROOT / "tests" / "fixtures" / "vep_gene_strand_snapshot.json"

# The seed carries one deliberately synthetic locus (rs12345 / ENST00000999999),
# used by several suites as a neutral stand-in. It has no Ensembl record, so it is
# exempt from the snapshot check -- but the exemption is pinned to this exact
# symbol, and `test_only_the_known_synthetic_gene_is_exempt` fails if another
# unresolvable symbol appears, so a fabricated gene cannot hide behind it. The set
# is the generator's own, so the guard cannot exempt a symbol the generator still
# submits to Ensembl (which fails the rebuild) or vice versa.
_SYNTHETIC_GENES = SYNTHETIC_GENES

# "c.1A>G" -- a substitution at the first coding base. Anchored so "c.10A>G" and
# "c.1_2del" do not match: only a single-base substitution at position 1.
_CODING_START = re.compile(r"^c\.1[ACGT]>[ACGT]$")

# A protein change naming a concrete residue at codon 1, e.g. "p.Met1Val".
_NAMED_START_RESIDUE = re.compile(r"^p\.Met1(?!\?)[A-Za-z]{2,3}$")


def _rows() -> list[dict[str, str]]:
    with _SEED.open(encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row.get("gene_symbol")]


def _snapshot() -> dict[str, dict[str, str]]:
    return json.loads(_SNAPSHOT.read_text(encoding="utf-8"))["strands"]


def _strands_by_gene(*, include_synthetic: bool = False) -> dict[str, set[str]]:
    by_gene: dict[str, set[str]] = collections.defaultdict(set)
    for row in _rows():
        gene = row["gene_symbol"]
        if not include_synthetic and gene in _SYNTHETIC_GENES:
            continue
        by_gene[gene].add(row["strand"])
    return dict(by_gene)


def test_only_the_known_synthetic_gene_is_exempt() -> None:
    """The exemption must not become a hiding place for a fabricated symbol."""
    snapshot = set(_snapshot())
    unresolvable = {
        gene for gene in _strands_by_gene(include_synthetic=True) if gene not in snapshot
    }
    assert unresolvable == _SYNTHETIC_GENES, (
        f"unresolvable gene symbols in vep_seed.csv: {sorted(unresolvable)} -- expected only "
        f"{sorted(_SYNTHETIC_GENES)}. A real gene missing from the snapshot means the snapshot "
        "needs regenerating; an unknown symbol means the row is fabricated."
    )


def test_generator_skips_the_synthetic_gene_it_cannot_resolve() -> None:
    """The documented rebuild must be runnable.

    The generator fails closed on any symbol Ensembl cannot resolve, so feeding it
    ``GENE1`` made ``scripts/build_vep_strand_snapshot.py`` exit 1 on the very seed
    it documents rebuilding from (#2045 review). Its input must be exactly the
    genes the committed snapshot covers: nothing synthetic, nothing missing.
    """
    submitted = set(_generator_genes())
    assert submitted.isdisjoint(_SYNTHETIC_GENES), (
        f"generator submits synthetic symbols to Ensembl: {sorted(submitted & _SYNTHETIC_GENES)}"
    )
    assert submitted == set(_snapshot()), (
        "generator input and committed snapshot disagree -- "
        f"only in generator: {sorted(submitted - set(_snapshot()))}; "
        f"only in snapshot: {sorted(set(_snapshot()) - submitted)}"
    )


def test_snapshot_records_the_exclusion_the_guard_applies() -> None:
    """A snapshot built under a different exclusion set must not pass silently."""
    provenance = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))["_provenance"]
    assert set(provenance["synthetic_excluded"]) == _SYNTHETIC_GENES, (
        f"snapshot was generated excluding {provenance['synthetic_excluded']}, but the "
        f"guard exempts {sorted(_SYNTHETIC_GENES)} -- regenerate the snapshot"
    )


def test_seed_is_substantial_enough_to_guard() -> None:
    """Anti-vacuity: a guard over an empty or tiny sweep proves nothing."""
    rows = _rows()
    assert len(rows) >= 90, f"expected the annotated seed to be substantial, got {len(rows)} rows"
    assert len(_strands_by_gene()) >= 50, "expected ~56 real annotated genes in the sweep"


def test_each_gene_has_exactly_one_strand() -> None:
    """A gene's strand is invariant -- two rows disagreeing is self-contradictory.

    This half needs neither network nor snapshot: it is wrong on its own terms.
    """
    contradictory = {
        gene: sorted(strands) for gene, strands in _strands_by_gene().items() if len(strands) > 1
    }
    assert not contradictory, (
        "genes carrying more than one strand in vep_seed.csv (impossible for a real gene): "
        f"{contradictory}"
    )


def test_every_gene_strand_matches_the_ensembl_snapshot() -> None:
    """Loud on an uncovered gene, rather than skipping it."""
    snapshot = _snapshot()
    by_gene = _strands_by_gene()

    uncovered = sorted(set(by_gene) - set(snapshot))
    assert not uncovered, (
        f"genes absent from the strand snapshot: {uncovered} -- regenerate with "
        "scripts/build_vep_strand_snapshot.py"
    )

    wrong = [
        f"{gene}: seed={sorted(strands)} ensembl={snapshot[gene]['strand']}"
        for gene, strands in sorted(by_gene.items())
        if strands != {snapshot[gene]["strand"]}
    ]
    assert not wrong, "gene strands disagreeing with Ensembl GRCh37:\n" + "\n".join(wrong)


def test_snapshot_entries_still_apply() -> None:
    """Self-cleaning: a snapshot entry must still describe a gene the seed cites."""
    stale = sorted(set(_snapshot()) - set(_strands_by_gene()))
    assert not stale, f"snapshot genes no longer cited by vep_seed.csv: {stale}"


def test_a_start_codon_substitution_is_not_called_missense() -> None:
    """``c.1`` destroys the initiator methionine: that is a start loss.

    CYP2C19``*4`` (rs28399504, ``c.1A>G``) shipped as ``missense_variant`` /
    ``p.Met1Val``. Ensembl VEP calls it ``start_lost`` / ``p.Met1?`` -- HIGH impact,
    and it cannot name a downstream product. Calling it a missense understates a
    defining no-function allele for any severity ranking.
    """
    offenders = []
    for row in _rows():
        if not _CODING_START.match(row.get("hgvs_coding", "") or ""):
            continue
        if row.get("consequence") != "start_lost":
            offenders.append(
                f"{row['rsid']} ({row['gene_symbol']}): {row['hgvs_coding']} is annotated "
                f"{row['consequence']!r}, expected 'start_lost'"
            )
        if _NAMED_START_RESIDUE.match(row.get("hgvs_protein", "") or ""):
            offenders.append(
                f"{row['rsid']} ({row['gene_symbol']}): hgvs_protein "
                f"{row['hgvs_protein']!r} names a residue at a codon that is no longer a start"
            )
    assert not offenders, "start-codon variants annotated as substitutions:\n" + "\n".join(
        offenders
    )


def test_cyp2c19_star4_stays_a_start_loss() -> None:
    """Spot-lock for #2045's headline row, so a silent revert fails loudly."""
    row = next((r for r in _rows() if r["rsid"] == "rs28399504"), None)
    assert row is not None, "rs28399504 (CYP2C19*4) missing from vep_seed.csv"
    assert row["gene_symbol"] == "CYP2C19"
    assert row["transcript_id"] == "ENST00000371321"
    assert row["hgvs_coding"] == "c.1A>G"
    assert row["consequence"] == "start_lost"
    assert row["hgvs_protein"] == "p.Met1?"
    assert row["strand"] == "+"
