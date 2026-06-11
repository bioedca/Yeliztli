"""Strand-orientation guard for CPIC star-allele definitions.

Yeliztli's pharmacogenomics caller (``backend/analysis/pharmacogenomics.py``)
compares a 23andMe genotype **directly** to the ``ref``/``alt`` stored for each
star allele in ``cpic_alleles.csv``, with **no strand harmonization**
(:func:`_count_alt_alleles`). 23andMe v3/v4/v5 raw exports report genotypes on
the **GRCh37 plus/forward strand** (per 23andMe's "Raw Genotype Data Technical
Details": genotypes refer to the plus strand of GRCh37, regardless of how dbSNP
defines the SNP). Therefore every defining ``{ref, alt}`` **must** be stored on
the GRCh37 plus strand, with ``alt`` equal to the plus-strand base produced by
the star-allele-*defining* variant (the base a carrier has) and ``ref`` the
plus-strand base of the normal/``*1`` allele.

For genes on the **minus strand** (CYP2D6, CYP3A5, DPYD, TPMT) the CPIC/PharmVar
cDNA defining change (e.g. ``c.2851C>T``) is the **reverse complement** of the
plus strand and must be complemented base-by-base (A<->T, C<->G) while
**preserving the ref/alt roles**. A historical bug stored several minus-strand
rows in cDNA orientation; on real plus-strand data the genotype bases then fell
outside ``{ref, alt}`` and the variant became silently **uncallable**
(a no-call), downgrading carriers to wild-type — a patient-safety defect on
fatal-toxicity (DPYD/5-FU), thiopurine (TPMT), tacrolimus (CYP3A5) and
CYP2D6 drugs.

THE TRAP (do not "fix" with genomic VCF orientation): at a few SNPs the GRCh37
reference genome itself carries the *variant/defining* allele (e.g. rs16947 ref
base = A = the ``*2`` allele; rs776746 ref base = C = the ``*3`` allele). A plain
genomic VCF lists REF=reference-genome-base, but using that as the engine
``alt`` is backwards and would call the star allele for ``*1`` carriers. The
engine ``alt`` must remain the *defining* base.

Each ``(ref, alt)`` below was verified against the **Ensembl GRCh37 REST API**
(forward-strand ``allele_string``), **dbSNP** RefSNP SPDI (plus strand of the
GRCh37 RefSeq contig), and the **PharmVar/CPIC** allele-definition cDNA changes.
This test pins those values so the strand bug cannot regress: any new
single-base defining variant must be added here (forcing the same verification)
or explicitly acknowledged in ``KNOWN_NON_SNV``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

# Production CPIC allele table consumed by the pharmacogenomics caller.
_CPIC_ALLELES_CSV = (
    Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic" / "cpic_alleles.csv"
)

# Verified GRCh37 plus/forward-strand (ref, alt) for every single-base SNV
# defining variant. alt = the base a carrier of the star allele has.
# Verified: Ensembl GRCh37 REST + dbSNP SPDI + PharmVar/CPIC cDNA definitions.
EXPECTED_PLUS_STRAND: dict[str, tuple[str, str]] = {
    # CYP2D6 (minus strand)
    "rs16947": ("G", "A"),  # *2  c.2851C>T; ref genome carries A (the *2 allele) — trap
    "rs3892097": ("C", "T"),  # *4  c.506-1G>A (minus-strand G>A -> plus C>T)
    "rs1065852": ("G", "A"),  # *10 c.100C>T
    "rs28371706": ("G", "A"),  # *17 c.320C>T
    "rs59421388": ("C", "T"),  # *29 c.1659G>A (minus-strand G>A -> plus C>T)
    "rs28371725": ("C", "T"),  # *41 c.985+39G>A (minus-strand G>A -> plus C>T)
    # CYP2C19 (plus strand — cDNA == plus)
    "rs4244285": ("G", "A"),  # *2  c.681G>A
    "rs4986893": ("G", "A"),  # *3  c.636G>A
    "rs28399504": ("A", "G"),  # *4  c.1A>G
    "rs12248560": ("C", "T"),  # *17 c.-806C>T
    # CYP2C9 (plus strand)
    "rs1799853": ("C", "T"),  # *2  c.430C>T
    "rs1057910": ("A", "C"),  # *3  c.1075A>C
    "rs28371686": ("C", "G"),  # *5  c.1080C>G
    "rs7900194": ("G", "A"),  # *8  c.449G>A
    "rs28371685": ("C", "T"),  # *11 c.1003C>T
    # CYP3A5 (minus strand)
    "rs776746": ("T", "C"),  # *3  c.219-237A>G; ref genome carries C (the *3 allele) — trap
    "rs10264272": ("C", "T"),  # *6  c.624G>A (minus-strand G>A -> plus C>T)
    # DPYD (minus strand) — fatal fluoropyrimidine toxicity gene
    "rs3918290": ("C", "T"),  # *2A c.1905+1G>A (minus-strand G>A -> plus C>T)
    "rs55886062": ("A", "C"),  # *13 c.1679T>G
    "rs67376798": ("T", "A"),  # c.2846A>T
    "rs75017182": ("G", "C"),  # HapB3 c.1129-5923C>G (strand-ambiguous G/C — orientation pinned)
    # TPMT (minus strand)
    "rs1800462": ("C", "G"),  # *2  c.238G>C
    "rs1800460": ("C", "T"),  # *3B/*3A c.460G>A
    "rs1142345": ("T", "C"),  # *3C/*3A c.719A>G
    # SLCO1B1 (plus strand)
    "rs2306283": ("A", "G"),  # *1B c.388A>G
    "rs4149056": ("T", "C"),  # *5  c.521T>C
    "rs4149015": ("G", "A"),  # *17 c.-910G>A
    # NUDT15 (plus strand) — thiopurine myelosuppression (SW-E1)
    "rs116855232": ("C", "T"),  # *3  c.415C>T (p.Arg139Cys); ref genome C = normal
    # UGT1A1 (plus strand) — irinotecan/atazanavir (SW-E1)
    "rs4148323": ("G", "A"),  # *6  c.211G>A (p.Gly71Arg); ref genome G = normal
    # (UGT1A1 *28 rs8175347 is a TATA-box TA-repeat, not an SNV — handled as indeterminate)
}

# Single-base rows that are NOT verifiable plus-strand SNVs and are intentionally
# excluded (documented known limitation, deferred — see PR). rs5030655 (CYP2D6*6)
# is biologically a 1-bp deletion (1707delT) but is stored as a placeholder SNV;
# it is already non-callable from array data and is not a fatal-toxicity gene.
KNOWN_NON_SNV: frozenset[str] = frozenset({"rs5030655"})


def _iter_defining_variants() -> list[tuple[str, str, str, str, str]]:
    """Yield (gene, allele_name, rsid, ref, alt) for each defining variant."""
    rows: list[tuple[str, str, str, str, str]] = []
    with _CPIC_ALLELES_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            variants = json.loads(row["defining_variants"]) if row["defining_variants"] else []
            for v in variants:
                rows.append((row["gene"], row["allele_name"], v["rsid"], v["ref"], v["alt"]))
    return rows


def _snv_defining_variants() -> list[tuple[str, str, str, str, str]]:
    """Single-base (SNV) defining variants only."""
    return [r for r in _iter_defining_variants() if len(r[3]) == 1 and len(r[4]) == 1]


def test_csv_exists() -> None:
    assert _CPIC_ALLELES_CSV.is_file(), f"missing CPIC allele table: {_CPIC_ALLELES_CSV}"


@pytest.mark.parametrize("gene,allele,rsid,ref,alt", _snv_defining_variants())
def test_snv_defining_variant_is_plus_strand(
    gene: str, allele: str, rsid: str, ref: str, alt: str
) -> None:
    """Every SNV defining variant must use the verified GRCh37 plus-strand ref/alt.

    A failure here means a star-allele defining variant is on the wrong strand
    (likely the cDNA/minus orientation), which makes the variant silently
    uncallable on real plus-strand 23andMe data and downgrades carriers to
    wild-type. Re-verify against Ensembl GRCh37 / dbSNP before changing the
    expected value — and never use plain genomic VCF orientation (see the trap
    note in this module's docstring).
    """
    if rsid in KNOWN_NON_SNV:
        pytest.skip(f"{rsid} is a documented non-SNV placeholder (see KNOWN_NON_SNV)")
    assert rsid in EXPECTED_PLUS_STRAND, (
        f"{gene}{allele} defining variant {rsid} is not in the verified plus-strand "
        f"table. Add it after verifying against Ensembl GRCh37 / dbSNP, or list it "
        f"in KNOWN_NON_SNV if it is not a plus-strand SNV."
    )
    expected = EXPECTED_PLUS_STRAND[rsid]
    assert (ref, alt) == expected, (
        f"{gene}{allele} {rsid}: stored {ref}>{alt} but the verified GRCh37 "
        f"plus-strand value is {expected[0]}>{expected[1]}. Storing the cDNA/minus "
        f"orientation makes this variant uncallable on real 23andMe data."
    )


def test_no_palindromic_ambiguity_unpinned() -> None:
    """Strand-ambiguous (A/T or C/G) SNVs are allowed only with a pinned orientation.

    The HapB3 DPYD variant rs75017182 is a G/C pair; the others are non-ambiguous.
    Each must still appear in EXPECTED_PLUS_STRAND so its orientation is explicit.
    """
    complement = {"A": "T", "T": "A", "C": "G", "G": "C"}
    for gene, allele, rsid, ref, alt in _snv_defining_variants():
        if rsid in KNOWN_NON_SNV:
            continue
        if complement[ref] == alt:  # palindromic / strand-ambiguous pair
            assert rsid in EXPECTED_PLUS_STRAND, (
                f"{gene}{allele} {rsid} ({ref}>{alt}) is strand-ambiguous and must "
                f"have its plus-strand orientation pinned in EXPECTED_PLUS_STRAND."
            )
