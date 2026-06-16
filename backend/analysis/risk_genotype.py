"""Declarative by-rsID risk-genotype caller (EXPANSION_STRATEGY.md §6, §9).

The existing monogenic modules (cancer, cardiovascular, carrier_status) query
``annotated_variants`` for ClinVar Pathogenic/Likely-pathogenic by gene, gated on
``zygosity ∈ CARRIED_ZYGOSITIES``. That pattern does not fit the directly-typed
risk modules in the expansion wave because:

  - several report **common GWAS risk alleles that are not ClinVar P/LP** (AMD
    CFH/ARMS2, APOL1 G1/G2, gout ABCG2); and
  - the finding is **genotype-combination-specific** (HFE C282Y homozygous vs
    compound het; thrombophilia FVL+F2 double-het; alpha-1 PiZZ; APOL1
    two-risk-allele recessive).

This engine generalises the ``apoe.py`` precedent (read named rsIDs straight from
``raw_variants`` and map the genotype combination to a curated call) into a
*declarative* caller: a panel JSON expresses the loci, the risk allele per
locus, and an ordered set of genotype→risk models. The engine reads genotypes,
counts risk-allele dosage (strand-harmonized via
:func:`backend.analysis.allele_match.risk_dosage`, so minus-strand vendors call
correctly), evaluates the models, and stores findings in the unified ``findings``
table — no schema change.

Honesty guardrails (§12) encoded here:

  - **negative ≠ clear**: a probe that is absent / no-call yields ``None``
    (indeterminate), never a false-negative; the disclaimer always states it.
  - **relative-vs-absolute**: :func:`load_risk_panel` rejects any model that sets
    an ``odds_ratio``/relative risk without an ``absolute_risk_context``.
  - **ancestry overstatement**: an optional ``ancestry_gate`` suppresses or
    caveats calls outside the validated ancestry (APOL1).
  - **no P/LP inflation**: risk-genotype findings write ``clinvar_significance =
    NULL`` and carry declarative evidence stars, never auto-upgraded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import structlog

from backend.analysis.allele_match import risk_dosage
from backend.analysis.zygosity import _NO_CALL_SENTINELS, is_no_call
from backend.db.tables import findings, raw_variants, sample_metadata_table

logger = structlog.get_logger(__name__)

_PANELS_DIR = Path(__file__).resolve().parent.parent / "data" / "panels"

# Probe-readout status.
PROBE_TYPED = "typed"
PROBE_NO_CALL = "no_call"
PROBE_ABSENT = "absent"

# Sample file_formats whose mitochondrial positions are the GRCh37/rCRS reference
# (NC_012920), so an MT locus's curated rCRS position can be matched directly
# against ``raw_variants.pos`` (the #820 position fallback). DENY-by-default:
# only formats verified to store rCRS MT coordinates are listed. 23andMe v4/v5
# are Build 37 (rCRS MT); 23andMe v3 is Build 36 (hg18/CRS MT — positions above
# 3107 are shifted ~1 bp vs rCRS, and MT is NOT lifted at ingest, so a position
# match there would read a *different* variant, e.g. v3 "MT 11778 C" is not the
# m.11778G>A locus). Any unlisted/unknown format simply falls back to rsID/alias
# matching (current behaviour) — never a mis-positioned MT call. Add a format
# only after verifying its MT reference is rCRS.
_RCRS_MT_FORMATS: frozenset[str] = frozenset({"23andme_v4", "23andme_v5"})
_MT_CHROM = "MT"

# Locus allele types.
ALLELE_TYPE_SNV = "snv"
ALLELE_TYPE_INDEL = "indel"

# The indel codes the global :func:`backend.analysis.zygosity.is_no_call`
# treats as no-calls because most modules cannot map an I/D call to ref/alt —
# but an indel-typed :class:`RiskLocus` declares its own I/D risk/ref tokens
# (e.g. APOL1 G2, a 6-bp deletion), so for those loci we recognise I/D calls as
# real genotypes instead of discarding them.
_INDEL_NO_CALL_CODES = frozenset({"DD", "II", "DI", "ID"})

# The genotype sentinels that are *always* a no-call, even for an indel-typed
# locus — a genuine no-call ("--", "??", empty, …) stays indeterminate for every
# locus type. Derived from the shared zygosity recognition set (minus the indel
# codes above) so this second copy cannot silently drift from ``is_no_call``
# (#525); the parity guard in tests/backend/test_no_call_sentinel_parity.py
# locks the invariant.
_TRUE_NO_CALLS = _NO_CALL_SENTINELS - _INDEL_NO_CALL_CODES

# ── Shared caveat registry ─────────────────────────────────────────────────
# Caveat keys referenced by panel models resolve to standing language so the
# load-bearing honesty statements are written once and reused. Modules extend
# this dict as they are added.
CAVEAT_REGISTRY: dict[str, str] = {
    "negative_not_clear": (
        "A negative or low-risk result does not rule out untyped or rare variants — "
        "the array interrogates only specific named positions, not the whole gene."
    ),
    "reduced_penetrance": (
        "Carrying this genotype does not mean the condition will develop. Penetrance "
        "is reduced and depends on age, sex, and other genetic and environmental factors."
    ),
    "ancestry_european": (
        "Prevalence and penetrance figures are derived from European-ancestry "
        "populations and may not transfer to other genetic backgrounds."
    ),
    "confirm_clinically": (
        "This is an array-derived research/educational result. Confirm with clinical "
        "testing in a CLIA/accredited laboratory before any medical action."
    ),
    "aat_rare_null": (
        "A result other than PiZZ or PiSZ does not exclude alpha-1 antitrypsin "
        "deficiency: rare null and other deficiency alleles (e.g. Pi*null, Pi*Mmalton) "
        "are not interrogated by the array."
    ),
    "aat_pisz_phase_inferred": (
        "PiSZ requires the S and Z variants to be on opposite chromosomes. This "
        "SNP-array result observes both variants but is unphased, so it cannot "
        "prove they are in trans; confirm with serum AAT plus phenotype or targeted "
        "SERPINA1 sequencing before treating it as definitive PiSZ."
    ),
    "apol1_recessive": (
        "APOL1 kidney risk is recessive-like: two risk alleles (any combination of "
        "G1 and G2) define the established high-risk genotype. One risk allele is "
        "usually lower risk than two, but recent West African data (Gbadegesin et al., "
        "NEJM 2025) suggest monoallelic variants may modestly raise CKD/FSGS odds, so "
        "it is not zero risk."
    ),
    "apol1_second_hit": (
        "Even the two-risk-allele genotype has incomplete penetrance — most people "
        "with it never develop kidney disease. A 'second hit' (e.g. an "
        "interferon-driven inflammatory state, HIV, or other illness) typically "
        "modulates whether disease develops."
    ),
    "apol1_ancestry_gated": (
        "These variants arose on, and are validated in, recent African-ancestry "
        "backgrounds; they are near-absent elsewhere. Interpret the result with "
        "extra validation caution when inferred African ancestry is absent or low."
    ),
    "off_chip_partial": (
        "One or more interrogated positions were not typed on this array, so the "
        "genotype is partial — this result could change if the untyped positions "
        "were sequenced."
    ),
    "phase_inferred_compound_het": (
        "This compound-heterozygous call is phase-inferred. SNP-array genotypes are "
        "unphased, so they cannot prove the two variants sit on opposite chromosomes "
        "(in trans). Trans is the usual and most likely arrangement, but the same two "
        "variants in cis (on one chromosome, leaving the other HFE copy wild-type) has "
        "been documented by family genotyping — in which case the result is closer to a "
        "single-variant carrier. Family or clinical phasing is needed to confirm true "
        "compound heterozygosity."
    ),
    "mt_maternal_inheritance": (
        "This is a mitochondrial (mtDNA) variant. Mitochondrial DNA is inherited "
        "only from the mother, so a variant here is shared with maternal relatives "
        "(your mother, full siblings, and her maternal line) but is never passed on "
        "by fathers."
    ),
    "mt_heteroplasmy": (
        "Arrays report a single mitochondrial call and cannot measure heteroplasmy — "
        "the fraction of mitochondrial copies carrying the variant. Penetrance can "
        "depend on that fraction, which only quantitative clinical mitochondrial "
        "testing can establish."
    ),
    "parkinsons_no_prevention": (
        "There is no proven way to prevent Parkinson's disease, and a positive result "
        "does not call for any specific preventive treatment. The value of knowing is "
        "personal — for awareness, family planning, or research participation — and "
        "should be weighed with a neurologist or genetic counselor."
    ),
}


# ── Dataclasses ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskLocus:
    """One typed position in a risk panel, anchored to its rsID."""

    rsid: str
    gene_symbol: str
    label: str  # e.g. "C282Y"
    risk_allele: str  # rsID-anchored, on the strand named by canonical_strand
    ref_allele: str
    canonical_strand: str = "plus"  # "plus" | "minus" (minus flags cross-vendor pitfalls)
    off_chip_risk: str = "low"  # "high" → may be absent on arrays (e.g. indels)
    allele_type: str = ALLELE_TYPE_SNV  # "snv" | "indel" (indel uses literal I/D tokens)
    # Whether a Watson–Crick-complemented observation may be accepted as a
    # reverse-strand reading of this locus. True for nuclear SNP-array loci where
    # the probe strand varies by vendor. False for haploid mtDNA loci (MT-RNR1,
    # LHON), which are reported on a single strand: a complemented single base is
    # a *different* variant, not a strand flip, so accepting it would be a false
    # positive. When False, complement-only observations are indeterminate. (#30)
    allow_strand_complement: bool = True
    # Cis partner SNP(s) that define the same haplotype as this (tag) locus. When
    # a partner is typed and corroborates STRICTLY LESS risk dosage than the tag,
    # the tag's risk is unconfirmed and its dosage is reduced to indeterminate
    # (None) before classification — so a discordant assay cannot inflate a
    # recessive risk count (APOL1 G1: rs73885319 tag + rs60910145, in near-absolute
    # LD; #160). The veto is one-directional (partner < tag only): a partner
    # showing MORE dosage than the tag is the known rs60910145/G2-deletion
    # amplification artifact (David et al. 2018, PMID 30596185) and must not
    # suppress a real call. An untyped/no-call partner does not veto — the tag SNP
    # alone is a validated G1 readout. (#160)
    concordant_rsids: tuple[str, ...] = ()
    # Additional rsIDs that are merged/synonym records of the SAME variant as
    # this locus. ``read_genotypes`` reads the union of ``rsid`` + ``alias_rsids``
    # and resolves the first *typed* match, keyed back to the canonical ``rsid``,
    # so a sample that stored this variant under an alias is not under-called to
    # off-chip. Only seed with dbSNP/gnomAD-verified merges of the same variant
    # (e.g. the APOL1 G2 6-bp deletion rs71785313 ≡ rs143830837 ≡ rs1317778148);
    # an incorrect alias would mis-read a different variant as this locus. (#262)
    alias_rsids: tuple[str, ...] = ()
    # Opt-in rCRS-position fallback (#820): for an MT locus, ``chrom="MT"`` +
    # ``pos`` (the rCRS/NC_012920 position) lets ``read_genotypes`` match this
    # variant by genomic position when neither the canonical rsID nor an alias
    # yields a typed call — vendor/chip-version independent, so the locus is
    # callable without hand-curating each chip's proprietary i-ID. Applied ONLY
    # for samples whose file_format is in ``_RCRS_MT_FORMATS`` (v3/hg18 MT is
    # CRS-coordinate and excluded). Loci that declare no ``pos`` are unchanged.
    chrom: str | None = None
    pos: int | None = None


@dataclass(frozen=True)
class GenotypeModel:
    """A declarative genotype→risk rule.

    ``match`` maps each rsID to a per-locus condition
    (``{"dosage"|"dosage_min"|"dosage_max": n}``). It may additionally carry the
    reserved key ``"total_risk_dosage"`` → ``{"rsids": [...], "dosage_min": n}``
    which sums the *known* risk-allele dosage across several loci (used for the
    APOL1 recessive two-risk-allele model across the G1/G2 haplotypes).

    ``recessive`` marks a two-allele-recessive model (drives an inheritance note
    and the partial-genotype guardrail). ``modifier`` declares an attenuating
    locus (e.g. APOL1 N264K) that, when present alongside the risk it modifies,
    reclassifies the call to a lower-risk tier — and, when *not assessed*, adds a
    "risk may be overstated" caveat rather than a confident high-risk call.
    """

    id: str
    # rsid → condition, plus optional reserved "total_risk_dosage" aggregate key.
    match: dict[str, Any]
    risk_classification: str
    evidence_stars: int
    finding_text: str  # template; supports {genotype} {penetrance_text} {classification}
    zygosity: str | None = None
    odds_ratio: str | None = None
    # Ancestry-specific OR text, e.g. {"EAS": "...", "default": "..."}. When set,
    # the engine picks the band for the sample's inferred ancestry (gout effect
    # sizes are larger in East Asian ancestry); falls back to "default".
    odds_ratio_by_ancestry: dict[str, str] | None = None
    penetrance: Any = None  # str, or {"by_sex": {"XX": ..., "XY": ...}}
    absolute_risk_context: str | None = None
    caveats: list[str] = field(default_factory=list)
    pmids: list[str] = field(default_factory=list)
    primary_rsid: str | None = None  # gene/rsid attribution; defaults to first match key
    recessive: bool = False
    modifier: dict[str, Any] | None = None
    # For a total_risk_dosage model: a disclosure emitted when the model is one
    # risk allele short of firing AND an untyped contributing locus could push it
    # over the threshold (e.g. one APOL1 G1 allele typed, the G2 indel off-chip).
    # Block: {risk_classification, evidence_stars, finding_text}. Never asserts
    # low-risk — it states the genotype is indeterminate / partial.
    partial_disclosure: dict[str, Any] | None = None
    # Marks models whose biological interpretation depends on phase that SNP-array
    # genotypes do not directly establish, while still detecting the observed
    # variant combination.
    phase_inferred: bool = False
    confidence_note: str | None = None


@dataclass(frozen=True)
class RiskPanel:
    """A loaded, validated risk-genotype panel."""

    module: str
    version: str
    description: str
    category: str
    loci: list[RiskLocus]
    genotype_models: list[GenotypeModel]
    evaluation: str = "first_match"  # "first_match" | "collect_all"
    sex_stratified: bool = False
    ancestry_gate: dict[str, Any] | None = None
    disclaimer_key: str | None = None

    def locus(self, rsid: str) -> RiskLocus | None:
        return next((loc for loc in self.loci if loc.rsid == rsid), None)

    @property
    def rsids(self) -> list[str]:
        return [loc.rsid for loc in self.loci]


@dataclass(frozen=True)
class ProbeReadout:
    rsid: str
    genotype: str | None
    status: str  # PROBE_TYPED | PROBE_NO_CALL | PROBE_ABSENT


@dataclass(frozen=True)
class RiskCall:
    """A fired model → one finding."""

    model_id: str
    gene_symbol: str
    rsid: str  # primary or comma-joined
    risk_classification: str
    evidence_stars: int
    finding_text: str
    zygosity: str | None
    detail: dict[str, Any]
    pmids: list[str]


@dataclass
class RiskAssessment:
    """The full result of assessing a sample against a risk panel."""

    module: str
    category: str
    calls: list[RiskCall] = field(default_factory=list)
    dosages: dict[str, int | None] = field(default_factory=dict)
    readouts: dict[str, ProbeReadout] = field(default_factory=dict)
    indeterminate_loci: list[str] = field(default_factory=list)
    # Tag loci whose risk dosage was vetoed to indeterminate by a discordant cis
    # partner (haplotype-concordance QC, e.g. APOL1 G1; #160).
    discordant_loci: list[str] = field(default_factory=list)
    sex_used: str | None = None
    inferred_ancestry: str | None = None
    ancestry_suppressed: bool = False


# ── Panel loading + validation ──────────────────────────────────────────────


def load_risk_panel(path: str | Path) -> RiskPanel:
    """Load and validate a risk-genotype panel JSON.

    Raises ``ValueError`` if a model declares an ``odds_ratio``/relative risk
    without an ``absolute_risk_context`` (the relative-vs-absolute guardrail), or
    references a caveat key absent from :data:`CAVEAT_REGISTRY`.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    loci = [
        RiskLocus(
            rsid=loc["rsid"],
            gene_symbol=loc["gene_symbol"],
            label=loc.get("label", loc["rsid"]),
            risk_allele=loc["risk_allele"].upper(),
            ref_allele=loc["ref_allele"].upper(),
            canonical_strand=loc.get("canonical_strand", "plus"),
            off_chip_risk=loc.get("off_chip_risk", "low"),
            allele_type=loc.get("allele_type", ALLELE_TYPE_SNV),
            allow_strand_complement=loc.get("allow_strand_complement", True),
            concordant_rsids=tuple(loc.get("concordant_rsids", ())),
            alias_rsids=tuple(loc.get("alias_rsids", ())),
            chrom=loc.get("chrom"),
            pos=loc.get("pos"),
        )
        for loc in data["loci"]
    ]

    # Every concordant_rsids partner must be a declared locus, else the
    # concordance veto would silently never fire for it (a one-directional safety
    # failure: under-veto). Mirrors the caveat-key / PMID load-time validation.
    locus_rsids = {loc.rsid for loc in loci}
    for loc in loci:
        for partner in loc.concordant_rsids:
            if partner not in locus_rsids:
                raise ValueError(
                    f"Panel '{data['module']}' locus '{loc.rsid}' lists concordant_rsids "
                    f"partner '{partner}' that is not a declared locus in this panel."
                )

    # No rsID may map to two loci: every canonical rsid and alias rsid across the
    # panel must be unique, else a single sample variant could be claimed by two
    # loci (ambiguous read). Guards against an alias colliding with another
    # locus's canonical rsid or alias. (#262)
    seen_rsids: dict[str, str] = {}
    for loc in loci:
        for rid in (loc.rsid, *loc.alias_rsids):
            if rid in seen_rsids:
                raise ValueError(
                    f"Panel '{data['module']}' rsID '{rid}' is declared by both locus "
                    f"'{seen_rsids[rid]}' and locus '{loc.rsid}' (canonical/alias collision)."
                )
            seen_rsids[rid] = loc.rsid

    models: list[GenotypeModel] = []
    for m in data["genotype_models"]:
        pmids = m.get("pmids", [])
        _validate_pmids(data["module"], m["id"], "pmids", pmids)
        if not m.get("match"):
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' has an empty 'match' — "
                f"a model must declare at least one genotype condition."
            )
        caveats = m.get("caveats", [])
        for key in caveats:
            if key not in CAVEAT_REGISTRY:
                raise ValueError(
                    f"Panel '{data['module']}' model '{m['id']}' references unknown "
                    f"caveat key '{key}'."
                )
        if (m.get("odds_ratio") or m.get("odds_ratio_by_ancestry")) and not m.get(
            "absolute_risk_context"
        ):
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' sets an odds_ratio without "
                f"an absolute_risk_context — relative risk must always be paired with "
                f"absolute context."
            )
        # Validate any modifier's caveat is well-formed (it carries a dynamic,
        # context-specific note rather than a registry key, so just require it).
        modifier = m.get("modifier")
        if modifier is not None and "unassessed_caveat" not in modifier:
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' declares a modifier "
                f"without an 'unassessed_caveat'."
            )
        if modifier is not None:
            _validate_pmids(data["module"], m["id"], "modifier.pmids", modifier.get("pmids", []))
        phase_inferred = m.get("phase_inferred", False)
        confidence_note = m.get("confidence_note")
        if not isinstance(phase_inferred, bool):
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' field "
                f"'phase_inferred' must be a boolean."
            )
        if confidence_note is not None and not isinstance(confidence_note, str):
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' field "
                f"'confidence_note' must be a string."
            )
        if phase_inferred and not (confidence_note and confidence_note.strip()):
            raise ValueError(
                f"Panel '{data['module']}' model '{m['id']}' declares "
                f"phase_inferred without a confidence_note."
            )
        models.append(
            GenotypeModel(
                id=m["id"],
                match=m["match"],
                risk_classification=m["risk_classification"],
                evidence_stars=m["evidence_stars"],
                finding_text=m["finding_text"],
                zygosity=m.get("zygosity"),
                odds_ratio=m.get("odds_ratio"),
                odds_ratio_by_ancestry=m.get("odds_ratio_by_ancestry"),
                penetrance=m.get("penetrance"),
                absolute_risk_context=m.get("absolute_risk_context"),
                caveats=caveats,
                pmids=m.get("pmids", []),
                primary_rsid=m.get("primary_rsid"),
                recessive=m.get("recessive", False),
                modifier=modifier,
                partial_disclosure=m.get("partial_disclosure"),
                phase_inferred=phase_inferred,
                confidence_note=confidence_note,
            )
        )

    return RiskPanel(
        module=data["module"],
        version=data["version"],
        description=data.get("description", ""),
        category=data.get("category", "risk_genotype"),
        loci=loci,
        genotype_models=models,
        evaluation=data.get("evaluation", "first_match"),
        sex_stratified=data.get("sex_stratified", False),
        ancestry_gate=data.get("ancestry_gate"),
        disclaimer_key=data.get("disclaimer_key"),
    )


def _validate_pmids(module: str, model_id: str, field_name: str, pmids: Any) -> None:
    if not isinstance(pmids, list):
        raise ValueError(
            f"Panel '{module}' model '{model_id}' field '{field_name}' must be a "
            "list of numeric PubMed IDs."
        )
    for pmid in pmids:
        if not isinstance(pmid, str) or not pmid or not pmid.isascii() or not pmid.isdigit():
            raise ValueError(
                f"Panel '{module}' model '{model_id}' field '{field_name}' contains "
                f"non-numeric PMID {pmid!r}; PMIDs must be numeric PubMed IDs."
            )


# ── Genotype reading + dosage ───────────────────────────────────────────────


def _sample_uses_rcrs_mt(conn: sa.Connection) -> bool:
    """True if the sample's file_format stores rCRS MT coordinates (#820).

    Reads the single-row ``sample_metadata`` table; unknown/missing format (or a
    missing table, in minimal test DBs) → ``False`` (deny — never risk a
    mis-positioned MT call). See ``_RCRS_MT_FORMATS``.
    """
    try:
        fmt = conn.execute(sa.select(sample_metadata_table.c.file_format)).scalar()
    except Exception:  # noqa: BLE001 — table absent / unreadable → deny the fallback
        return False
    return fmt in _RCRS_MT_FORMATS


def read_genotypes(panel: RiskPanel, sample_engine: sa.Engine) -> dict[str, ProbeReadout]:
    """Read each panel locus's genotype from ``raw_variants``.

    A locus is read from its canonical ``rsid`` or any of its ``alias_rsids``
    (merged/synonym records of the same variant). The first *typed* match wins,
    keyed back to the canonical rsid, so a variant stored under an alias is not
    under-called as off-chip (#262). A probe absent from ``raw_variants``
    (off-chip) is ``PROBE_ABSENT`` and a no-call is ``PROBE_NO_CALL`` — both
    yield an indeterminate dosage downstream, never a false-negative.

    **rCRS position fallback (#820).** When a locus declares ``chrom="MT"`` and a
    rCRS ``pos`` and neither its rsID nor any alias yields a typed call, the call
    is matched by genomic position — ``raw_variants WHERE chrom='MT' AND pos=…``
    — so an MT variant typed only under a chip-specific i-ID (no curated alias)
    is still read. Applied ONLY for rCRS-coordinate samples (``_RCRS_MT_FORMATS``;
    v3/hg18 MT is excluded). Multiple probes at one position must agree on the
    allele set, else the position is left un-called (never a false call). Loci
    that declare no ``pos`` are unaffected — nuclear panels read exactly as before.
    """
    query_rsids: set[str] = set()
    for loc in panel.loci:
        query_rsids.add(loc.rsid)
        query_rsids.update(loc.alias_rsids)

    # MT loci that opt into the rCRS-position fallback (#820), keyed by position.
    pos_loci = {
        loc.pos: loc for loc in panel.loci if loc.chrom == _MT_CHROM and loc.pos is not None
    }

    with sample_engine.connect() as conn:
        stmt = sa.select(raw_variants.c.rsid, raw_variants.c.genotype).where(
            raw_variants.c.rsid.in_(query_rsids)
        )
        rows = {row.rsid: row.genotype for row in conn.execute(stmt)}

        # Build-gated MT position rows: only for rCRS-coordinate samples, and only
        # the positions some opt-in locus declares.
        pos_genotypes: dict[int, list[str]] = {}
        if pos_loci and _sample_uses_rcrs_mt(conn):
            pos_stmt = sa.select(raw_variants.c.pos, raw_variants.c.genotype).where(
                raw_variants.c.chrom == _MT_CHROM,
                raw_variants.c.pos.in_(pos_loci),
            )
            for r in conn.execute(pos_stmt):
                pos_genotypes.setdefault(r.pos, []).append(r.genotype)

    readouts: dict[str, ProbeReadout] = {}
    for loc in panel.loci:
        # Prefer a real typed call from the canonical rsid or any alias (in
        # order); fall back to a no-call if some record was present but uncalled;
        # else off-chip. For loci with no aliases this is identical to reading the
        # canonical rsid alone.
        typed_genotype: str | None = None
        nocall_genotype: str | None = None
        for candidate in (loc.rsid, *loc.alias_rsids):
            if candidate not in rows:
                continue
            genotype = rows[candidate]
            if _locus_is_no_call(loc, genotype):
                if nocall_genotype is None:
                    nocall_genotype = genotype
            else:
                typed_genotype = genotype
                break

        # rCRS position fallback (#820): no typed rsID/alias call, but the locus
        # declares an MT rCRS position that the (rCRS-format) sample types under
        # some probe. A typed positional call wins over an rsID no-call/absent.
        if typed_genotype is None and loc.pos is not None and loc.pos in pos_genotypes:
            pos_typed = [g for g in pos_genotypes[loc.pos] if not _locus_is_no_call(loc, g)]
            allele_sets = {frozenset(g.strip().upper()) for g in pos_typed}
            if len(allele_sets) == 1:  # all probes at this position agree
                typed_genotype = pos_typed[0]
            # discordant probes (len > 1) → ambiguous; leave the rsID-derived
            # no-call/absent rather than risk a false positional call.

        if typed_genotype is not None:
            readouts[loc.rsid] = ProbeReadout(loc.rsid, typed_genotype, PROBE_TYPED)
        elif nocall_genotype is not None:
            readouts[loc.rsid] = ProbeReadout(loc.rsid, nocall_genotype, PROBE_NO_CALL)
        else:
            readouts[loc.rsid] = ProbeReadout(loc.rsid, None, PROBE_ABSENT)
    return readouts


def _locus_is_no_call(locus: RiskLocus | None, genotype: str | None) -> bool:
    """No-call test that respects an indel locus's own I/D allele tokens.

    For an indel-typed locus the global :func:`is_no_call` would discard a real
    I/D call, so we only treat the universal sentinels as no-calls there.
    """
    if locus is not None and locus.allele_type == ALLELE_TYPE_INDEL:
        if genotype is None:
            return True
        return genotype.strip() in _TRUE_NO_CALLS
    return is_no_call(genotype)


def _indel_dosage(genotype: str | None, risk_token: str, ref_token: str) -> int | None:
    """Count copies of an indel risk token (e.g. ``"D"``) in an I/D genotype.

    Returns ``None`` when the call contains anything outside the locus's declared
    ``{risk, ref}`` tokens — i.e. it cannot be resolved, never a false negative.
    """
    if genotype is None:
        return None
    gt = genotype.strip().upper()
    if not gt:
        return None
    risk_u, ref_u = risk_token.upper(), ref_token.upper()
    chars = [gt] if len(gt) == 1 else list(gt)
    if any(c not in {risk_u, ref_u} for c in chars):
        return None
    return min(sum(1 for c in chars if c == risk_u), 2)


def compute_dosages(panel: RiskPanel, readouts: dict[str, ProbeReadout]) -> dict[str, int | None]:
    """Risk-allele dosage (0/1/2) per locus, or ``None`` when indeterminate."""
    dosages: dict[str, int | None] = {}
    for loc in panel.loci:
        readout = readouts.get(loc.rsid)
        if readout is None or readout.status != PROBE_TYPED:
            dosages[loc.rsid] = None
            continue
        if loc.allele_type == ALLELE_TYPE_INDEL:
            dosages[loc.rsid] = _indel_dosage(readout.genotype, loc.risk_allele, loc.ref_allele)
        else:
            dosages[loc.rsid] = risk_dosage(
                readout.genotype,
                loc.risk_allele,
                loc.ref_allele,
                allow_complement=loc.allow_strand_complement,
            )
    return dosages


def reconcile_concordance(
    panel: RiskPanel, dosages: dict[str, int | None]
) -> tuple[dict[str, int | None], list[str]]:
    """Veto a tag locus's dosage when a cis partner corroborates less risk.

    For a haplotype tag locus declaring ``concordant_rsids`` (e.g. APOL1 G1's
    rs73885319, with cis partner rs60910145 in near-absolute LD): if a partner is
    *typed* and its risk dosage is **strictly less** than the tag's, the tag's
    risk is unconfirmed, so the tag dosage is reduced to ``None`` (indeterminate)
    before classification. This stops a discordant assay (e.g. rs73885319 = GG but
    rs60910145 = TT) from being summed into a false two-risk-allele high-risk call
    (#160).

    The veto is **one-directional** — only ``partner < tag`` triggers it. A partner
    showing *more* dosage than the tag is the documented rs60910145/G2-deletion
    amplification artifact, which mis-scores genuine G1/G2 and G2/G2 carriers as
    homozygous-variant (David et al. 2018, PMID 30596185); treating that as
    discordant would suppress real high-risk calls. An untyped/no-call partner
    never vetoes — the tag SNP alone is a validated haplotype readout.

    Returns the (possibly adjusted) dosage dict and the list of vetoed tag rsids.
    """
    adjusted = dict(dosages)
    discordant: list[str] = []
    for loc in panel.loci:
        if not loc.concordant_rsids:
            continue
        tag_dosage = dosages.get(loc.rsid)
        if tag_dosage is None:
            continue
        partners_below = [
            p
            for p in loc.concordant_rsids
            if (pd := dosages.get(p)) is not None and pd < tag_dosage
        ]
        if partners_below:
            adjusted[loc.rsid] = None
            discordant.append(loc.rsid)
    return adjusted, discordant


# ── Classification ──────────────────────────────────────────────────────────


# Reserved ``match`` key: an aggregate condition that sums the known risk-allele
# dosage across several loci (the APOL1 recessive two-risk-allele model).
TOTAL_RISK_DOSAGE_KEY = "total_risk_dosage"


def _condition_matches(dosage: int | None, cond: dict[str, int]) -> bool:
    if dosage is None:
        return False
    if "dosage" in cond and dosage != cond["dosage"]:
        return False
    if "dosage_min" in cond and dosage < cond["dosage_min"]:
        return False
    if "dosage_max" in cond and dosage > cond["dosage_max"]:
        return False
    return True


def _total_dosage_matches(dosages: dict[str, int | None], cond: dict[str, Any]) -> bool:
    """Match on the summed *known* risk-allele dosage across ``cond['rsids']``.

    Indeterminate (``None``) loci contribute nothing to the known sum — so a
    model fires only when the *observed* alleles already meet the threshold (e.g.
    G1/G1 = 2 fires even with G2 untyped), never on the basis of an untyped
    locus. The partial-genotype guardrail (:func:`_apply_partial_guardrail`)
    then flags that an untyped contributing locus leaves the count uncertain.
    """
    rsids = cond.get("rsids", [])
    total_known = sum(d for rsid in rsids if (d := dosages.get(rsid)) is not None)
    if "dosage_min" in cond and total_known < cond["dosage_min"]:
        return False
    if "dosage_max" in cond and total_known > cond["dosage_max"]:
        return False
    return True


def _model_matches(model: GenotypeModel, dosages: dict[str, int | None]) -> bool:
    for key, cond in model.match.items():
        if key == TOTAL_RISK_DOSAGE_KEY:
            if not _total_dosage_matches(dosages, cond):
                return False
        elif not _condition_matches(dosages.get(key), cond):
            return False
    return True


def _model_rsids(model: GenotypeModel) -> list[str]:
    """The concrete loci a model references, expanding the aggregate key."""
    rsids: list[str] = []
    for key, cond in model.match.items():
        if key == TOTAL_RISK_DOSAGE_KEY:
            rsids.extend(cond.get("rsids", []))
        else:
            rsids.append(key)
    seen: set[str] = set()
    return [r for r in rsids if not (r in seen or seen.add(r))]


def _resolve_penetrance_text(penetrance: Any, sex: str | None) -> str:
    if penetrance is None:
        return ""
    if isinstance(penetrance, str):
        return penetrance
    by_sex = penetrance.get("by_sex", {}) if isinstance(penetrance, dict) else {}
    if sex in by_sex:
        return by_sex[sex]
    # Sex indeterminate (manual_review / unknown / None): show both, flagged.
    parts = [f"{s}: {txt}" for s, txt in by_sex.items()]
    if parts:
        return (
            "Biological sex could not be determined from the array, so both estimates "
            "are shown — " + "; ".join(parts) + "."
        )
    return ""


def _resolve_odds_ratio(model: GenotypeModel, inferred_ancestry: str | None) -> str:
    """Pick the ancestry-appropriate OR text, falling back to ``default``/``odds_ratio``."""
    by = model.odds_ratio_by_ancestry
    if by:
        return by.get(inferred_ancestry or "", by.get("default", "")) or model.odds_ratio or ""
    return model.odds_ratio or ""


def _render_finding(
    model: GenotypeModel,
    panel: RiskPanel,
    dosages: dict[str, int | None],
    readouts: dict[str, ProbeReadout],
    sex: str | None,
    inferred_ancestry: str | None = None,
) -> RiskCall:
    match_rsids = _model_rsids(model)
    primary = model.primary_rsid or match_rsids[0]
    primary_locus = panel.locus(primary)
    gene_symbol = primary_locus.gene_symbol if primary_locus else ""

    genotype_calls = {
        rsid: (readouts[rsid].genotype if rsid in readouts else None) for rsid in match_rsids
    }
    genotype_text = "; ".join(
        f"{rsid} {genotype_calls.get(rsid) or 'n/a'}" for rsid in match_rsids
    )
    penetrance_text = _resolve_penetrance_text(model.penetrance, sex)
    resolved_caveats = [CAVEAT_REGISTRY[k] for k in model.caveats]
    effective_or = _resolve_odds_ratio(model, inferred_ancestry)

    context = {
        "genotype": genotype_text,
        "penetrance_text": penetrance_text,
        "classification": model.risk_classification,
        "odds_ratio": effective_or,
        "absolute_risk": model.absolute_risk_context or "",
    }
    finding_text = model.finding_text.format_map(_SafeDict(context))

    detail = {
        "model_id": model.id,
        "classification": model.risk_classification,
        "genotype_calls": genotype_calls,
        "dosages": {rsid: dosages.get(rsid) for rsid in match_rsids},
        "evidence_stars": model.evidence_stars,
        "odds_ratio": effective_or or model.odds_ratio,
        "penetrance": model.penetrance,
        "penetrance_text": penetrance_text,
        "absolute_risk_context": model.absolute_risk_context,
        "caveats": resolved_caveats,
        "sex_used": sex,
        "recessive": model.recessive,
    }
    if model.phase_inferred:
        detail["phase_inferred"] = True
        detail["call_confidence"] = "Partial"
        detail["confidence_note"] = model.confidence_note

    return RiskCall(
        model_id=model.id,
        gene_symbol=gene_symbol,
        rsid=",".join(match_rsids),
        risk_classification=model.risk_classification,
        evidence_stars=model.evidence_stars,
        finding_text=finding_text,
        zygosity=model.zygosity,
        detail=detail,
        pmids=model.pmids,
    )


class _SafeDict(dict):
    """format_map helper that leaves unknown placeholders untouched."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - defensive
        return "{" + key + "}"


def _apply_partial_guardrail(
    call: RiskCall,
    model: GenotypeModel,
    dosages: dict[str, int | None],
    discordant: list[str],
) -> RiskCall:
    """Flag a fired aggregate-dosage model whose contributing loci weren't all typed.

    The recessive risk count can only be *at least* the observed sum when a
    contributing locus (e.g. the APOL1 G2 indel) is untyped, so the call is
    annotated as a partial genotype rather than presented as complete. Never
    flips the call to low-risk — it only adds caution.

    A contributing locus that was veto-nulled for cis-partner discordance is
    ``None`` in ``dosages`` but **was** typed, so it must NOT be reported as
    "not typed on this array" (that caveat is for genuinely off-chip loci). Such a
    locus instead gets an accurate genotyping-concordance note (#160).
    """
    cond = model.match.get(TOTAL_RISK_DOSAGE_KEY)
    if not cond:
        return call
    rsids = cond.get("rsids", [])
    discordant_set = set(discordant)
    # Veto-nulled (discordant) loci were typed, just discounted → not "untyped".
    unknown = [r for r in rsids if dosages.get(r) is None and r not in discordant_set]
    contributing_discordant = [r for r in rsids if r in discordant_set]

    out = call
    if unknown:
        out = _with_caveat(
            out,
            f"Partial genotype — {', '.join(unknown)} was not typed on this array, so "
            f"the risk-allele count could be higher than observed; interpret with caution.",
        )
    if contributing_discordant:
        out = _with_caveat(
            out,
            f"Genotyping-concordance note — the {', '.join(contributing_discordant)} G1 tag "
            f"call was discordant with its cis partner and discounted (QC); this result rests "
            f"on the remaining concordant risk allele(s).",
        )
    if out is call:
        return call

    detail = dict(out.detail)
    if unknown:
        detail["partial_genotype"] = True
        detail["untyped_loci"] = unknown
    if contributing_discordant:
        detail["discordant_loci"] = contributing_discordant
    return RiskCall(
        model_id=out.model_id,
        gene_symbol=out.gene_symbol,
        rsid=out.rsid,
        risk_classification=out.risk_classification,
        evidence_stars=out.evidence_stars,
        finding_text=out.finding_text,
        zygosity=out.zygosity,
        detail=detail,
        pmids=out.pmids,
    )


def _apply_modifier(
    call: RiskCall, model: GenotypeModel, dosages: dict[str, int | None]
) -> RiskCall:
    """Apply an attenuating modifier locus (e.g. APOL1 N264K) to a fired call.

    - Modifier present (dosage ≥ threshold) *and* a risk allele it attenuates is
      present → reclassify the call to the attenuated/low-risk tier.
    - Modifier not assessed (absent/no-call) while an attenuated risk allele is
      present *or* still unknown → add a "risk may be overstated" caveat rather
      than a confident high-risk call (§12 honesty guardrail).
    - Modifier confidently absent (typed, dosage 0) → no change.
    """
    mod = model.modifier
    if not mod:
        return call
    attenuates = mod.get("attenuates_risk_loci", [])
    mod_dosage = dosages.get(mod["rsid"])
    present_min = mod.get("present_min_dosage", 1)
    relevant_present = any((d := dosages.get(r)) is not None and d >= 1 for r in attenuates)
    relevant_unknown = any(dosages.get(r) is None for r in attenuates)

    if relevant_present and mod_dosage is not None and mod_dosage >= present_min:
        recl = mod["reclassify"]
        detail = dict(call.detail)
        detail["classification"] = recl["risk_classification"]
        detail["modifier_applied"] = mod["rsid"]
        detail["evidence_stars"] = recl.get("evidence_stars", call.evidence_stars)
        return RiskCall(
            model_id=call.model_id,
            gene_symbol=call.gene_symbol,
            rsid=call.rsid,
            risk_classification=recl["risk_classification"],
            evidence_stars=recl.get("evidence_stars", call.evidence_stars),
            finding_text=recl["finding_text"],
            zygosity=recl.get("zygosity", call.zygosity),
            detail=detail,
            pmids=[*call.pmids, *mod.get("pmids", [])],
        )

    if (relevant_present or relevant_unknown) and mod_dosage is None:
        return _with_caveat(call, mod["unassessed_caveat"])

    return call


def _maybe_partial_disclosure(
    panel: RiskPanel,
    dosages: dict[str, int | None],
    readouts: dict[str, ProbeReadout],
    sex: str | None,
) -> RiskCall | None:
    """Emit an indeterminate disclosure for a recessive model that is one risk
    allele short of firing because a contributing locus is untyped.

    Fires only when at least one risk allele is *confirmed* (``total_known >= 1``),
    the model has not reached its threshold, and an untyped contributing locus
    could still push it over (so the genotype is genuinely indeterminate — e.g.
    one APOL1 G1 allele typed with the G2 indel off-chip). Never asserts low-risk.
    Returns ``None`` when there is nothing to disclose (all loci typed, or no
    confirmed risk allele at all).
    """
    for model in panel.genotype_models:
        cond = model.match.get(TOTAL_RISK_DOSAGE_KEY)
        pd = model.partial_disclosure
        if not cond or not pd:
            continue
        rsids = cond.get("rsids", [])
        dmin = cond.get("dosage_min", 0)
        total_known = sum(d for r in rsids if (d := dosages.get(r)) is not None)
        untyped = [r for r in rsids if dosages.get(r) is None]
        if not untyped:
            continue
        # Confirmed at least one risk allele, below threshold, and the untyped
        # loci could still reach it → indeterminate.
        if 1 <= total_known < dmin and total_known + 2 * len(untyped) >= dmin:
            return _render_partial(model, pd, panel, dosages, readouts, sex, untyped)
    return None


def _render_partial(
    model: GenotypeModel,
    pd: dict[str, Any],
    panel: RiskPanel,
    dosages: dict[str, int | None],
    readouts: dict[str, ProbeReadout],
    sex: str | None,
    untyped: list[str],
) -> RiskCall:
    match_rsids = _model_rsids(model)
    primary = model.primary_rsid or match_rsids[0]
    primary_locus = panel.locus(primary)
    gene_symbol = primary_locus.gene_symbol if primary_locus else ""

    genotype_calls = {
        rsid: (readouts[rsid].genotype if rsid in readouts else None) for rsid in match_rsids
    }
    genotype_text = "; ".join(
        f"{rsid} {genotype_calls.get(rsid) or 'n/a'}" for rsid in match_rsids
    )
    note = (
        f"Indeterminate genotype — {', '.join(untyped)} was not typed on this array, "
        f"so a recessive (two-risk-allele) result cannot be ruled out."
    )
    resolved_caveats = [CAVEAT_REGISTRY[k] for k in model.caveats] + [note]
    finding_text = pd["finding_text"].format_map(_SafeDict({"genotype": genotype_text}))

    detail = {
        "model_id": f"{model.id}_partial",
        "classification": pd["risk_classification"],
        "genotype_calls": genotype_calls,
        "dosages": {rsid: dosages.get(rsid) for rsid in match_rsids},
        "evidence_stars": pd.get("evidence_stars", 1),
        "indeterminate": True,
        "partial_genotype": True,
        "untyped_loci": untyped,
        "caveats": resolved_caveats,
        "sex_used": sex,
    }
    return RiskCall(
        model_id=f"{model.id}_partial",
        gene_symbol=gene_symbol,
        rsid=",".join(match_rsids),
        risk_classification=pd["risk_classification"],
        evidence_stars=pd.get("evidence_stars", 1),
        finding_text=finding_text,
        zygosity=None,
        detail=detail,
        # The partial disclosure may carry its own evidence for the distinct
        # monoallelic-risk claim its finding_text makes (e.g. APOL1 Gbadegesin
        # 2025), beyond the model's two-risk-allele framework citations. Merge
        # both so the persisted provenance backs the partial finding's text
        # (#424). Mirrors `_apply_modifier`; falls back to the model's pmids
        # when the partial disclosure declares none.
        pmids=[*model.pmids, *pd.get("pmids", [])],
    )


def _render_discordance(
    panel: RiskPanel,
    dosages: dict[str, int | None],
    readouts: dict[str, ProbeReadout],
    sex: str | None,
    discordant: list[str],
) -> RiskCall:
    """Indeterminate disclosure for a haplotype tag vetoed by a discordant partner.

    Emitted when a tag locus's cis partner corroborated less risk (e.g. APOL1 G1:
    rs73885319 = GG but rs60910145 = TT), so the risk allele cannot be confirmed
    from the discordant assay. This is a genotyping-concordance (QC) flag and the
    recessive status is indeterminate — never reported as a low-risk result (#160).
    """
    # The aggregate model referencing the discordant tag drives gene/caveats/pmids.
    model = next(
        (m for m in panel.genotype_models if any(r in discordant for r in _model_rsids(m))),
        panel.genotype_models[0] if panel.genotype_models else None,
    )
    tag_locus = panel.locus(discordant[0])
    gene_symbol = tag_locus.gene_symbol if tag_locus else ""

    parts: list[str] = []
    involved: list[str] = []
    for tag in discordant:
        loc = panel.locus(tag)
        if loc is None:
            continue
        comps = [tag, *loc.concordant_rsids]
        comp_descs = []
        for rsid in comps:
            comp_locus = panel.locus(rsid)
            gt = (readouts[rsid].genotype if rsid in readouts else None) or "n/a"
            comp_descs.append(f"{rsid} [{comp_locus.label if comp_locus else rsid}] {gt}")
        parts.append(", ".join(comp_descs))
        involved.extend(comps)

    seen: set[str] = set()
    involved = [r for r in involved if not (r in seen or seen.add(r))]

    classification = f"{gene_symbol} haplotype indeterminate (discordant component SNPs)"
    note = (
        f"Genotyping-concordance flag — the cis SNPs that define this haplotype "
        f"disagree ({'; '.join(parts)}), so the {gene_symbol} risk allele cannot be "
        f"confirmed from these data. This is not a low-risk result: the recessive "
        f"(two-risk-allele) status is indeterminate. Consider sequencing-based "
        f"confirmation and discuss with a clinician or genetic counselor."
    )
    resolved_caveats = ([CAVEAT_REGISTRY[k] for k in model.caveats] if model else []) + [note]

    genotype_calls = {
        rsid: (readouts[rsid].genotype if rsid in readouts else None) for rsid in involved
    }
    detail = {
        "model_id": "haplotype_discordance",
        "classification": classification,
        "genotype_calls": genotype_calls,
        "dosages": {rsid: dosages.get(rsid) for rsid in involved},
        "evidence_stars": 1,
        "indeterminate": True,
        "discordant_loci": list(discordant),
        "caveats": resolved_caveats,
        "sex_used": sex,
    }
    return RiskCall(
        model_id="haplotype_discordance",
        gene_symbol=gene_symbol,
        rsid=",".join(involved),
        risk_classification=classification,
        evidence_stars=1,
        finding_text=note,
        zygosity=None,
        detail=detail,
        pmids=model.pmids if model else [],
    )


def classify(
    panel: RiskPanel,
    dosages: dict[str, int | None],
    readouts: dict[str, ProbeReadout],
    *,
    sex: str | None = None,
    inferred_ancestry: str | None = None,
    ancestry_fraction: float | None = None,
) -> RiskAssessment:
    """Evaluate the panel's genotype models against the computed dosages.

    Returns a :class:`RiskAssessment`. All-reference / no-model-fires yields an
    empty ``calls`` list (the carriage gate — no positive finding). Loci that are
    absent or no-call are listed in ``indeterminate_loci``.
    """
    # Haplotype-concordance QC: a tag locus whose cis partner corroborates less
    # risk is reduced to indeterminate, so a discordant assay cannot inflate a
    # recessive risk count into a false high-risk call (APOL1 G1; #160).
    dosages, discordant = reconcile_concordance(panel, dosages)

    assessment = RiskAssessment(
        module=panel.module,
        category=panel.category,
        dosages=dosages,
        readouts=readouts,
        sex_used=sex,
        inferred_ancestry=inferred_ancestry,
    )
    assessment.indeterminate_loci = [
        loc.rsid for loc in panel.loci if dosages.get(loc.rsid) is None
    ]
    assessment.discordant_loci = discordant

    matched = [m for m in panel.genotype_models if _model_matches(m, dosages)]
    if panel.evaluation == "first_match":
        matched = matched[:1]

    calls = []
    for m in matched:
        call = _render_finding(m, panel, dosages, readouts, sex, inferred_ancestry)
        call = _apply_partial_guardrail(call, m, dosages, discordant)
        call = _apply_modifier(call, m, dosages)
        calls.append(call)

    # No model fired. Surface an indeterminate disclosure (never low-risk):
    #   - a haplotype discordance vetoed a tag's risk (QC flag), or
    #   - a recessive model is one risk allele short with an untyped contributing
    #     locus that could still reach the threshold.
    if not calls:
        if discordant:
            indeterminate = _render_discordance(panel, dosages, readouts, sex, discordant)
        else:
            indeterminate = _maybe_partial_disclosure(panel, dosages, readouts, sex)
        if indeterminate is not None:
            calls = [indeterminate]

    # Ancestry gate (e.g. APOL1): suppress or caveat calls outside the validated
    # ancestry so risk is never overstated for non-target populations. Some
    # panels may still suppress indeterminate-only disclosures while caveating a
    # directly observed genotype.
    gate = panel.ancestry_gate
    if gate and calls:
        required = gate.get("required_ancestry")
        min_fraction = gate.get("min_fraction", 0.0)
        meets = inferred_ancestry == required and (
            ancestry_fraction is None or ancestry_fraction >= min_fraction
        )
        if not meets:
            mode = gate.get("mode", "caveat")
            note = gate.get(
                "note",
                f"These variants are validated only in {required} ancestry; this "
                f"result is not reported as actionable for your inferred ancestry "
                f"({inferred_ancestry or 'unknown'}).",
            )
            assessment.ancestry_suppressed = mode == "suppress"
            if mode == "suppress":
                # Drop the actionable finding; keep nothing user-facing as a high
                # risk call, but record the suppression on the assessment.
                calls = []
            else:  # caveat: keep but annotate
                if gate.get("suppress_indeterminate"):
                    observed_calls = [
                        call for call in calls if not call.detail.get("indeterminate")
                    ]
                    assessment.ancestry_suppressed = len(observed_calls) < len(calls)
                    calls = observed_calls
                calls = [_with_caveat(c, note) for c in calls]

    assessment.calls = calls
    return assessment


def _with_caveat(call: RiskCall, note: str) -> RiskCall:
    detail = dict(call.detail)
    detail["caveats"] = [*detail.get("caveats", []), note]
    return RiskCall(
        model_id=call.model_id,
        gene_symbol=call.gene_symbol,
        rsid=call.rsid,
        risk_classification=call.risk_classification,
        evidence_stars=call.evidence_stars,
        finding_text=f"{call.finding_text} {note}",
        zygosity=call.zygosity,
        detail=detail,
        pmids=call.pmids,
    )


# ── Findings storage ────────────────────────────────────────────────────────


def store_risk_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Store an assessment's calls in the ``findings`` table (idempotent).

    Clears existing rows for this (module, category) then inserts the fired
    calls. Risk-genotype findings carry ``clinvar_significance = NULL`` (these are
    risk genotypes, not ClinVar P/LP) and declarative evidence stars.
    """
    rows: list[dict[str, Any]] = []
    for call in assessment.calls:
        detail = dict(call.detail)
        detail["indeterminate_loci"] = assessment.indeterminate_loci
        rows.append(
            {
                "module": assessment.module,
                "category": assessment.category,
                "evidence_level": call.evidence_stars,
                "gene_symbol": call.gene_symbol,
                "rsid": call.rsid,
                "finding_text": call.finding_text,
                "conditions": call.risk_classification,
                "zygosity": call.zygosity,
                "clinvar_significance": None,
                "pmid_citations": json.dumps(call.pmids),
                "detail_json": json.dumps(detail),
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == assessment.module,
                findings.c.category == assessment.category,
            )
        )
        if rows:
            conn.execute(sa.insert(findings), rows)

    logger.info(
        "risk_findings_stored",
        module=assessment.module,
        count=len(rows),
        indeterminate=len(assessment.indeterminate_loci),
        ancestry_suppressed=assessment.ancestry_suppressed,
    )
    return len(rows)
