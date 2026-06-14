"""GRCh38 liftover integration (P4-19).

Converts GRCh37 (hg19) genomic coordinates to GRCh38 (hg38) using pyliftover.

The hg19→hg38 chain file is vendored in-repo at ``backend/data/chains/`` and
loaded directly, so liftover never touches the network. pyliftover's default
behaviour (``LiftOver("hg19", "hg38")``) would download the chain from UCSC on
first use, which made CI flaky when that fetch failed; loading the bundled file
keeps tests offline/deterministic and avoids a first-run download in production.
A network fetch remains only as a fallback if the vendored file is ever missing.

Lifted coordinates are stored as parallel columns (chrom_grch38, pos_grch38) in
the annotated_variants table — the primary (chrom, pos) columns remain GRCh37.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from pyliftover import LiftOver

logger = logging.getLogger(__name__)

# Vendored UCSC hg19→hg38 chain (~222 KB). See backend/data/chains/README.md
# for provenance and refresh instructions.
_CHAIN_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "chains" / "hg19ToHg38.over.chain.gz"
)

# Vendored UCSC hg18→hg19 chain (~140 KB) — converts NCBI build 36 (23andMe v3)
# to GRCh37 at ingest (#562). See backend/data/chains/README.md for provenance.
_CHAIN_PATH_HG18 = (
    Path(__file__).resolve().parent.parent / "data" / "chains" / "hg18ToHg19.over.chain.gz"
)

# Thread-safe singletons for the LiftOver instances (chain files are small,
# loaded once and reused across all liftover calls).
_lock = threading.Lock()
_liftover: LiftOver | None = None
_lock_hg18 = threading.Lock()
_liftover_hg18: LiftOver | None = None

# Plus-strand base complement. Only A/C/G/T (both cases) are translated; indel
# tokens (I/D), no-calls (-, 0) and 23andMe internal markers pass through
# unchanged — they must NOT be complemented.
_BASE_COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")
_ACGT = frozenset("ACGT")


def _get_liftover() -> LiftOver:
    """Return (or lazily initialise) the hg19→hg38 LiftOver instance.

    Loads the vendored chain file directly (no network). Falls back to
    pyliftover's UCSC download only if the bundled file is missing, logging a
    warning since that reintroduces the network dependency the vendored file
    exists to remove.
    """
    global _liftover
    with _lock:
        if _liftover is None:
            if _CHAIN_PATH.exists():
                logger.info(
                    "liftover_init",
                    extra={"from": "hg19", "to": "hg38", "source": "vendored"},
                )
                _liftover = LiftOver(str(_CHAIN_PATH))
            else:
                logger.warning(
                    "liftover_chain_missing_fallback_to_web",
                    extra={"expected_path": str(_CHAIN_PATH)},
                )
                _liftover = LiftOver("hg19", "hg38")
    return _liftover


def convert_coordinate(
    chrom: str,
    pos: int,
) -> tuple[str, int] | None:
    """Convert a single GRCh37 coordinate to GRCh38.

    Args:
        chrom: Chromosome name (e.g. "1", "X", "MT"). The ``chr`` prefix is
            added automatically if missing (pyliftover requires UCSC-style names).
        pos: 0-based or 1-based GRCh37 position. pyliftover uses 0-based
            coordinates internally; 23andMe positions are 1-based, so we
            convert to 0-based before the call and back to 1-based on return.

    Returns:
        Tuple of ``(chrom_grch38, pos_grch38)`` with 1-based position and
        chromosome name without ``chr`` prefix (matching our internal convention),
        or ``None`` if the coordinate could not be lifted over (e.g. the region
        was deleted/rearranged in GRCh38, or a mitochondrial input — see below).

    Mitochondrial inputs (``MT`` / ``chrM``) always return ``None`` (F34): UCSC
    hg19 ``chrM`` is the old Yoruba reference sequence, **not** rCRS — the build
    the chip data uses — so the hg19→hg38 chain lifts MT positions to wrong
    GRCh38 coordinates (e.g. 263→deleted, 750→748). Refusing to lift is correct
    here; emitting a bogus coordinate would silently corrupt downstream joins.
    """
    clean = chrom.removeprefix("chr")

    # MT short-circuit (F34): the vendored hg19→hg38 chain's chrM is Yoruba, not
    # rCRS, so any lifted mitochondrial coordinate is wrong. Decline to lift.
    if clean in ("MT", "M"):
        return None

    lo = _get_liftover()

    # pyliftover requires UCSC-style "chr"-prefixed names.
    ucsc_chrom = f"chr{clean}"

    # pyliftover uses 0-based coordinates; our positions are 1-based
    results = lo.convert_coordinate(ucsc_chrom, pos - 1)

    if not results:
        return None

    # Take the best (first) result
    new_chrom, new_pos_0based, _strand, _score = results[0]

    # Strip "chr" prefix for internal consistency. MT is short-circuited above,
    # and no autosomal/sex input lifts to chrM, so no chrM→MT remap is needed.
    out_chrom = new_chrom.removeprefix("chr")

    # Convert back to 1-based
    return (out_chrom, new_pos_0based + 1)


def _get_liftover_hg18() -> LiftOver:
    """Return (or lazily initialise) the hg18→hg19 LiftOver instance.

    Mirrors :func:`_get_liftover`: loads the vendored hg18→hg19 chain directly
    (no network), falling back to pyliftover's UCSC download only if the bundled
    file is missing.
    """
    global _liftover_hg18
    with _lock_hg18:
        if _liftover_hg18 is None:
            if _CHAIN_PATH_HG18.exists():
                logger.info(
                    "liftover_init",
                    extra={"from": "hg18", "to": "hg19", "source": "vendored"},
                )
                _liftover_hg18 = LiftOver(str(_CHAIN_PATH_HG18))
            else:
                logger.warning(
                    "liftover_hg18_chain_missing_fallback_to_web",
                    extra={"expected_path": str(_CHAIN_PATH_HG18)},
                )
                _liftover_hg18 = LiftOver("hg18", "hg19")
    return _liftover_hg18


def lift_build36_to_grch37(
    chrom: str,
    pos: int,
    genotype: str,
) -> tuple[str, int, str] | None:
    """Lift a single NCBI build-36 (hg18) variant to GRCh37 (hg19).

    23andMe v3 exports are build 36, but every position-dependent path in the
    pipeline assumes the stored ``(chrom, pos)`` are GRCh37 (#480/#562). hg18→hg19
    conversion is chain-driven and **non-uniform**: positions shift by varying
    amounts, a few even change chromosome, and on inverted/rearranged segments the
    target strand flips (Ormond 2021, *Brief Bioinform*; Sheng 2022, *HGG Adv* —
    inverted regions cause allelic-conversion errors if strand is ignored).

    When the lifted strand is ``-``, the genotype's A/C/G/T alleles are
    **complemented** so the stored call stays plus-strand-relative on GRCh37
    (otherwise positional PRS joins, which expect plus-strand GRCh37 effect
    alleles, silently mismatch). Indel tokens (``I``/``D``), no-calls and 23andMe
    internal ``i``-marker genotypes are passed through unchanged — they must not
    be complemented. Palindromic genotypes (A/T, C/G) are naturally invariant
    under complement, so no special-casing is needed here.

    Args:
        chrom: build-36 chromosome ("1".."22", "X", "Y", "MT"); ``chr`` optional.
        pos: 1-based build-36 position.
        genotype: the canonical called genotype (e.g. ``"AG"``, ``"II"``, ``"--"``,
            or a single char for haploid X/Y).

    Returns:
        ``(chrom_grch37, pos_grch37, genotype_grch37)`` with a 1-based position and
        no ``chr`` prefix, or ``None`` if the variant does not lift — deleted or
        rearranged out in hg19, unknown chromosome, or mitochondrial (see below).

    Mitochondrial inputs return ``None``: UCSC hg18 ``chrM`` is the old (non-rCRS)
    reference, so a lifted MT coordinate would be wrong — the same rationale that
    makes :func:`convert_coordinate` decline MT for hg19→hg38. 23andMe MT calls are
    matched by rsID/position downstream, not via this positional lift.
    """
    clean = chrom.removeprefix("chr")

    # MT short-circuit: hg18 chrM is not rCRS, so any lifted MT coordinate is wrong.
    if clean in ("MT", "M"):
        return None

    lo = _get_liftover_hg18()
    ucsc_chrom = f"chr{clean}"

    # pyliftover uses 0-based coordinates; our positions are 1-based.
    results = lo.convert_coordinate(ucsc_chrom, pos - 1)
    if not results:
        return None

    # Take the best (first) result: (chrom, pos_0based, strand, score).
    new_chrom, new_pos_0based, strand, _score = results[0]
    out_chrom = new_chrom.removeprefix("chr")

    out_genotype = genotype
    if strand == "-":
        # Complement A/C/G/T alleles so the call stays plus-strand-relative on
        # GRCh37, then restore the parser's canonical uppercased+sorted-pair form.
        # str.translate preserves allele order, so a sorted het "AG" would
        # otherwise become "TC" rather than the canonical "CT" — silently breaking
        # sorted-pair lookups for exactly these minus-strand v3 calls. Only re-sort
        # 2-char A/C/G/T pairs; indel tokens (I/D), no-calls and single-char
        # haploid calls are left as-is (their set is not ⊆ {A,C,G,T}).
        out_genotype = genotype.translate(_BASE_COMPLEMENT)
        if len(out_genotype) == 2 and set(out_genotype) <= _ACGT:
            out_genotype = "".join(sorted(out_genotype))

    return (out_chrom, new_pos_0based + 1, out_genotype)


def batch_convert(
    variants: list[tuple[str, str, int]],
) -> dict[str, tuple[str, int] | None]:
    """Batch convert GRCh37 coordinates to GRCh38.

    Args:
        variants: List of ``(rsid, chrom, pos)`` tuples.

    Returns:
        Dict mapping rsid → ``(chrom_grch38, pos_grch38)`` or ``None`` if
        the coordinate could not be lifted.
    """
    results: dict[str, tuple[str, int] | None] = {}
    converted = 0
    failed = 0

    for rsid, chrom, pos in variants:
        result = convert_coordinate(chrom, pos)
        results[rsid] = result
        if result is not None:
            converted += 1
        else:
            failed += 1

    logger.info(
        "liftover_batch_complete",
        extra={
            "total": len(variants),
            "converted": converted,
            "failed": failed,
        },
    )
    return results


def reset_liftover() -> None:
    """Reset the cached LiftOver instances (for testing)."""
    global _liftover, _liftover_hg18
    with _lock:
        _liftover = None
    with _lock_hg18:
        _liftover_hg18 = None
