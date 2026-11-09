"""SpliceAI splice-prediction ingestion (SW-F2 / roadmap #38).

**BYO-only.** SpliceAI precomputed scores are an Illumina **non-commercial**
product distributed behind a BaseSpace login, so they are NEVER bundled or
auto-downloaded by this app (owner posture (A): non-commercial sources stay
user-fetch — see ``docs/external-inputs-strategy.md``). A user who has obtained
the precomputed VCF (e.g. ``spliceai_scores.raw.snv.hg19.vcf.gz``) ingests it into
a standalone ``spliceai.db`` via ``scripts/ingest_spliceai_scores.py``; the app
then surfaces, for a sample's *typed* SNVs, SpliceAI's predicted splice impact as
**in-silico context only**, never as ACMG evidence.

The hg19 precomputed files are GRCh37-coordinate — the same build as the app's
sample data — so rows are stored position-keyed (chrom/pos/ref/alt) and joined
directly, with NO liftover and NO rsID round-trip. (Use the hg19 files, not hg38.)
Because nothing converts coordinates, ingestion verifies the build from the VCF
header and refuses any other build without changing committed data (#2175).

SpliceAI VCF INFO format (Illumina/SpliceAI README; Jaganathan 2019)::

    SpliceAI=ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL

``DS_*`` are the four delta scores (acceptor gain, acceptor loss, donor gain,
donor loss), each 0–1; a variant's delta score is ``max(DS_AG, DS_AL, DS_DG,
DS_DL)``. ``DP_*`` are delta positions relative to the variant (positive =
downstream, negative = upstream). Recommended operating points: **0.2 high
recall, 0.5 recommended, 0.8 high precision** (Jaganathan et al. 2019, Cell
176(3):535-548; PMID:30661751 / DOI:10.1016/j.cell.2018.12.015, accessed
2026-06-26).

Guardrails:

* **Prediction ≠ proof.** SpliceAI is an in-silico predictor, not a functional
  assay; this layer is context-only and is deliberately NOT fed into ACMG (no
  PVS1/PP3/PS3 uplift) — see :data:`backend.disclaimers.SPLICEAI_CONTEXT_ONLY`.
* **No silent wipe.** A clear-then-load that parses zero rows raises (mirrors the
  GTEx/ClinGen/CPIC empty-parse guards).
* **Never bundled / auto-fetched.** No URL, no build_fn; registered
  ``build_mode="manual"`` so the setup wizard / update manager never try to fetch
  it (NC + BaseSpace-login-gated).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING

import sqlalchemy as sa
import structlog

from backend.db.build_guard import build_claim

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# SpliceAI primary paper (Jaganathan et al. 2019, Cell;
# DOI:10.1016/j.cell.2018.12.015). The PMID is carried on every badge.
SPLICEAI_PMID = "30661751"
# Default version label recorded in database_versions for a BYO ingest. The
# current Illumina precomputed scores derive from the SpliceAI v1.3 model; the
# CLI exposes --version so a user can stamp the exact file they supplied.
SPLICEAI_VERSION = "1.3"

# Recommended delta-score operating points (Jaganathan 2019; evidence-verified
# 2026-06-26 — 0.5 is "recommended", 0.2 is "high recall", 0.8 is "high
# precision"). Shared by the badge classifier (backend.analysis.spliceai).
SPLICEAI_CUTOFF_POSSIBLE = 0.2  # high recall
SPLICEAI_CUTOFF_LIKELY = 0.5  # recommended
SPLICEAI_CUTOFF_HIGH = 0.8  # high precision

# Below the lowest published operating point (0.2) SpliceAI does not flag a
# splice effect, so by default those rows are not stored — a lookup miss then
# means "no splice-altering prediction". The CLI exposes --min-ds to override.
DEFAULT_MIN_DS = SPLICEAI_CUTOFF_POSSIBLE

# Pull the pipe-delimited SpliceAI payload out of the INFO column. Anchored to a
# field boundary (start or ';') so it cannot match a substring of another key.
_SPLICEAI_INFO_RE = re.compile(r"(?:^|;)SpliceAI=([^;\t]+)")

_metadata = sa.MetaData()

spliceai_scores = sa.Table(
    "spliceai_scores",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chrom", sa.Text, nullable=False),  # GRCh37 chrom, no 'chr', upper
    sa.Column("pos", sa.Integer, nullable=False),  # GRCh37 1-based position
    sa.Column("ref", sa.Text, nullable=False),
    sa.Column("alt", sa.Text, nullable=False),
    sa.Column("symbol", sa.Text),  # gene symbol
    sa.Column("ds_ag", sa.Float),  # delta score: acceptor gain
    sa.Column("ds_al", sa.Float),  # acceptor loss
    sa.Column("ds_dg", sa.Float),  # donor gain
    sa.Column("ds_dl", sa.Float),  # donor loss
    sa.Column("dp_ag", sa.Integer),  # delta positions (relative to the variant)
    sa.Column("dp_al", sa.Integer),
    sa.Column("dp_dg", sa.Integer),
    sa.Column("dp_dl", sa.Integer),
    sa.Column("ds_max", sa.Float, nullable=False),  # max(DS_*); precomputed
)

sa.Index(
    "idx_spliceai_locus",
    spliceai_scores.c.chrom,
    spliceai_scores.c.pos,
    spliceai_scores.c.ref,
    spliceai_scores.c.alt,
)


# Written in the same transaction as the scores. Old database_versions stamps
# cannot reconstruct this evidence and are deliberately never migrated into it.
SPLICEAI_INGESTION_REVISION = 1
SPLICEAI_REINGEST = (
    "SpliceAI build provenance is unverified. Please re-ingest all original hg19 files "
    "with scripts/ingest_spliceai_scores.py; a version stamp cannot verify them."
)
spliceai_sources = sa.Table(
    "spliceai_sources",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("source_name", sa.Text, nullable=False),
    sa.Column("source_sha256", sa.Text, nullable=False),
    sa.Column("source_size_bytes", sa.Integer, nullable=False),
    sa.Column("genome_build", sa.Text, nullable=False),
    sa.Column("validation_method", sa.Text, nullable=False),
    sa.Column("caller_confirmation", sa.Text),
    sa.Column("build_header_json", sa.Text, nullable=False),
    sa.Column("min_ds", sa.Float, nullable=False),
    sa.Column("row_count", sa.Integer, nullable=False),
    sa.Column("ingestion_revision", sa.Integer, nullable=False),
    sa.Column("ingested_at", sa.Text, nullable=False),
)


@dataclass(frozen=True)
class SpliceAIHealth:
    """Shared structural/provenance decision for health and score consumers."""

    state: str
    detail: str


def check_spliceai_connection(conn: sa.Connection, *, deep: bool = False) -> SpliceAIHealth:
    """Read the consumer schema and recheck persisted build evidence, without writes.

    Fast checks read one score and the small source manifest. Explicit deep
    verification additionally reconciles row counts; polling never scans scores.
    """
    try:
        if conn.execute(sa.select(spliceai_scores).limit(1)).first() is None:
            return SpliceAIHealth("corrupt", "table 'spliceai_scores' is empty")
    except sa.exc.SQLAlchemyError:
        return SpliceAIHealth(
            "corrupt", "SpliceAI scores are unreadable or missing required columns."
        )
    try:
        sources = conn.execute(sa.select(spliceai_sources)).mappings().all()
        if not sources:
            return SpliceAIHealth("unverified", SPLICEAI_REINGEST)
        for source in sources:
            header = json.loads(source["build_header_json"])
            if not isinstance(header, list) or any(
                not isinstance(line, str)
                or not line.startswith(("##reference=", "##assembly=", "##contig="))
                for line in header
            ):
                return SpliceAIHealth("unverified", SPLICEAI_REINGEST)
            declared = _assess_header(Path("SpliceAI source"), header)
            confirmation = source["caller_confirmation"]
            _require_grch37(Path("SpliceAI source"), declared, confirmation)
            expected_method = "header" if declared else "caller_confirmation"
            stamp = datetime.fromisoformat(source["ingested_at"])
            if (
                source["ingestion_revision"] != SPLICEAI_INGESTION_REVISION
                or source["genome_build"] != "GRCh37"
                or source["validation_method"] != expected_method
                or not re.fullmatch(r"[0-9a-f]{64}", source["source_sha256"])
                or not source["source_name"]
                or not isinstance(source["source_size_bytes"], int)
                or source["source_size_bytes"] <= 0
                or not isinstance(source["row_count"], int)
                or source["row_count"] <= 0
                or not 0 <= source["min_ds"] <= 1
                or stamp.tzinfo is None
            ):
                return SpliceAIHealth("unverified", SPLICEAI_REINGEST)
        if deep:
            actual = conn.execute(
                sa.select(sa.func.count()).select_from(spliceai_scores)
            ).scalar_one()
            if actual != sum(source["row_count"] for source in sources):
                return SpliceAIHealth("unverified", SPLICEAI_REINGEST)
    except (sa.exc.SQLAlchemyError, ValueError, TypeError, OverflowError):
        return SpliceAIHealth("unverified", SPLICEAI_REINGEST)
    return SpliceAIHealth("ready", "ok (GRCh37 import provenance verified)")


def _source_fingerprint(path: Path) -> tuple[str, int]:
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        size = handle.tell()
    return digest, size


@dataclass
class SpliceAILoadStats:
    """Outcome of ingesting a SpliceAI precomputed-scores VCF."""

    loaded: int
    skipped_below_threshold: int
    skipped_bad_row: int
    genome_build: str = "GRCh37"


# GRCh37 lengths of contigs whose length differs between builds, from NCBI's
# GRCh37.p13 assembly (GCF_000001405.25, accessed 2026-10-17; evidence in
# data/science-evidence/2026-10-17-spliceai-grch37-contig-lengths/). Illumina's
# SpliceAI example VCF declares the same values, and they match the hg19 FASTA
# sentinels the Genome Browser validates (#1449). MT is left out: GRCh37's MT and
# hg19's chrM are different sequences, so a genuine GRCh37 file can declare either.
_GRCH37_CONTIG_LENGTHS: dict[str, int] = {
    "1": 249_250_621,
    "2": 243_199_373,
    "10": 135_534_747,
    "X": 155_270_560,
    "Y": 59_373_566,
}
# Build names, each matched at the start of a label token (the label split on
# non-alphanumerics) and not followed by another digit. Suffixed releases such as
# "GRCh38p14", "hs37d5" or "CHM13v2" are recognized; "GRCh370" or a path segment
# like "lab37" is not. Broad's references are "Homo_sapiens_assembly19/38". A name
# in neither set leaves the header silent, so the caller must confirm the build,
# and the contig-length check still applies.
_GRCH37_NAME_RE = re.compile(r"(?:grch37|ncbi37|hg19|b37|hs37|assembly19)(?!\d)")
_OTHER_BUILD_NAME_RE = re.compile(
    r"(?:grch38|hg38|b38|hs38|assembly38|ncbi36|hg18|b36|chm13|t2t|hs1)(?!\d)"
)
_LABEL_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Possessive repeats prevent malformed quoted values from redistributing a long
# whitespace run among adjacent matches. Delimiters are excluded from values.
_CONTIG_FIELD_RE = re.compile(
    r"\s*+(?P<key>[A-Za-z_][A-Za-z0-9_.-]*+)\s*+=\s*+"
    r'(?P<value>"(?:[^"\\]|\\["\\])*+"|[^",<>]*+)\s*+(?:,|$)'
)


def _classify_build_label(label: str) -> str | None:
    """``"GRCh37"``, ``"other"`` (any other build named, alone or alongside), or None."""
    lower = label.lower()
    tokens = _LABEL_TOKEN_RE.findall(lower)
    if any(_OTHER_BUILD_NAME_RE.match(token) for token in tokens):
        return "other"
    # 1000 Genomes' GRCh37 reference is "human_g1k_v37", which splits into "g1k"/"v37".
    if any(_GRCH37_NAME_RE.match(token) for token in tokens) or "g1k_v37" in lower:
        return "GRCh37"
    return None


def _parse_contig_fields(path: Path, line: str) -> dict[str, str]:
    """Read one structured contig record without skipping malformed evidence.

    Quoted values may contain commas and escaped double quotes/backslashes.
    Match every character rather than searching for fields inside other values;
    reject duplicate keys instead of letting the last one hide a contradiction.
    """
    error = f"{path}: malformed ##contig header"
    body = line.removeprefix("##contig=").strip()
    if not (body.startswith("<") and body.endswith(">")):
        raise ValueError(f"{error}: expected <key=value,...>.")
    body = body[1:-1].strip()
    if not body or body.endswith(","):
        raise ValueError(f"{error}: empty record or trailing comma.")
    fields: dict[str, str] = {}
    position = 0
    while position < len(body):
        match = _CONTIG_FIELD_RE.match(body, position)
        if match is None:
            raise ValueError(f"{error}: invalid field or quoting.")
        key, value = match.group("key", "value")
        if key in fields:
            raise ValueError(f"{error}: duplicate field {key}.")
        if value.startswith('"'):
            value = re.sub(r'\\(["\\])', r"\1", value[1:-1])
        fields[key] = value.strip()
        position = match.end()
    identifier = fields.get("ID", "")
    if not re.fullmatch(r'[^\s,<>"\\]+', identifier):
        raise ValueError(f"{error}: missing or invalid ID.")
    length = fields.get("length")
    if length is not None and (not re.fullmatch(r"[0-9]+", length) or not length.lstrip("0")):
        raise ValueError(f"{error}: length must be a positive decimal integer.")
    return fields


def _assess_header(path: Path, header: list[str]) -> str | None:
    """Return ``"GRCh37"`` when the header verifies it, ``None`` when it is silent.

    Evidence is the ``##reference`` and ``##assembly`` lines, each ``##contig``
    ``assembly=`` value,
    and the declared length of any contig in :data:`_GRCH37_CONTIG_LENGTHS`.

    Raises:
        ValueError: the header has malformed contig metadata, names another
            build, or contradicts itself (e.g. a GRCh37 label with another length).
    """
    grch37: list[str] = []
    other: list[str] = []
    for line in header:
        if line.startswith(("##reference=", "##assembly=")):
            key, _, label = line[2:].partition("=")
            verdict = _classify_build_label(label.strip())
            if verdict == "GRCh37":
                grch37.append(f"##{key}={label.strip()}")
            elif verdict == "other":
                other.append(f"##{key}={label.strip()}")
        elif line.startswith("##contig="):
            fields = _parse_contig_fields(path, line)
            assembly = fields.get("assembly", "").strip()
            verdict = _classify_build_label(assembly) if assembly else None
            if verdict == "GRCh37":
                grch37.append(f"contig assembly={assembly}")
            elif verdict == "other":
                other.append(f"contig assembly={assembly}")
            contig = normalize_chrom(fields.get("ID", ""))
            expected = _GRCH37_CONTIG_LENGTHS.get(contig)
            length = fields.get("length")
            if expected is not None and length is not None:
                if length.lstrip("0") == str(expected):
                    grch37.append(f"contig {contig} length={length}")
                else:
                    other.append(f"contig {contig} length={length} (GRCh37: {expected})")
    if other:
        raise ValueError(
            f"{path}: the VCF header does not describe GRCh37 ({'; '.join(other)}). "
            "SpliceAI scores are joined to GRCh37 sample coordinates without liftover; "
            "use the hg19 precomputed files."
        )
    return "GRCh37" if grch37 else None


def _require_grch37(path: Path, declared: str | None, genome_build: str | None) -> str:
    """Reconcile the header's verdict with the caller's stated build, or raise."""
    if genome_build is not None and genome_build != "GRCh37":
        raise ValueError(f"{path}: only GRCh37 SpliceAI scores can be joined; got {genome_build}.")
    if declared is None and genome_build is None:
        raise ValueError(
            f"{path}: the VCF header declares no genome build. Confirm the file is the "
            "hg19 (GRCh37) release, then pass genome_build='GRCh37' "
            "(--genome-build GRCh37 on the command line)."
        )
    return "GRCh37"


def read_spliceai_assembly(vcf_path: Path, *, genome_build: str | None = None) -> str:
    """Verify a SpliceAI VCF is GRCh37 from its header alone, without reading rows.

    Raises:
        ValueError: the header names another build or contradicts itself, or it
            names none and ``genome_build`` does not supply GRCh37.
    """
    header: list[str] = []
    with _open_maybe_gzip(vcf_path) as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            header.append(line.rstrip("\n"))
    return _require_grch37(vcf_path, _assess_header(vcf_path, header), genome_build)


def create_spliceai_tables(engine: sa.Engine) -> None:
    """Create score and source tables if absent; never certify existing rows."""
    _metadata.create_all(engine)


def normalize_chrom(chrom: str) -> str:
    """Normalize a chromosome label to bare, uppercase form (``chr7`` → ``7``)."""
    c = chrom.strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    return c.upper()


def _float_or_none(val: str) -> float | None:
    """Parse a delta score → float in [0, 1], or None if missing/invalid.

    Rejects non-finite (``nan``/``inf``) and out-of-range values so they cannot
    slip past the min-DS threshold check (``nan`` comparisons are always False).
    """
    val = val.strip()
    if not val or val in (".", "NA"):
        return None
    try:
        parsed = float(val)
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > 1.0:
        return None
    return parsed


def _int_or_none(val: str) -> int | None:
    val = val.strip()
    if not val or val in (".", "NA"):
        return None
    try:
        return int(float(val))
    except (OverflowError, ValueError):  # OverflowError: int(float("inf"))
        return None


def _open_maybe_gzip(path: Path) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return p.open("r", encoding="utf-8")


def parse_spliceai_info(info: str) -> list[dict]:
    """Parse a VCF INFO column's ``SpliceAI=`` payload → per-allele/gene dicts.

    Returns one dict per pipe-delimited SpliceAI entry (a VCF row may carry
    several, comma-separated, one per ALT allele / overlapping gene). Each dict
    has ``allele``, ``symbol``, the four ``ds_*`` / ``dp_*`` values, and
    ``ds_max`` (= the max of the present delta scores). Entries with fewer than
    the 10 expected subfields, or with no parseable delta score, are dropped.
    """
    m = _SPLICEAI_INFO_RE.search(info)
    if not m:
        return []
    out: list[dict] = []
    for entry in m.group(1).split(","):
        fields = entry.split("|")
        if len(fields) < 10:
            continue
        ds = [_float_or_none(fields[i]) for i in range(2, 6)]  # AG, AL, DG, DL
        dp = [_int_or_none(fields[i]) for i in range(6, 10)]
        ds_present = [v for v in ds if v is not None]
        if not ds_present:
            continue
        out.append(
            {
                "allele": fields[0].strip().upper(),
                "symbol": fields[1].strip() or None,
                "ds_ag": ds[0],
                "ds_al": ds[1],
                "ds_dg": ds[2],
                "ds_dl": ds[3],
                "dp_ag": dp[0],
                "dp_al": dp[1],
                "dp_dg": dp[2],
                "dp_dl": dp[3],
                "ds_max": max(ds_present),
            }
        )
    return out


def ingest_spliceai_vcf(
    vcf_path: Path,
    engine: sa.Engine,
    *,
    min_ds: float = DEFAULT_MIN_DS,
    clear_existing: bool = True,
    parse_progress: Callable[[int], None] | None = None,
    genome_build: str | None = None,
) -> SpliceAILoadStats:
    """Ingest a BYO SpliceAI precomputed-scores VCF into ``spliceai.db``.

    Args:
        vcf_path: SpliceAI precomputed VCF (plain or ``.gz``), GRCh37 / hg19.
        engine: Target standalone-DB engine.
        min_ds: Only store rows whose ``ds_max`` is at least this (default 0.2,
            the lowest published operating point — below it SpliceAI flags no
            splice effect). Pass 0.0 to keep every scored row.
        clear_existing: Delete all prior rows first (use ``False`` to append a
            second file, e.g. indels after SNVs).
        parse_progress: Optional callback invoked with the running stored-row
            count as parsing proceeds.
        genome_build: The caller's confirmation that the file is ``"GRCh37"``,
            required only when the header declares no build. A header that names
            another build is refused either way.

    Raises:
        ValueError: zero rows met the threshold (no silent clear/replace with
            empty data — mirrors the GTEx empty-parse guard), or the file is not
            verifiably GRCh37 (the existing committed rows remain unchanged).
    """
    return ingest_spliceai_vcfs(
        [vcf_path],
        engine,
        min_ds=min_ds,
        clear_existing=clear_existing,
        parse_progress=parse_progress,
        genome_build=genome_build,
    )[0]


def ingest_spliceai_vcfs(
    vcf_paths: list[Path],
    engine: sa.Engine,
    *,
    min_ds: float = DEFAULT_MIN_DS,
    clear_existing: bool = True,
    parse_progress: Callable[[int], None] | None = None,
    genome_build: str | None = None,
) -> list[SpliceAILoadStats]:
    """Atomically load every source, or retain the entire previous installation.

    Each source records only assembly-related header lines, never sample names
    or unrelated VCF metadata. Appending to unverified legacy rows is refused.
    """
    if not vcf_paths:
        raise ValueError("at least one SpliceAI VCF is required")
    if not math.isfinite(min_ds) or not 0 <= min_ds <= 1:
        raise ValueError("min_ds must be finite and between 0 and 1")
    database = engine.url.database
    claim = (
        build_claim("spliceai", Path(database).resolve().parent)
        if engine.dialect.name == "sqlite" and database and database != ":memory:"
        else nullcontext(True)
    )
    # Protect even the initially empty schema, and keep cleanup excluded until
    # the batch transaction has committed or rolled back. SQLite's writer lock
    # alone cannot prevent another process from unlinking its database file.
    with claim as acquired:
        if not acquired:
            raise RuntimeError("Another SpliceAI import or cleanup is in progress; retry later.")
        create_spliceai_tables(engine)
        with engine.begin() as conn:
            # Acquire SQLite's writer lock before checking append provenance. Even
            # a zero-row DELETE begins the write transaction, so another ingestion
            # cannot replace the source manifest between this check and insertion.
            conn.execute(spliceai_scores.delete().where(sa.false()))
            if clear_existing:
                conn.execute(spliceai_scores.delete())
                # Replacement also repairs malformed provenance schemas. SQLite DDL
                # participates in this already-started write transaction, so a later
                # failure restores the old manifest together with the old scores.
                spliceai_sources.drop(conn)
                spliceai_sources.create(conn)
            elif (
                conn.execute(sa.select(spliceai_scores.c.id).limit(1)).first() is not None
                or conn.execute(sa.select(spliceai_sources.c.id).limit(1)).first() is not None
            ) and check_spliceai_connection(conn).state != "ready":
                raise ValueError(SPLICEAI_REINGEST)
            return [
                _load_spliceai_vcf(
                    path,
                    conn,
                    min_ds=min_ds,
                    parse_progress=parse_progress,
                    genome_build=genome_build,
                )
                for path in vcf_paths
            ]


def _load_spliceai_vcf(
    vcf_path: Path,
    conn: sa.Connection,
    *,
    min_ds: float,
    parse_progress: Callable[[int], None] | None,
    genome_build: str | None,
) -> SpliceAILoadStats:
    fingerprint = _source_fingerprint(vcf_path)

    rows: list[dict] = []
    skipped_below = 0
    skipped_bad = 0
    lines = 0
    header: list[str] = []
    build: str | None = None
    with _open_maybe_gzip(vcf_path) as fh:
        for line in fh:
            if not line:
                continue
            if line.startswith("#"):
                if build is None:
                    header.append(line.rstrip("\n"))
                continue
            if build is None:
                build = _require_grch37(vcf_path, _assess_header(vcf_path, header), genome_build)
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                skipped_bad += 1
                continue
            chrom_raw, pos_raw, _id, ref, alt, _qual, _filter, info = parts[:8]
            try:
                pos = int(pos_raw)
            except ValueError:
                skipped_bad += 1
                continue
            entries = parse_spliceai_info(info)
            if not entries:
                skipped_bad += 1
                continue
            chrom = normalize_chrom(chrom_raw)
            ref_u = ref.strip().upper()
            alts = [a.strip().upper() for a in alt.split(",") if a.strip()]
            for e in entries:
                if e["ds_max"] is None or e["ds_max"] < min_ds:
                    skipped_below += 1
                    continue
                # Precomputed files are single-ALT, but stay robust to multi-allelic
                # rows: prefer the SpliceAI entry's own ALLELE; fall back to the VCF
                # ALT only when it is unambiguous (exactly one). An unmatched entry
                # on a multi-allelic row would otherwise be stored under the wrong
                # allele, so skip it instead.
                if e["allele"] in alts:
                    this_alt = e["allele"]
                elif len(alts) == 1:
                    this_alt = alts[0]
                else:
                    skipped_bad += 1
                    continue
                rows.append(
                    {
                        "chrom": chrom,
                        "pos": pos,
                        "ref": ref_u,
                        "alt": this_alt,
                        "symbol": e["symbol"],
                        "ds_ag": e["ds_ag"],
                        "ds_al": e["ds_al"],
                        "ds_dg": e["ds_dg"],
                        "ds_dl": e["ds_dl"],
                        "dp_ag": e["dp_ag"],
                        "dp_al": e["dp_al"],
                        "dp_dg": e["dp_dg"],
                        "dp_dl": e["dp_dl"],
                        "ds_max": e["ds_max"],
                    }
                )
            lines += 1
            if parse_progress is not None and lines % 100_000 == 0:
                parse_progress(len(rows))

    if build is None:
        build = _require_grch37(vcf_path, _assess_header(vcf_path, header), genome_build)
    if not rows:
        raise ValueError(
            f"parsed zero SpliceAI rows at/above min_ds={min_ds} — refusing to "
            f"clear/replace with empty data (is this a SpliceAI-annotated VCF?)."
        )

    if fingerprint != _source_fingerprint(vcf_path):
        raise ValueError("SpliceAI source changed during ingestion; retry with an unchanged file.")
    build_header = [
        line for line in header if line.startswith(("##reference=", "##assembly=", "##contig="))
    ]
    declared = _assess_header(vcf_path, build_header)
    for i in range(0, len(rows), 1000):
        conn.execute(spliceai_scores.insert(), rows[i : i + 1000])
    conn.execute(
        spliceai_sources.insert(),
        {
            "source_name": vcf_path.name,
            "source_sha256": fingerprint[0],
            "source_size_bytes": fingerprint[1],
            "genome_build": build,
            "validation_method": "header" if declared else "caller_confirmation",
            "caller_confirmation": genome_build,
            "build_header_json": json.dumps(build_header),
            "min_ds": min_ds,
            "row_count": len(rows),
            "ingestion_revision": SPLICEAI_INGESTION_REVISION,
            "ingested_at": datetime.now(UTC).isoformat(),
        },
    )

    if parse_progress is not None:
        parse_progress(len(rows))
    return SpliceAILoadStats(
        loaded=len(rows),
        skipped_below_threshold=skipped_below,
        skipped_bad_row=skipped_bad,
        genome_build=build,
    )


def lookup_spliceai_by_variant(
    chrom: str | None,
    pos: int | None,
    ref: str | None,
    alt: str | None,
    engine: sa.Engine,
) -> dict | None:
    """Return the highest-``ds_max`` SpliceAI row for a GRCh37 variant, or None.

    Chrom/ref/alt are normalized (bare uppercase chrom, uppercase alleles) so a
    ``chr7`` query matches a stored ``7`` row. If several genes score the same
    locus, the strongest (largest ``ds_max``) is returned.
    """
    if not chrom or pos is None or not ref or not alt:
        return None
    nchrom = normalize_chrom(chrom)
    nref = ref.strip().upper()
    nalt = alt.strip().upper()
    with engine.connect() as conn:
        if check_spliceai_connection(conn).state != "ready":
            return None
        stmt = (
            sa.select(spliceai_scores)
            .where(
                spliceai_scores.c.chrom == nchrom,
                spliceai_scores.c.pos == pos,
                spliceai_scores.c.ref == nref,
                spliceai_scores.c.alt == nalt,
            )
            .order_by(spliceai_scores.c.ds_max.desc())
            .limit(1)
        )
        row = conn.execute(stmt).fetchone()
    return dict(row._mapping) if row else None


def record_spliceai_version(
    engine: sa.Engine,
    *,
    genome_build: str,
    version: str = SPLICEAI_VERSION,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Record the SpliceAI version in ``database_versions``.

    ``genome_build`` is the build ingestion verified (``SpliceAILoadStats``), not a
    constant: the scores are joined directly by position, so only GRCh37 is
    accepted. Must be written to reference.db so the Update Manager / Database
    Stats surface a row.
    """
    from backend.db.database_registry import _record_db_version

    if genome_build != "GRCh37":
        raise ValueError(f"only GRCh37 SpliceAI scores can be recorded; got {genome_build}")

    _record_db_version(
        engine,
        db_name="spliceai",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
        genome_build=genome_build,
    )
