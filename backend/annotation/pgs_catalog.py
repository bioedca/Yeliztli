"""PGS Catalog harmonized scoring-file ingestion (SW-B1 / roadmap #6).

Ingests PGS Catalog **GRCh37-harmonized** scoring files
(``PGS######_hmPOS_GRCh37.txt.gz``) into a standalone ``pgs_scores.db``, so the
generic PRS engine (:mod:`backend.analysis.prs`) can score against published
weight sets "at scale" instead of a handful of hand-curated JSON weights.

Three guardrails the plan (SW-B1) requires:

1. **Build firewall.** The harmonized file's ``#HmPOS_build`` header must be
   ``GRCh37`` (the app is GRCh37-native). A GRCh38 file is *rejected*, never
   silently coordinate-mismatched.
2. **Per-score license honoring.** Each PGS has its own license. Under the
   project's (A) non-commercial posture we **bundle only clearly-permissive
   scores** (CC0 / CC-BY, attribution honored in NOTICE); everything else —
   non-commercial, author-restricted, or the PGS Catalog *default* "used in
   accordance with any licensing restrictions set by the authors" — is flagged
   ``bundle_ok = False`` and stays **user-fetch only**. The classifier defaults
   to *not* bundleable when in doubt.
3. **No silent data wipe.** A clear-then-load that parses zero weight rows
   raises rather than truncating an existing score (mirrors the ClinGen/CPIC
   empty-parse guards).

The harmonized file carries GRCh37 coordinates in the ``hm_chr``/``hm_pos``
columns and the harmonized rsID in ``hm_rsID``; the score weights are
``effect_allele`` / ``other_allele`` / ``effect_weight``. Column order varies
between scores, so parsing is **column-name keyed**, never positional.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import sqlalchemy as sa

from backend.annotation.http_download import stream_download

# Harmonized GRCh37 scoring file (positions) + REST metadata (license).
PGS_FTP_TEMPLATE = (
    "https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/"
    "{pgs_id}/ScoringFiles/Harmonized/{pgs_id}_hmPOS_GRCh37.txt.gz"
)
PGS_REST_TEMPLATE = "https://www.pgscatalog.org/rest/score/{pgs_id}"

EXPECTED_BUILD = "GRCh37"

_PGS_ID_RE = re.compile(r"^PGS\d{6}$")

_metadata = sa.MetaData()

pgs_score_metadata = sa.Table(
    "pgs_score_metadata",
    _metadata,
    sa.Column("pgs_id", sa.Text, primary_key=True),
    sa.Column("pgs_name", sa.Text),
    sa.Column("trait_reported", sa.Text),
    sa.Column("trait_efo", sa.Text),
    sa.Column("genome_build", sa.Text, nullable=False),  # HmPOS_build (always GRCh37 here)
    sa.Column("variants_number", sa.Integer),
    sa.Column("weight_type", sa.Text),
    sa.Column("license", sa.Text),
    # 1 if the license clearly permits redistribution (CC0/CC-BY) → bundle-eligible.
    sa.Column("license_bundle_ok", sa.Integer, nullable=False),
    sa.Column("citation", sa.Text),
    sa.Column("pgp_id", sa.Text),
)

pgs_score_weights = sa.Table(
    "pgs_score_weights",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("pgs_id", sa.Text, nullable=False),
    sa.Column("rsid", sa.Text),
    sa.Column("chrom", sa.Text, nullable=False),
    sa.Column("pos", sa.Integer, nullable=False),
    sa.Column("effect_allele", sa.Text, nullable=False),
    sa.Column("other_allele", sa.Text),
    sa.Column("effect_weight", sa.Float, nullable=False),
)

sa.Index("idx_pgs_weights_pgs_id", pgs_score_weights.c.pgs_id)
sa.Index("idx_pgs_weights_rsid", pgs_score_weights.c.rsid)


@dataclass
class PgsLoadStats:
    """Outcome of ingesting one harmonized scoring file."""

    pgs_id: str
    loaded: int
    skipped: int
    genome_build: str
    license_bundle_ok: bool


def create_pgs_tables(engine: sa.Engine) -> None:
    """Create the PGS Catalog tables if absent (idempotent)."""
    _metadata.create_all(engine)


def classify_pgs_license(license_text: str | None) -> tuple[bool, str]:
    """Classify a PGS license string into (bundle_ok, normalized_label).

    Under the project's (A) non-commercial posture, only **CC0** and
    **CC-BY** (attribution, *not* the NC variant) are bundle-eligible. Anything
    non-commercial, author-restricted, the PGS Catalog default terms, or
    unspecified is *not* bundleable (user-fetch only). Conservative by design:
    unknown → not bundleable.
    """
    if not license_text or not license_text.strip():
        return (False, "unspecified")
    t = license_text.lower()
    # Non-commercial is never bundled, even though the project is non-commercial
    # (the owner decision keeps NC sources user-fetch).
    if "non-commercial" in t or "noncommercial" in t or "-nc" in t or " nc " in t or "by-nc" in t:
        return (False, "non-commercial")
    if "cc0" in t or "public domain" in t or "publicdomain" in t:
        return (True, "CC0")
    if "cc-by" in t or "cc by" in t or "creative commons attribution" in t:
        return (True, "CC-BY")
    # The PGS Catalog default ("used in accordance with any licensing restrictions
    # set by the authors") defers to authors → not clearly redistributable.
    return (False, "author-restricted")


def parse_pgs_header(lines: list[str]) -> dict[str, str]:
    """Parse ``#key=value`` header lines from a PGS scoring file.

    Section banners (``##...`` / ``###...`` with no ``=``) are ignored. Returns a
    flat dict, e.g. ``{"pgs_id": "PGS000001", "HmPOS_build": "GRCh37", ...}``.
    """
    out: dict[str, str] = {}
    for line in lines:
        if not line.startswith("#"):
            break
        body = line.lstrip("#").strip()
        if "=" not in body:
            continue
        key, _, value = body.partition("=")
        out[key.strip()] = value.strip()
    return out


def _open_maybe_gzip(path: Path) -> IO[str]:
    """Open a possibly-gzipped text file as a UTF-8 text stream."""
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return p.open("r", encoding="utf-8")


def load_pgs_score_from_file(
    path: Path,
    engine: sa.Engine,
    *,
    license_text: str | None = None,
    clear_existing: bool = True,
) -> PgsLoadStats:
    """Ingest one harmonized GRCh37 scoring file into ``pgs_scores.db``.

    Args:
        path: Path to ``PGS######_hmPOS_GRCh37.txt.gz`` (or plain ``.txt``).
        engine: Target standalone-DB engine.
        license_text: License string from the PGS Catalog REST API (the file
            header does not carry it). Drives the bundle-eligibility flag.
        clear_existing: Delete any prior rows for this ``pgs_id`` first.

    Raises:
        ValueError: if the harmonized build is not GRCh37, the header lacks a
            ``pgs_id``, or the table parses to zero weight rows (no silent wipe).
    """
    create_pgs_tables(engine)

    with _open_maybe_gzip(path) as fh:
        raw_lines = fh.read().splitlines()

    header = parse_pgs_header(raw_lines)
    pgs_id = header.get("pgs_id", "")
    if not _PGS_ID_RE.match(pgs_id):
        raise ValueError(f"Missing/invalid pgs_id in header: {pgs_id!r}")

    build = header.get("HmPOS_build", "")
    if build != EXPECTED_BUILD:
        raise ValueError(
            f"{pgs_id}: harmonized build is {build!r}, expected {EXPECTED_BUILD!r} "
            f"— refusing to ingest a build-mismatched score."
        )

    # Locate the column-header row (first non-'#' line) and map names → indices.
    col_row_idx = next((i for i, ln in enumerate(raw_lines) if not ln.startswith("#")), None)
    if col_row_idx is None:
        raise ValueError(f"{pgs_id}: no variant table found")
    cols = raw_lines[col_row_idx].split("\t")
    idx = {name: i for i, name in enumerate(cols)}

    def col(*names: str) -> int | None:
        for n in names:
            if n in idx:
                return idx[n]
        return None

    i_chrom = col("hm_chr", "chr_name")
    i_pos = col("hm_pos", "chr_position")
    i_ea = col("effect_allele")
    i_oa = col("other_allele", "hm_inferOtherAllele")
    i_w = col("effect_weight")
    i_rs = col("hm_rsID", "rsID")
    if i_chrom is None or i_pos is None or i_ea is None or i_w is None:
        raise ValueError(f"{pgs_id}: required columns missing (have {cols})")

    rows: list[dict] = []
    skipped = 0
    for line in raw_lines[col_row_idx + 1 :]:
        if not line.strip():
            continue
        parts = line.split("\t")
        try:
            chrom = parts[i_chrom].strip()
            pos = int(parts[i_pos])
            ea = parts[i_ea].strip().upper()
            weight = float(parts[i_w])
        except (IndexError, ValueError):
            skipped += 1  # unmapped/missing coordinate or weight → skip, don't crash
            continue
        if not chrom or not ea:
            skipped += 1
            continue
        oa = parts[i_oa].strip().upper() if (i_oa is not None and i_oa < len(parts)) else None
        rsid = parts[i_rs].strip() if (i_rs is not None and i_rs < len(parts)) else None
        rows.append(
            {
                "pgs_id": pgs_id,
                "rsid": rsid or None,
                "chrom": chrom,
                "pos": pos,
                "effect_allele": ea,
                "other_allele": oa or None,
                "effect_weight": weight,
            }
        )

    if not rows:
        raise ValueError(
            f"{pgs_id}: parsed zero weight rows — refusing to clear/replace an "
            f"existing score with empty data."
        )

    bundle_ok, _label = classify_pgs_license(license_text)
    n_raw = header.get("variants_number", "")
    variants_number = int(n_raw) if n_raw.isdigit() else None

    with engine.begin() as conn:
        if clear_existing:
            conn.execute(pgs_score_weights.delete().where(pgs_score_weights.c.pgs_id == pgs_id))
            conn.execute(pgs_score_metadata.delete().where(pgs_score_metadata.c.pgs_id == pgs_id))
        conn.execute(
            pgs_score_metadata.insert().values(
                pgs_id=pgs_id,
                pgs_name=header.get("pgs_name"),
                trait_reported=header.get("trait_reported"),
                trait_efo=header.get("trait_efo"),
                genome_build=build,
                variants_number=variants_number,
                weight_type=header.get("weight_type"),
                license=license_text,
                license_bundle_ok=1 if bundle_ok else 0,
                citation=header.get("citation"),
                pgp_id=header.get("pgp_id"),
            )
        )
        for i in range(0, len(rows), 1000):
            conn.execute(pgs_score_weights.insert(), rows[i : i + 1000])

    return PgsLoadStats(
        pgs_id=pgs_id,
        loaded=len(rows),
        skipped=skipped,
        genome_build=build,
        license_bundle_ok=bundle_ok,
    )


def fetch_pgs_license(pgs_id: str, *, timeout: float = 30.0) -> str | None:
    """Fetch a score's license string from the PGS Catalog REST API (best-effort)."""
    if not _PGS_ID_RE.match(pgs_id):
        raise ValueError(f"Invalid pgs_id: {pgs_id!r}")
    url = PGS_REST_TEMPLATE.format(pgs_id=pgs_id)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https REST)
        data = json.load(resp)
    return data.get("license")


def download_pgs_score(
    pgs_id: str,
    dest_dir: Path,
    *,
    timeout: float = 600.0,
) -> Path:
    """Download a score's harmonized GRCh37 scoring file (atomic rename on success).

    Heavy bulk fetches belong on the cluster; this is the single-score primitive.
    """
    if not _PGS_ID_RE.match(pgs_id):
        raise ValueError(f"Invalid pgs_id: {pgs_id!r}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{pgs_id}_hmPOS_GRCh37.txt.gz"
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    stream_download(
        PGS_FTP_TEMPLATE.format(pgs_id=pgs_id), tmp_path, timeout=timeout, resumable=True
    )
    tmp_path.rename(dest_path)
    return dest_path


def download_and_load_pgs_score(
    pgs_id: str,
    engine: sa.Engine,
    dest_dir: Path,
    *,
    timeout: float = 600.0,
) -> PgsLoadStats:
    """Fetch a score's license + harmonized GRCh37 file and ingest it.

    The license drives bundle-eligibility; a build-mismatched file is rejected by
    :func:`load_pgs_score_from_file`.
    """
    license_text = None
    try:
        license_text = fetch_pgs_license(pgs_id, timeout=min(timeout, 30.0))
    except Exception:  # noqa: BLE001 — license is best-effort; absence → not bundleable
        license_text = None
    path = download_pgs_score(pgs_id, dest_dir, timeout=timeout)
    return load_pgs_score_from_file(path, engine, license_text=license_text)


def list_scores(engine: sa.Engine, *, bundle_ok_only: bool = False) -> list[dict]:
    """List ingested PGS scores (metadata), optionally only bundle-eligible ones."""
    stmt = sa.select(pgs_score_metadata)
    if bundle_ok_only:
        stmt = stmt.where(pgs_score_metadata.c.license_bundle_ok == 1)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt.order_by(pgs_score_metadata.c.pgs_id))]


def load_score_weights(engine: sa.Engine, pgs_id: str) -> list[dict]:
    """Return the (rsid, chrom, pos, effect_allele, other_allele, weight) rows for a score."""
    stmt = sa.select(pgs_score_weights).where(pgs_score_weights.c.pgs_id == pgs_id)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]
