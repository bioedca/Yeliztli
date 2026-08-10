"""Suite-wide guard: a panel's nucleotide shorthand must point the same way as
the amino-acid change printed beside it.

Curated panels label a locus with a legacy coding shorthand — ``C677T``,
``A1298C``, ``A80G`` — and many of those rows also carry an amino-acid change in
``hgvs_protein`` (or in a parenthetical inside ``variant_name``). Both strings are
rendered together: ``methylation.store_methylation_findings`` builds
``f"{gene} {variant_name} ({genotype}) — {effect_summary}"`` and
``components/methylation/PathwayDetailPanel.tsx`` prints ``{gene} — {variant_name}``.
So a shorthand whose reference and alternate bases are the wrong way round does
not merely sit in a data file; it reaches the user as one self-contradictory
string.

That is #2023: ``methylation_panel.json`` shipped ``"G80A (His27Arg)"`` for
SLC19A1 rs1051266. His is ``CAY`` and Arg is ``CGY``, so His→Arg is A→G at codon
position 2 — the canonical ``c.80A>G`` (NCBI dbSNP RefSNP v2 on RefSeq
``NM_194255.4``/``NP_919231.1``, and Ensembl GRCh37, both accessed 2026-08-10;
in-repo, ``tests/fixtures/seed_csvs/vep_seed.csv`` agrees). ``G80A`` asserts the
opposite polarity, and no genetic code reconciles the two halves of that label.

Do **not** reach for the committed ``bundles/vep_bundle.db`` to settle a question
like this: every single-base row in it orders the two alleles alphabetically
rather than reference-first, so its ``hgvs_protein`` is inverted for the whole
T/C class — it reports ``p.Arg27His`` here. That artifact is the trap #2023's
reporter narrowly missed, and it is written up separately.

The check is purely combinatorial and needs no external reference: a shorthand
``<ref><pos><alt>`` is consistent with ``<From><n><To>`` if some codon encoding
``From`` and some codon encoding ``To`` differ at exactly one position, where the
first carries ``ref`` and the second carries ``alt``. Deliberately *not* checked:

* that ``pos`` falls inside codon ``n``. The shorthands use legacy transcript
  numbering — MTHFR ``C677T`` is ``p.Ala222Val``, whose codon is c.664-666 — so a
  positional check would fail on correct rows.
* the reverse-complement spelling. ``variant_name`` is written on the same strand
  as ``hgvs_protein``; accepting the complement as well would have let ``G80A``
  through only if C→T reached Arg from a His codon, which it does not, but it
  would blunt the guard everywhere else.

**The check has a real blind spot, and it is asserted rather than assumed.** For
some amino-acid pairs *both* directions are reachable through different codon
pairs — Arg→Ser is reachable by A→C (``AGA``→``AGC``) and by C→A
(``CGT``→``AGT``) — so reversing such a shorthand would still satisfy the
predicate. Measured over the standard code, **124 of the 324 reachable
(from, to, ref, alt) combinations (38.3%) are direction-undecidable this way**,
so this is a large gap rather than a corner case. Two tests below pin it: one
asserts no *non-synonymous* panel row currently falls in it, and one records that
*synonymous* rows are structurally undecidable (Tyr→Tyr is symmetric by
construction, so CBS ``rs234706 C699T (Tyr233Tyr)`` cannot be direction-checked
here at all). Deciding those needs the actual transcript codon, not the code
table.

This is a SELF-DISCOVERING guard (mirroring ``test_panel_risk_ref_invariant.py``
and ``test_panel_effect_summary_consistency.py``): it walks every
``backend/data/panels/*.json`` node, so a newly added or edited shorthand is
covered with no allow-list to update.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PANEL_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "panels"

# Standard genetic code (NCBI translation table 1), generated in TCAG codon order.
_CODON_TABLE = {
    f"{first}{second}{third}": amino
    for (first, second, third), amino in zip(
        ((a, b, c) for a in "TCAG" for b in "TCAG" for c in "TCAG"),
        "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG",
        strict=True,
    )
}

_THREE_TO_ONE = {
    "Ala": "A",
    "Arg": "R",
    "Asn": "N",
    "Asp": "D",
    "Cys": "C",
    "Gln": "Q",
    "Glu": "E",
    "Gly": "G",
    "His": "H",
    "Ile": "I",
    "Leu": "L",
    "Lys": "K",
    "Met": "M",
    "Phe": "F",
    "Pro": "P",
    "Ser": "S",
    "Thr": "T",
    "Trp": "W",
    "Tyr": "Y",
    "Val": "V",
    "Ter": "*",
}

# A leading ``<base><digits><base>`` shorthand, e.g. "A80G" or "C1420T (Leu474Phe)".
_NUCLEOTIDE_SHORTHAND = re.compile(r"^([ACGT])(\d+)([ACGT])(?![A-Za-z0-9])")
_HGVS_PROTEIN = re.compile(r"^p\.([A-Z][a-z]{2})(\d+)([A-Z][a-z]{2})$")
_PARENTHETICAL_PROTEIN = re.compile(r"\(([A-Z][a-z]{2})(\d+)([A-Z][a-z]{2})\)")


def _walk_dicts(node: object):
    """Yield every dict nested anywhere inside a parsed-JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _protein_change(node: dict) -> tuple[str, int, str] | None:
    """``(from_aa, position, to_aa)`` in one-letter form, from ``hgvs_protein`` if
    present, else from a ``(His27Arg)`` parenthetical inside ``variant_name``."""
    raw = node.get("hgvs_protein")
    match = _HGVS_PROTEIN.match(raw) if isinstance(raw, str) else None
    if match is None:
        name = node.get("variant_name")
        match = _PARENTHETICAL_PROTEIN.search(name) if isinstance(name, str) else None
    if match is None:
        return None
    first, position, second = match.group(1), int(match.group(2)), match.group(3)
    if first not in _THREE_TO_ONE or second not in _THREE_TO_ONE:
        return None
    return _THREE_TO_ONE[first], position, _THREE_TO_ONE[second]


def _codons_for(amino: str) -> list[str]:
    return [codon for codon, encoded in _CODON_TABLE.items() if encoded == amino]


def _substitution_reaches(ref: str, alt: str, from_aa: str, to_aa: str) -> bool:
    """Whether a single ``ref``→``alt`` base change can turn ``from_aa`` into
    ``to_aa`` under the standard genetic code."""
    for source in _codons_for(from_aa):
        for target in _codons_for(to_aa):
            differing = [i for i in range(3) if source[i] != target[i]]
            if len(differing) != 1:
                continue
            index = differing[0]
            if source[index] == ref and target[index] == alt:
                return True
    return False


def _direction_is_decidable(ref: str, alt: str, from_aa: str, to_aa: str) -> bool:
    """Whether the code table can tell this shorthand apart from its reverse.

    True when exactly one of the two directions reaches the amino-acid change, so
    the shorthand's polarity carries information. False when *both* reach it —
    the guard would then accept the shorthand written either way round and proves
    nothing about direction for that row.

    A row where the forward direction fails is a plain violation, not an
    ambiguity: it is decidably backwards, and
    ``test_every_panel_shorthand_agrees_with_its_amino_acid_change`` reports it.
    """
    forward = _substitution_reaches(ref, alt, from_aa, to_aa)
    reverse = _substitution_reaches(alt, ref, from_aa, to_aa)
    return forward and not reverse


def _is_protein_shorthand(ref: str, position: int, alt: str, change: tuple[str, int, str]) -> bool:
    """Whether the shorthand is really a one-letter *amino-acid* label that only
    looks nucleotide-like because A/C/G/T are also amino-acid codes.

    ATG16L1 ``rs2241880`` in ``gene_health_panel.json`` is exactly this: ``T300A``
    is Thr300Ala, not a T→A base change, and reading it as nucleotides would
    manufacture a false failure.
    """
    from_aa, protein_position, to_aa = change
    return position == protein_position and ref == from_aa and alt == to_aa


def _discover_shorthand_loci() -> list[tuple[str, str, str, tuple[str, int, str]]]:
    """``(label, variant_name, panel_file, protein_change)`` for every panel locus
    whose ``variant_name`` opens with a nucleotide shorthand *and* carries an
    amino-acid change to check it against."""
    found: list[tuple[str, str, str, tuple[str, int, str]]] = []
    for path in sorted(PANEL_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for node in _walk_dicts(raw):
            name = node.get("variant_name")
            if not isinstance(name, str) or not _NUCLEOTIDE_SHORTHAND.match(name):
                continue
            change = _protein_change(node)
            if change is None:
                continue
            found.append((f"{path.name}::{node.get('rsid')}", name, path.name, change))
    return found


def test_detector_rejects_the_2023_label_and_accepts_its_fix() -> None:
    """The detector must actually discriminate: it has to reject the shipped
    ``G80A (His27Arg)`` and accept the canonical ``A80G (His27Arg)``.

    Without this, a detector that silently matched nothing would let the guard
    below pass while enforcing nothing.
    """
    assert not _substitution_reaches("G", "A", "H", "R")
    assert _substitution_reaches("A", "G", "H", "R")

    # The other real shorthands in the panels, as a spot-check of the code table.
    assert _substitution_reaches("C", "T", "A", "V")  # MTHFR C677T / Ala222Val
    assert _substitution_reaches("A", "C", "E", "A")  # MTHFR A1298C / Glu429Ala
    assert _substitution_reaches("C", "T", "Y", "Y")  # CBS C699T / Tyr233Tyr
    assert _substitution_reaches("G", "A", "R", "Q")  # BHMT G742A / Arg239Gln


def test_protein_shorthand_is_not_read_as_nucleotides() -> None:
    """``T300A``/Thr300Ala must be recognised as an amino-acid label.

    Thr→Ala needs A→G at codon position 1, so treating the label as a T→A base
    change would report a false contradiction on a correct row.
    """
    assert _is_protein_shorthand("T", 300, "A", ("T", 300, "A"))
    assert not _substitution_reaches("T", "A", "T", "A")
    # A genuine nucleotide shorthand must not be mistaken for a protein one.
    assert not _is_protein_shorthand("G", 80, "A", ("H", 27, "R"))
    assert not _is_protein_shorthand("C", 677, "T", ("A", 222, "V"))


def test_ambiguous_amino_acid_pairs_are_reported_as_undecidable() -> None:
    """The decidability helper must recognise the pairs this guard cannot judge.

    Arg→Ser is the worked case: ``AGA``→``AGC`` makes it reachable by A→C, and
    ``CGT``→``AGT`` by C→A, so a reversed ``A123C``/``C123A`` shorthand would
    satisfy the forward check either way round. His→Arg, the #2023 case, is
    reachable in one direction only and so is genuinely decidable.
    """
    assert _substitution_reaches("A", "C", "R", "S")
    assert _substitution_reaches("C", "A", "R", "S")
    assert not _direction_is_decidable("A", "C", "R", "S")

    assert _direction_is_decidable("A", "G", "H", "R")
    assert _direction_is_decidable("C", "T", "A", "V")

    # Synonymous changes are symmetric by construction and never decidable here.
    assert not _direction_is_decidable("C", "T", "Y", "Y")


def test_discovery_finds_the_panel_shorthand_loci() -> None:
    """Sanity: the walker must keep finding the curated shorthand rows, so the
    invariant below cannot pass vacuously after a schema or path change."""
    loci = _discover_shorthand_loci()
    assert len(loci) >= 14, f"shorthand discovery regressed; found only {len(loci)}"
    assert any(label.endswith("::rs1051266") for label, _, _, _ in loci)


def test_every_panel_shorthand_agrees_with_its_amino_acid_change() -> None:
    """SELF-DISCOVERING durable guard (#2023): every ``<ref><pos><alt>`` shorthand
    must be reachable, under the standard genetic code, as the amino-acid change
    the same row declares."""
    offenders = []
    for label, name, _panel, change in _discover_shorthand_loci():
        match = _NUCLEOTIDE_SHORTHAND.match(name)
        assert match is not None  # guaranteed by discovery
        ref, position, alt = match.group(1), int(match.group(2)), match.group(3)
        if _is_protein_shorthand(ref, position, alt, change):
            continue
        from_aa, protein_position, to_aa = change
        if ref == alt:
            offenders.append(f"{label} {name!r} has identical ref and alt bases")
            continue
        if not _substitution_reaches(ref, alt, from_aa, to_aa):
            offenders.append(
                f"{label} {name!r} says {ref}->{alt} but "
                f"{from_aa}{protein_position}{to_aa} is unreachable that way"
            )
    assert not offenders, (
        "panel nucleotide shorthand contradicts its own amino-acid change: " + "; ".join(offenders)
    )


def _decidability_split() -> tuple[list[str], list[str]]:
    """``(undecidable_nonsynonymous, undecidable_synonymous)`` panel row labels."""
    non_synonymous: list[str] = []
    synonymous: list[str] = []
    for label, name, _panel, change in _discover_shorthand_loci():
        match = _NUCLEOTIDE_SHORTHAND.match(name)
        assert match is not None  # guaranteed by discovery
        ref, position, alt = match.group(1), int(match.group(2)), match.group(3)
        if _is_protein_shorthand(ref, position, alt, change) or ref == alt:
            continue
        from_aa, _protein_position, to_aa = change
        if not _substitution_reaches(ref, alt, from_aa, to_aa):
            continue  # a violation, reported by the guard above — not an ambiguity
        if _direction_is_decidable(ref, alt, from_aa, to_aa):
            continue
        (synonymous if from_aa == to_aa else non_synonymous).append(f"{label} {name!r}")
    return non_synonymous, synonymous


def test_no_nonsynonymous_shorthand_escapes_the_direction_check() -> None:
    """Coverage honesty: the guard above must not be silently vacuous for a row.

    For 38.3% of reachable (from, to, ref, alt) combinations both directions
    satisfy the predicate, so the guard would accept such a shorthand reversed.
    No non-synonymous panel row is in that state today; if one is ever added,
    this fails and says so, because verifying it needs the real transcript codon
    rather than the code table — silently passing it would be the
    non-discriminating-guard failure this file exists to prevent.
    """
    non_synonymous, _ = _decidability_split()
    assert not non_synonymous, (
        "panel shorthand whose direction this guard cannot decide (both "
        "substitution directions reach the declared amino-acid change); verify "
        "against the transcript codon: " + "; ".join(non_synonymous)
    )


def test_synonymous_shorthand_is_recorded_as_structurally_undecidable() -> None:
    """Synonymous rows cannot be direction-checked by codon reachability at all.

    Tyr→Tyr is symmetric by construction (``TAT``↔``TAC``), so both C→T and T→C
    satisfy the predicate. CBS ``rs234706 C699T (Tyr233Tyr)`` is the current
    example. Asserting the set explicitly keeps the guard's real coverage
    visible instead of letting these rows look checked when they are not.
    """
    _, synonymous = _decidability_split()
    assert [entry.split()[0] for entry in synonymous] == ["methylation_panel.json::rs234706"], (
        "the set of structurally-undecidable synonymous shorthands changed; "
        "update this lock deliberately: " + "; ".join(synonymous)
    )
