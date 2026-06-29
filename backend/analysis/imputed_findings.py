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
* **Carriage only.** The continuous Beagle dosage (ALT dose 0–2) is hard-called to
  a best-guess copy count by rounding — the standard best-guess genotype, which for a
  well-imputed (``R² > 0.8``) variant coincides with the max-posterior call (Naj 2019,
  DOI:10.1002/cphg.84; Marchini & Howie 2010, PMID:20517342; accessed 2026-06-27).
  Only an individual carrying ≥ 1 copy of the ALT (= the ClinVar) allele gets a
  finding; hom-reference and missing-dosage rows surface nothing.
* **Exact-allele match.** The imputed ``(chrom, pos, ref, alt)`` must match a
  ClinVar record exactly, so the finding rests on *that* allele's classification —
  not a higher-star benign record at a multi-allelic site.
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

import sqlalchemy as sa
import structlog

from backend.analysis.clinvar_significance import primary_pathogenic_classification
from backend.analysis.evidence import assign_clinvar_evidence_level
from backend.analysis.finding_gate import imputed_variant_surfaceable
from backend.analysis.imputation_runner import ImputedVariant
from backend.db.tables import annotated_variants, clinvar_variants, findings, imputed_variants

logger = structlog.get_logger(__name__)

# ``findings.module`` value for this source.
IMPUTED_MODULE = "imputed_variants"
# ``findings.category`` for an imputed common variant matching a ClinVar P/LP record.
IMPUTED_CLINVAR_PATHOGENIC_CATEGORY = "imputed_clinvar_pathogenic"
# Imputed P/LP calls are statistically inferred, not directly observed, so they are
# capped below the high-confidence (≥3-star) headline tier regardless of the ClinVar
# review-star evidence the same variant would earn if directly typed: imputation is
# reliable for common, well-imputed variants, but a best-guess dosage hard-call is still
# an inference. The firewall + this cap + the caveat keep it appropriately framed.
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


@dataclass(frozen=True)
class ImputedClinVarFinding:
    """One firewall-cleared imputed common variant matching a ClinVar P/LP record."""

    chrom: str
    pos: int
    ref: str
    alt: str
    dr2: float
    af: float
    dosage: float
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
    """Firewall-cleared imputed variants the individual carries, as ``(variant, copies)``.

    Graceful degradation: returns ``[]`` when ``imputed_variants`` is absent (a sample
    DB predating Wave C) or empty (no imputation persisted) — so this whole module is a
    no-op there, byte-identical to not running it. Rows with a missing or best-guess
    hom-reference dosage (``round(dosage) < 1``) carry nothing and are dropped, and the
    SW-C3 firewall is re-asserted at the gate over each surviving row (defense in depth).
    """
    with sample_engine.connect() as conn:
        if not sa.inspect(conn).has_table(imputed_variants.name):
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
            )
        ).fetchall()

    carried: list[tuple[ImputedVariant, int]] = []
    for r in rows:
        if r.dosage is None:
            continue
        copies = round(r.dosage)
        if copies < 1:
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
        )
        if not imputed_variant_surfaceable(variant):
            continue  # firewall at the gate (defense in depth over persistence)
        carried.append((variant, min(copies, 2)))
    return carried


def _typed_alleles(sample_engine: sa.Engine) -> set[tuple[str | None, int, str, str]]:
    """``(norm_chrom, pos, ref, alt)`` the chip directly typed (from ``annotated_variants``).

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
    ``annotated_variants``; a row missing either cannot claim a specific allele, so it
    normalizes to ``""`` and will not match any real imputed allele key.
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
    return {
        (_norm_chrom(r.chrom), r.pos, (r.ref or "").upper(), (r.alt or "").upper()) for r in rows
    }


def find_imputed_clinvar_findings(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> list[ImputedClinVarFinding]:
    """Surface firewall-cleared imputed common variants at ClinVar P/LP loci.

    For each imputed variant the individual carries (at an allele the chip did **not**
    directly type), look up ClinVar by exact ``(chrom, pos, ref, alt)`` and, when the
    record's primary classification is (Likely) Pathogenic, emit a finding labeled
    imputed-not-typed.
    Lower-penetrance / risk-allele and "Conflicting classifications" records are
    excluded — they are not ordinary high-penetrance P/LP
    (:mod:`backend.analysis.clinvar_significance`). Returns an empty list when no
    imputation has been persisted (graceful degradation).
    """
    carried = _load_carried_imputed_variants(sample_engine)
    if not carried:
        return []

    typed = _typed_alleles(sample_engine)

    # Index carried imputed variants by exact allele key; drop chip-typed alleles.
    # The drop is allele-specific: only an imputed candidate whose exact
    # ``(chrom, pos, ref, alt)`` was directly typed is excluded, so a different ALT at a
    # coordinate the chip typed for another allele still surfaces (issue #1187).
    by_allele: dict[tuple[str | None, int, str, str], tuple[ImputedVariant, int]] = {}
    for variant, copies in carried:
        allele_key = (
            _norm_chrom(variant.chrom),
            variant.pos,
            variant.ref.upper(),
            variant.alt.upper(),
        )
        if allele_key in typed:
            continue
        by_allele[allele_key] = (variant, copies)
    if not by_allele:
        return []

    positions = sorted({(nchrom, pos) for (nchrom, pos, _ref, _alt) in by_allele})

    results: list[ImputedClinVarFinding] = []
    seen: set[tuple[str | None, int, str, str]] = set()
    with reference_engine.connect() as conn:
        # Batch the (chrom, pos) lookups under SQLite's bound-variable limit, mirroring
        # backend.annotation.clinvar.lookup_clinvar_by_positions.
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
                key = (
                    _norm_chrom(row.chrom),
                    row.pos,
                    (row.ref or "").upper(),
                    (row.alt or "").upper(),
                )
                match = by_allele.get(key)
                if match is None or key in seen:
                    continue
                seen.add(key)
                variant, copies = match
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
                        dosage=variant.dosage if variant.dosage is not None else 0.0,
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
    )
    return results


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
