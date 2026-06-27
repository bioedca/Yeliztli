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
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING

import sqlalchemy as sa
import structlog

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


@dataclass
class SpliceAILoadStats:
    """Outcome of ingesting a SpliceAI precomputed-scores VCF."""

    loaded: int
    skipped_below_threshold: int
    skipped_bad_row: int


def create_spliceai_tables(engine: sa.Engine) -> None:
    """Create the SpliceAI scores table if absent (idempotent)."""
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

    Raises:
        ValueError: zero rows met the threshold (no silent clear/replace with
            empty data — mirrors the GTEx empty-parse guard).
    """
    create_spliceai_tables(engine)

    rows: list[dict] = []
    skipped_below = 0
    skipped_bad = 0
    lines = 0
    with _open_maybe_gzip(vcf_path) as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
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

    if not rows:
        raise ValueError(
            f"parsed zero SpliceAI rows at/above min_ds={min_ds} — refusing to "
            f"clear/replace with empty data (is this a SpliceAI-annotated VCF?)."
        )

    with engine.begin() as conn:
        if clear_existing:
            conn.execute(spliceai_scores.delete())
        for i in range(0, len(rows), 1000):
            conn.execute(spliceai_scores.insert(), rows[i : i + 1000])

    if parse_progress is not None:
        parse_progress(len(rows))
    return SpliceAILoadStats(
        loaded=len(rows),
        skipped_below_threshold=skipped_below,
        skipped_bad_row=skipped_bad,
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
    version: str = SPLICEAI_VERSION,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Record the SpliceAI version in ``database_versions`` (GRCh37).

    The precomputed hg19 scores are GRCh37-coordinate and joined directly by
    position (no liftover), so the recorded build is GRCh37. Must be written to
    reference.db so the Update Manager / Database Stats surface a row.
    """
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="spliceai",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
        genome_build="GRCh37",
    )
