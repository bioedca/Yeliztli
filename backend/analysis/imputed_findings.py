"""Imputed common-variant ClinVar finding source (SW-C6 / roadmap #32).

Wave C uplift. Array genotyping misses many common, clinically catalogued loci
that simply are not on the chip; Wave C imputes them up to the 1000G panel
(SW-C1/C2) behind the SW-C3 MAF/r² firewall, and persists the firewall-cleared
imputed variants to ``imputed_variants`` (SW-C5 persist). This module turns those
*imputed common variants* into a **finding source**: where a firewall-cleared
imputed variant the individual carries sits at a ClinVar Pathogenic /
Likely-pathogenic locus the chip did **not** directly type, it surfaces a
finding — clearly labeled *imputed, not directly genotyped* and flagged for
clinical-grade confirmation.

**Safety posture (high-stakes — this changes what is surfaced to users).**

* **Firewall at the gate.** Every imputed variant is re-asserted against the SW-C3
  firewall via :func:`backend.analysis.finding_gate.imputed_variant_surfaceable`
  before it can back a finding (well-imputed ``DR2 >= 0.8`` **and** common
  ``MAF >= 1%``) — defense in depth over the persistence-time filter, so an imputed
  P/LP call can never rest on a rare or low-quality imputed dosage.
* **Carriage only.** The discrete FORMAT ``GT`` best-guess / MAP genotype supplies
  the ALT copy count for clinical carriage. The continuous ``DS`` dosage remains
  metadata (and PRS input), but it is not rounded into a clinical genotype because
  dosage is an expected ALT count and can disagree with the max-posterior call
  (Marchini & Howie 2010, PMID:20517342; accessed 2026-06-29). Only an individual
  carrying ≥ 1 copy of the ALT (= the ClinVar) allele gets a finding; hom-reference
  and missing-GT rows surface nothing.
* **Allele-specific ClinVar match.** The imputed ``(chrom, pos, ref, alt)`` must match
  a ClinVar record after reference-free minimal-representation normalization and, when
  a GRCh37 FASTA is configured, reference-aware indel left-alignment. The finding rests
  on *that* allele's classification — not a higher-star benign record at a multi-allelic
  site.
* **No duplication.** *Alleles* the chip directly typed are excluded — the typed
  generators (rare_variant_finder / carrier_status / cardiovascular) already own
  those; this layer only fills chip gaps. The exclusion is allele-specific, so a
  *different* imputed ALT that exactly matches a separate ClinVar P/LP record still
  surfaces even when the chip typed another allele at the same coordinate (#1187).
* **Lower confidence by construction.** Imputed P/LP findings are capped at
  evidence level 2 (:data:`IMPUTED_EVIDENCE_CAP`) so they never headline the
  high-confidence (≥ 3★) set, and every finding carries a confirm-clinically caveat.

**Graceful degradation.** A sample with no imputation (no ``imputed_variants``
table, or an empty one) yields zero findings — byte-identical to not running this
module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import sqlalchemy as sa
import structlog

from backend.analysis.clinvar_significance import primary_pathogenic_classification
from backend.analysis.evidence import assign_clinvar_evidence_level
from backend.analysis.finding_gate import imputed_variant_surfaceable
from backend.analysis.imputation_input import AUTOSOMAL_INPUT_CHROMOSOMES
from backend.analysis.imputation_runner import ImputedVariant
from backend.db.tables import annotated_variants, clinvar_variants, findings, imputed_variants

logger = structlog.get_logger(__name__)

AlleleKey = tuple[str | None, int, str, str]


class _ReferenceSequence(Protocol):
    """Minimal random-access reference sequence interface used for indel alignment."""

    def fetch(self, chrom: str, start: int, end: int) -> str:
        """Return ``chrom[start:end]`` using 0-based, half-open coordinates."""
        ...

    def get_reference_length(self, chrom: str) -> int:
        """Return the reference length for ``chrom``."""
        ...


class _PysamReferenceSequence:
    """Small adapter over ``pysam.FastaFile`` with chromosome alias handling."""

    def __init__(self, fasta_path: Path) -> None:
        import pysam

        self._fasta = pysam.FastaFile(str(fasta_path))
        self._references = set(self._fasta.references)

    def close(self) -> None:
        self._fasta.close()

    def _reference_name(self, chrom: str) -> str:
        norm = _norm_chrom(chrom) or str(chrom).strip()
        candidates = [str(chrom).strip(), norm, f"chr{norm}"]
        if norm == "MT":
            candidates.extend(["M", "chrM", "chrMT"])
        for candidate in candidates:
            if candidate in self._references:
                return candidate
        raise KeyError(f"chromosome {chrom!r} not found in reference FASTA")

    def fetch(self, chrom: str, start: int, end: int) -> str:
        return self._fasta.fetch(self._reference_name(chrom), start, end).upper()

    def get_reference_length(self, chrom: str) -> int:
        return self._fasta.get_reference_length(self._reference_name(chrom))


def _open_reference_sequence(
    reference_fasta_path: str | Path | None,
) -> _PysamReferenceSequence | None:
    """Open an indexed GRCh37 FASTA, returning ``None`` when unavailable."""
    if reference_fasta_path is None:
        return None
    path = Path(reference_fasta_path)
    if not path.exists():
        logger.warning("imputed_clinvar_reference_fasta_missing", path=str(path))
        return None
    try:
        return _PysamReferenceSequence(path)
    except Exception as exc:  # pragma: no cover - exact pysam exception varies by build.
        logger.warning(
            "imputed_clinvar_reference_fasta_unavailable",
            path=str(path),
            error=str(exc),
        )
        return None


# ``findings.module`` value for this source.
IMPUTED_MODULE = "imputed_variants"
_DIPLOID_IMPUTED_FINDING_CHROMOSOMES = frozenset(AUTOSOMAL_INPUT_CHROMOSOMES)
# ``findings.category`` for an imputed common variant matching a ClinVar P/LP record.
IMPUTED_CLINVAR_PATHOGENIC_CATEGORY = "imputed_clinvar_pathogenic"
# Imputed P/LP calls are statistically inferred, not directly observed, so they are
# capped below the high-confidence (≥3-star) headline tier regardless of the ClinVar
# review-star evidence the same variant would earn if directly typed: imputation is
# reliable for common, well-imputed variants, but an imputed best-guess GT call is
# still an inference. The firewall + this cap + the caveat keep it appropriately framed.
IMPUTED_EVIDENCE_CAP = 2
# Disclosure carried with every imputed clinical finding. Statistically imputed variants
# entering clinical interpretation have low positive predictive value and must be
# confirmed by orthogonal clinical-grade genotyping (e.g. Sanger) before any medical use
# — false positives from raw consumer/array calls are common and require clinical
# confirmation (Tandy-Connor 2018, PMID:29565420), the same caveat the SW-C3 firewall
# cites by analogy from direct array genotyping (Weedon 2021, PMID:33589468; accessed
# 2026-06-27). Imported by the SW-C6 frontend-parity test so the disclosure can't
# silently drift between backend and UI.
IMPUTED_CONFIRMATION_CAVEAT = (
    "Imputed (statistically inferred from a reference panel), not directly "
    "genotyped — confirm with clinical-grade testing before any medical decision."
)
IMPUTED_CONFIRMATION_PMIDS: tuple[str, ...] = ("29565420", "33589468")


def _norm_chrom(chrom: str | None) -> str | None:
    """Normalize a chromosome label for positional matching (strip ``chr``, upper).

    Mirrors :func:`backend.analysis.prs._norm_chrom` and ClinVar ingest's
    ``_normalize_chrom`` so ``imputed_variants`` / ``annotated_variants`` /
    ``clinvar_variants`` chromosome labels compare equal regardless of ``chr`` prefix.
    """
    if chrom is None:
        return None
    c = str(chrom).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    return c.upper()


def _allele_text(allele: str | None) -> str:
    """Uppercase allele text, using ``""`` for empty/deletion sentinels."""
    text = (allele or "").strip().upper()
    return "" if text in {"", "-"} else text


def _has_specific_allele(allele: str | None) -> bool:
    """Return whether a typed row allele can identify a concrete variant allele."""
    if allele is None:
        return False
    return str(allele).strip() not in {"", "."}


def _raw_allele_key(
    chrom: str | None,
    pos: int,
    ref: str | None,
    alt: str | None,
) -> AlleleKey:
    """Return the source allele key with chromosome/text normalization only."""
    return (_norm_chrom(chrom), int(pos), _allele_text(ref), _allele_text(alt))


@dataclass(frozen=True)
class ImputedClinVarFinding:
    """One firewall-cleared imputed common variant matching a ClinVar P/LP record."""

    chrom: str
    pos: int
    ref: str
    alt: str
    dr2: float
    af: float
    dosage: float | None
    copies: int  # best-guess ALT copy count (1 = het, 2 = hom-alt)
    zygosity: str  # "het" | "hom_alt"
    rsid: str | None
    gene_symbol: str | None
    clinvar_significance: str
    clinvar_review_stars: int
    clinvar_accession: str | None
    clinvar_conditions: str | None
    evidence_level: int


def _load_carried_imputed_variants(
    sample_engine: sa.Engine,
) -> list[tuple[ImputedVariant, int]]:
    """Firewall-cleared autosomal imputed variants carried as ``(variant, copies)``.

    Graceful degradation: returns ``[]`` when ``imputed_variants`` is absent (a sample
    DB predating Wave C) or empty (no imputation persisted) — so this whole module is a
    no-op there, byte-identical to not running it. Rows with a missing best-guess
    genotype, or with best-guess hom-reference ``GT`` (``best_guess_copies == 0``),
    carry nothing and are dropped. ``DS`` is retained as dosage metadata only; it is
    never rounded into a clinical genotype. Chromosome X is suppressed until this
    finding source can carry ploidy/sex and label hemizygous calls correctly. The
    SW-C3 firewall is re-asserted at the gate over each surviving row (defense in
    depth).
    """
    with sample_engine.connect() as conn:
        inspector = sa.inspect(conn)
        if not inspector.has_table(imputed_variants.name):
            return []
        existing_cols = {c["name"] for c in inspector.get_columns(imputed_variants.name)}
        if "best_guess_copies" not in existing_cols:
            logger.info("imputed_variants_best_guess_copies_missing")
            return []
        rows = conn.execute(
            sa.select(
                imputed_variants.c.chrom,
                imputed_variants.c.pos,
                imputed_variants.c.ref,
                imputed_variants.c.alt,
                imputed_variants.c.dr2,
                imputed_variants.c.af,
                imputed_variants.c.dosage,
                imputed_variants.c.best_guess_copies,
            )
        ).fetchall()

    carried: list[tuple[ImputedVariant, int]] = []
    for r in rows:
        if _norm_chrom(r.chrom) not in _DIPLOID_IMPUTED_FINDING_CHROMOSOMES:
            continue
        if r.best_guess_copies is None:
            continue
        try:
            copies = int(r.best_guess_copies)
        except (TypeError, ValueError):
            continue
        if copies not in (1, 2):
            continue  # best-guess hom-reference: not a carrier of the ALT allele
        variant = ImputedVariant(
            chrom=r.chrom,
            pos=r.pos,
            ref=r.ref,
            alt=r.alt,
            dr2=r.dr2,
            af=r.af,
            imputed=True,
            dosage=r.dosage,
            best_guess_copies=copies,
        )
        if not imputed_variant_surfaceable(variant):
            continue  # firewall at the gate (defense in depth over persistence)
        carried.append((variant, copies))
    return carried


def _minimal_repr(chrom: str | None, pos: int, ref: str | None, alt: str | None) -> AlleleKey:
    """Reduce an allele to its parsimonious minimal representation for comparison.

    The same biological indel can be written several valid ways in VCF (different
    anchor base, trailing context); the array-, imputation-, and ClinVar-derived
    tables come from independent pipelines and need not agree. This trims the bases
    REF and ALT share — right-most context bases first (position-preserving), then
    left-most anchor bases (advancing ``pos``) — the reference-free half of variant
    normalization (Tan et al. 2015, PMID:25701572, DOI:10.1093/bioinformatics/btv112).

    **SNVs are returned unchanged** (single-base alleles have nothing to trim), so
    this is a no-op on this module's SNV-dominated traffic; it only collapses the
    differing-anchor / trailing-context indel spellings two sources can disagree on
    (issues #1218/#1252). It does **not** left-align across a repeat (that needs the
    reference sequence), so equivalence is resolved only up to parsimony.
    """
    pos = int(pos)
    ref = _allele_text(ref)
    alt = _allele_text(alt)
    if not ref or not alt:
        return (chrom, pos, ref, alt)
    # Trim shared trailing context, preserving pos, while both alleles keep >=1 base.
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    # Trim shared anchor bases, advancing pos, while both alleles keep >=1 base.
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    return (chrom, pos, ref, alt)


def _empty_allele_repr(chrom: str | None, pos: int, ref: str | None, alt: str | None) -> AlleleKey:
    """Return an internal key that can compare padded VCF alleles to ``-`` sentinels."""
    pos = int(pos)
    ref = _allele_text(ref)
    alt = _allele_text(alt)
    if not ref or not alt:
        return (chrom, pos, ref, alt)
    while ref and alt and ref[0] == alt[0] and (len(ref) > 1 or len(alt) > 1):
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    while ref and alt and ref[-1] == alt[-1] and (len(ref) > 1 or len(alt) > 1):
        ref, alt = ref[:-1], alt[:-1]
    return (chrom, pos, ref, alt)


def _reference_fetch(
    reference_sequence: _ReferenceSequence, chrom: str, start: int, end: int
) -> str | None:
    """Fetch uppercase reference sequence, returning ``None`` on invalid access."""
    if start < 0 or end < start:
        return None
    try:
        seq = reference_sequence.fetch(chrom, start, end).upper()
    except (KeyError, IndexError, OSError, ValueError):
        return None
    return seq if len(seq) == end - start else None


def _reference_length(reference_sequence: _ReferenceSequence, chrom: str) -> int | None:
    """Return reference length, or ``None`` when the contig is unavailable."""
    try:
        return int(reference_sequence.get_reference_length(chrom))
    except (KeyError, IndexError, OSError, ValueError):
        return None


def _left_aligned_repr(
    chrom: str | None,
    pos: int,
    ref: str | None,
    alt: str | None,
    reference_sequence: _ReferenceSequence | None,
) -> AlleleKey | None:
    """Return a reference-aware left-aligned indel key.

    The reference-free key trims shared context but cannot shift equivalent indels
    through repeats. With a GRCh37 FASTA, represent the indel as an empty-ref or
    empty-alt change and rotate it left while the previous reference base permits an
    equivalent representation. Deletions are validated against the reference before
    they can produce a key.
    """
    if reference_sequence is None:
        return None
    chrom, pos, ref, alt = _empty_allele_repr(chrom, pos, ref, alt)
    if chrom is None or not (ref or alt) or (ref and alt):
        return None

    allele = ref or alt
    contig_len = _reference_length(reference_sequence, chrom)
    if contig_len is None or pos < 1:
        return None
    if ref:
        if pos + len(allele) - 1 > contig_len:
            return None
        observed = _reference_fetch(reference_sequence, chrom, pos - 1, pos - 1 + len(allele))
        if observed != allele:
            return None
    elif pos > contig_len + 1:
        return None

    while pos > 1:
        prev_base = _reference_fetch(reference_sequence, chrom, pos - 2, pos - 1)
        if prev_base != allele[-1]:
            break
        allele = prev_base + allele[:-1]
        pos -= 1

    return (chrom, pos, allele if ref else "", "" if ref else allele)


def _right_shifted_indel_positions(
    left_key: AlleleKey, reference_sequence: _ReferenceSequence | None
) -> set[tuple[str | None, int]]:
    """All equivalent empty-allele indel positions reachable by right-shifting."""
    if reference_sequence is None:
        return set()
    chrom, pos, ref, alt = left_key
    if chrom is None or not (ref or alt) or (ref and alt):
        return set()
    contig_len = _reference_length(reference_sequence, chrom)
    if contig_len is None:
        return set()

    allele = ref or alt
    is_deletion = bool(ref)
    positions: set[tuple[str | None, int]] = set()
    while True:
        positions.add((chrom, pos))
        next_pos = pos + len(allele) if is_deletion else pos
        if next_pos < 1 or next_pos > contig_len:
            break
        next_base = _reference_fetch(reference_sequence, chrom, next_pos - 1, next_pos)
        if next_base != allele[0]:
            break
        allele = allele[1:] + next_base
        pos += 1
    return positions


def _allele_match_keys(
    chrom: str | None,
    pos: int,
    ref: str | None,
    alt: str | None,
    reference_sequence: _ReferenceSequence | None = None,
) -> set[AlleleKey]:
    """Return all allele keys used for typed/ClinVar comparison."""
    keys = {
        _minimal_repr(chrom, pos, ref, alt),
        _empty_allele_repr(chrom, pos, ref, alt),
    }
    left_key = _left_aligned_repr(chrom, pos, ref, alt, reference_sequence)
    if left_key is not None:
        keys.add(left_key)
    return keys


def _query_positions_for_key(
    allele_key: AlleleKey, reference_sequence: _ReferenceSequence | None
) -> set[tuple[str | None, int]]:
    """ClinVar positions worth fetching for one normalized allele key."""
    chrom, pos, ref, alt = allele_key
    positions: set[tuple[str | None, int]] = {(chrom, pos)}
    if "" in (ref, alt):
        left_anchor_pos = pos - 1
        if left_anchor_pos > 0:
            positions.add((chrom, left_anchor_pos))
    for shifted_chrom, shifted_pos in _right_shifted_indel_positions(
        allele_key, reference_sequence
    ):
        positions.add((shifted_chrom, shifted_pos))
        left_anchor_pos = shifted_pos - 1
        if left_anchor_pos > 0:
            positions.add((shifted_chrom, left_anchor_pos))
    return positions


def _typed_alleles(
    sample_engine: sa.Engine, reference_sequence: _ReferenceSequence | None = None
) -> set[AlleleKey]:
    """Minimal-representation ``(norm_chrom, pos, ref, alt)`` the chip directly typed.

    Imputed findings only fill chip gaps, so an allele already in ``annotated_variants``
    — owned by the directly-typed finding generators — is excluded to avoid duplicating
    or conflicting with a typed finding for *that same allele*.

    The match is allele-specific, **not coordinate-only**. ClinVar records clinical
    significance for specific variant interpretations, not for coordinates (Landrum 2016,
    PMID:26582918, DOI:10.1093/nar/gkv1222), and this module already rests every finding
    on an exact ``(chrom, pos, ref, alt)`` ClinVar match. At a multi-allelic locus the
    typed generators own only the allele the chip actually typed; a *different* imputed
    ALT that exactly matches a separate ClinVar P/LP record is a genuine chip gap, not a
    duplicate — so a typed allele must not suppress a different imputed allele that merely
    shares its coordinate (issue #1187). ``ref``/``alt`` are nullable in
    ``annotated_variants``; a row missing either, or carrying a dot allele, cannot claim
    a specific allele and is not allowed to exclude an imputed finding.

    Keys are reduced to their :func:`_minimal_repr` and, when a GRCh37 FASTA is
    available, reference-aware left-aligned keys, so a typed indel still excludes an
    imputed candidate written with a different (but equivalent) anchor/trailing context
    or repeat-shifted position (issues #1218/#1252); the imputed candidate is
    normalized the same way at the exclusion check.
    """
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                annotated_variants.c.chrom,
                annotated_variants.c.pos,
                annotated_variants.c.ref,
                annotated_variants.c.alt,
            )
        ).fetchall()
    keys: set[AlleleKey] = set()
    for r in rows:
        if _has_specific_allele(r.ref) and _has_specific_allele(r.alt):
            keys.update(
                _allele_match_keys(_norm_chrom(r.chrom), r.pos, r.ref, r.alt, reference_sequence)
            )
    return keys


def find_imputed_clinvar_findings(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    *,
    reference_fasta_path: str | Path | None = None,
    reference_sequence: _ReferenceSequence | None = None,
) -> list[ImputedClinVarFinding]:
    """Surface firewall-cleared imputed common variants at ClinVar P/LP loci.

    For each imputed variant the individual carries (at an allele the chip did **not**
    directly type), look up ClinVar by allele after reference-free normalization and,
    when a FASTA is provided, reference-aware left alignment. If the record's primary
    classification is (Likely) Pathogenic, emit a finding labeled imputed-not-typed.
    Lower-penetrance / risk-allele and "Conflicting classifications" records are
    excluded — they are not ordinary high-penetrance P/LP
    (:mod:`backend.analysis.clinvar_significance`). Returns an empty list when no
    imputation has been persisted (graceful degradation).
    """
    opened_reference = None
    if reference_sequence is None:
        opened_reference = _open_reference_sequence(reference_fasta_path)
        reference_sequence = opened_reference

    try:
        carried = _load_carried_imputed_variants(sample_engine)
        if not carried:
            return []

        typed = _typed_alleles(sample_engine, reference_sequence)

        # Index carried imputed variants by exact allele key; drop chip-typed alleles.
        # The drop is allele-specific: only an imputed candidate whose exact
        # ``(chrom, pos, ref, alt)`` was directly typed is excluded, so a different ALT
        # at a coordinate the chip typed for another allele still surfaces (issue #1187).
        # Compare minimal and, when available, left-aligned representations so
        # anchor/trailing-context and repeat-shift differences do not turn a typed indel
        # into a duplicate imputed finding (issue #1218), and do not make the downstream
        # ClinVar match a false negative when ClinVar uses the paired spelling (issue
        # #1252). The original imputed ref/alt remain on the emitted finding.
        by_allele: dict[AlleleKey, tuple[ImputedVariant, int]] = {}
        query_positions: set[tuple[str | None, int]] = set()
        for variant, copies in carried:
            raw_key = _raw_allele_key(variant.chrom, variant.pos, variant.ref, variant.alt)
            allele_keys = _allele_match_keys(*raw_key, reference_sequence)
            if typed.intersection(allele_keys):
                continue
            query_positions.add((raw_key[0], raw_key[1]))
            for allele_key in allele_keys:
                by_allele.setdefault(allele_key, (variant, copies))
                query_positions.update(_query_positions_for_key(allele_key, reference_sequence))
        if not by_allele:
            return []

        positions = sorted(query_positions)

        results: list[ImputedClinVarFinding] = []
        seen: set[AlleleKey] = set()
        with reference_engine.connect() as conn:
            # Batch the (chrom, pos) lookups under SQLite's bound-variable limit,
            # mirroring backend.annotation.clinvar.lookup_clinvar_by_positions.
            for start in range(0, len(positions), 250):
                batch = positions[start : start + 250]
                conditions = [
                    sa.and_(clinvar_variants.c.chrom == chrom, clinvar_variants.c.pos == pos)
                    for chrom, pos in batch
                ]
                stmt = sa.select(
                    clinvar_variants.c.chrom,
                    clinvar_variants.c.pos,
                    clinvar_variants.c.ref,
                    clinvar_variants.c.alt,
                    clinvar_variants.c.rsid,
                    clinvar_variants.c.gene_symbol,
                    clinvar_variants.c.significance,
                    clinvar_variants.c.review_stars,
                    clinvar_variants.c.accession,
                    clinvar_variants.c.conditions,
                ).where(sa.or_(*conditions))
                for row in conn.execute(stmt).fetchall():
                    # Only ordinary high-penetrance P/LP (excludes Conflicting and the
                    # lower-penetrance / risk-allele tier).
                    if primary_pathogenic_classification(row.significance) is None:
                        continue
                    match = None
                    for key in _allele_match_keys(
                        _norm_chrom(row.chrom),
                        row.pos,
                        row.ref,
                        row.alt,
                        reference_sequence,
                    ):
                        match = by_allele.get(key)
                        if match is not None:
                            break
                    if match is None:
                        continue
                    variant, copies = match
                    seen_key = _raw_allele_key(
                        variant.chrom, variant.pos, variant.ref, variant.alt
                    )
                    if seen_key in seen:
                        continue
                    seen.add(seen_key)
                    stars = row.review_stars or 0
                    evidence_level = min(
                        assign_clinvar_evidence_level(row.significance, stars),
                        IMPUTED_EVIDENCE_CAP,
                    )
                    results.append(
                        ImputedClinVarFinding(
                            chrom=variant.chrom,
                            pos=variant.pos,
                            ref=variant.ref,
                            alt=variant.alt,
                            dr2=variant.dr2 if variant.dr2 is not None else 0.0,
                            af=variant.af if variant.af is not None else 0.0,
                            dosage=variant.dosage,
                            copies=copies,
                            zygosity="hom_alt" if copies >= 2 else "het",
                            rsid=row.rsid,
                            gene_symbol=row.gene_symbol,
                            clinvar_significance=row.significance,
                            clinvar_review_stars=stars,
                            clinvar_accession=row.accession,
                            clinvar_conditions=row.conditions,
                            evidence_level=evidence_level,
                        )
                    )

        results.sort(key=lambda f: (_norm_chrom(f.chrom) or "", f.pos))
        logger.info(
            "imputed_clinvar_findings_found",
            carried_imputed=len(carried),
            candidates=len(by_allele),
            pathogenic=len(results),
            reference_aligned=reference_sequence is not None,
        )
        return results
    finally:
        if opened_reference is not None:
            opened_reference.close()


def store_imputed_findings(
    results: list[ImputedClinVarFinding],
    sample_engine: sa.Engine,
) -> int:
    """Store imputed ClinVar findings (``module='imputed_variants'``); returns the count.

    Replaces any prior rows for this module so a re-run reflects the latest imputation
    rather than accumulating stale findings. With an empty ``results`` the prior rows
    are still cleared (and nothing inserted), so a sample that loses its imputation does
    not retain stale imputed findings.
    """
    rows: list[dict] = []
    for f in results:
        locus = f.rsid if f.rsid else f"{f.chrom}:{f.pos} {f.ref}>{f.alt}"
        gene_text = f.gene_symbol or "intergenic"
        finding_text = (
            f"{gene_text} {locus} — ClinVar {f.clinvar_significance} "
            f"({f.zygosity}). {IMPUTED_CONFIRMATION_CAVEAT}"
        )
        detail = {
            "imputed": True,
            "matched_by": "imputed_position_allele",
            "ref": f.ref,
            "alt": f.alt,
            "dr2": f.dr2,
            "af": f.af,
            "dosage": f.dosage,
            "copies": f.copies,
            "clinvar_accession": f.clinvar_accession,
            "clinvar_review_stars": f.clinvar_review_stars,
            "confirmation_caveat": IMPUTED_CONFIRMATION_CAVEAT,
        }
        rows.append(
            {
                "module": IMPUTED_MODULE,
                "category": IMPUTED_CLINVAR_PATHOGENIC_CATEGORY,
                "evidence_level": f.evidence_level,
                "gene_symbol": f.gene_symbol,
                "rsid": f.rsid,
                "finding_text": finding_text,
                "conditions": f.clinvar_conditions,
                "zygosity": f.zygosity,
                "clinvar_significance": f.clinvar_significance,
                "pmid_citations": json.dumps(list(IMPUTED_CONFIRMATION_PMIDS)),
                "detail_json": json.dumps(detail),
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(sa.delete(findings).where(findings.c.module == IMPUTED_MODULE))
        if not rows:
            logger.info("no_imputed_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

    logger.info("imputed_findings_stored", count=len(rows))
    return len(rows)
