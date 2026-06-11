"""GTEx eQTL regulatory-association ingestion (SW-F3 / roadmap #39).

Ingests GTEx v8 **open-access** significant cis-eQTL variant-gene pairs into a
standalone ``gtex_eqtl.db`` so the app can show, for a sample's *typed non-coding*
variants, which gene-expression associations GTEx reports — as **regulatory
context only**, never as a causal-mechanism claim or ACMG evidence.

GTEx's eQTL files are GRCh38: the ``variant_id`` is ``chrN_pos_ref_alt_b38``. To
join against the app's GRCh37 / rsID-keyed sample data **without a coordinate
liftover**, we map each ``variant_id`` to its dbSNP rsID via GTEx's WGS lookup
table (``rs_id_dbSNP151_GRCh38p7`` column) and store rsID-keyed rows; pairs whose
variant has no rsID are skipped (they cannot be matched to array data anyway).

Guardrails:

* **Association ≠ mechanism.** An eQTL is a statistical association with
  expression; the causal variant is often a correlated LD neighbor. This layer is
  context-only and is deliberately *not* fed into ACMG (no PP3/PS3 uplift) — see
  :data:`backend.disclaimers.GTEX_EQTL_CONTEXT_ONLY`.
* **No silent wipe.** A clear-then-load that parses zero rows raises (mirrors the
  ClinGen/CPIC/PGS empty-parse guards).

GTEx open-access summary statistics (eQTL results) are redistributable (the
protected data is the individual-level WGS, not these summaries); cite the GTEx
Consortium (2020, PMID 32913098).
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import sqlalchemy as sa

# GTEx Consortium 2020 (Science) — the v8 atlas.
GTEX_PMID = "32913098"

# variant_id format: chr1_64764_C_T_b38  (GRCh38, 'chr'-prefixed).
_VARIANT_ID_RE = re.compile(r"^chr([0-9XYM]+)_(\d+)_([ACGT]+)_([ACGT]+)_b38$", re.IGNORECASE)

_metadata = sa.MetaData()

gtex_eqtl = sa.Table(
    "gtex_eqtl",
    _metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("rsid", sa.Text, nullable=False),
    sa.Column("gene_id", sa.Text, nullable=False),  # Ensembl gene ID (ENSG...)
    sa.Column("tissue", sa.Text, nullable=False),
    sa.Column("chrom", sa.Text),  # GRCh38 chrom (no 'chr')
    sa.Column("pos", sa.Integer),  # GRCh38 pos
    sa.Column("pval_nominal", sa.Float),
    sa.Column("slope", sa.Float),  # effect size; sign = direction on expression
)

sa.Index("idx_gtex_eqtl_rsid", gtex_eqtl.c.rsid)
sa.Index("idx_gtex_eqtl_gene", gtex_eqtl.c.gene_id)


@dataclass
class GtexLoadStats:
    """Outcome of ingesting one tissue's significant eQTL pairs."""

    tissue: str
    loaded: int
    skipped_no_rsid: int
    skipped_bad_row: int


def create_gtex_tables(engine: sa.Engine) -> None:
    """Create the GTEx eQTL table if absent (idempotent)."""
    _metadata.create_all(engine)


def _float_at(parts: list[str], i: int | None) -> float | None:
    """Parse ``parts[i]`` as float, or None if missing/blank/non-numeric."""
    if i is None or i >= len(parts):
        return None
    try:
        return float(parts[i])
    except ValueError:
        return None


def _open_maybe_gzip(path: Path) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return p.open("r", encoding="utf-8")


def parse_variant_id(variant_id: str) -> tuple[str, int] | None:
    """Parse a GTEx ``chrN_pos_ref_alt_b38`` id → (chrom-without-chr, pos), or None."""
    m = _VARIANT_ID_RE.match(variant_id.strip())
    if not m:
        return None
    return (m.group(1).upper(), int(m.group(2)))


def load_variant_rsid_lookup(path: Path) -> dict[str, str]:
    """Load GTEx's WGS lookup table → {variant_id: rsID}.

    Reads the ``variant_id`` and ``rs_id_dbSNP151_GRCh38p7`` columns (name-keyed).
    Entries with no rsID (``.`` / empty) are omitted.
    """
    out: dict[str, str] = {}
    with _open_maybe_gzip(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        i_vid = idx.get("variant_id")
        i_rs = next(
            (
                idx[c]
                for c in ("rs_id_dbSNP151_GRCh38p7", "rs_id_dbSNP150_GRCh38p7", "rs_id")
                if c in idx
            ),
            None,
        )
        if i_vid is None or i_rs is None:
            raise ValueError(f"lookup table missing variant_id/rs_id columns (have {header})")
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(i_vid, i_rs):
                continue
            rs = parts[i_rs].strip()
            if rs and rs not in (".", "NA"):
                out[parts[i_vid].strip()] = rs
    return out


def load_gtex_eqtl(
    signif_pairs_path: Path,
    rsid_lookup: dict[str, str],
    tissue: str,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> GtexLoadStats:
    """Ingest one tissue's ``*.signif_variant_gene_pairs.txt(.gz)`` into ``gtex_eqtl.db``.

    Args:
        signif_pairs_path: GTEx significant variant-gene pairs file.
        rsid_lookup: ``{variant_id: rsID}`` from :func:`load_variant_rsid_lookup`.
        tissue: Tissue label (e.g. ``"Whole_Blood"``), normally the filename stem.
        engine: Target standalone-DB engine.
        clear_existing: Delete prior rows for this tissue first.

    Raises:
        ValueError: required columns missing, or zero rows parsed (no silent wipe).
    """
    create_gtex_tables(engine)

    with _open_maybe_gzip(signif_pairs_path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        i_vid = idx.get("variant_id")
        i_gene = idx.get("gene_id")
        if i_vid is None or i_gene is None:
            raise ValueError(f"signif_pairs missing variant_id/gene_id (have {header})")
        i_p = idx.get("pval_nominal")
        i_slope = idx.get("slope")

        rows: list[dict] = []
        skipped_no_rsid = 0
        skipped_bad = 0
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(i_vid, i_gene):
                skipped_bad += 1
                continue
            variant_id = parts[i_vid].strip()
            rsid = rsid_lookup.get(variant_id)
            if not rsid:
                skipped_no_rsid += 1
                continue
            coords = parse_variant_id(variant_id)
            chrom, pos = coords if coords else (None, None)
            rows.append(
                {
                    "rsid": rsid,
                    "gene_id": parts[i_gene].strip(),
                    "tissue": tissue,
                    "chrom": chrom,
                    "pos": pos,
                    "pval_nominal": _float_at(parts, i_p),
                    "slope": _float_at(parts, i_slope),
                }
            )

    if not rows:
        raise ValueError(
            f"{tissue}: parsed zero eQTL rows with an rsID — refusing to clear/replace "
            f"with empty data."
        )

    with engine.begin() as conn:
        if clear_existing:
            conn.execute(gtex_eqtl.delete().where(gtex_eqtl.c.tissue == tissue))
        for i in range(0, len(rows), 1000):
            conn.execute(gtex_eqtl.insert(), rows[i : i + 1000])

    return GtexLoadStats(
        tissue=tissue,
        loaded=len(rows),
        skipped_no_rsid=skipped_no_rsid,
        skipped_bad_row=skipped_bad,
    )


def lookup_eqtls_by_rsids(rsids: list[str], engine: sa.Engine) -> dict[str, list[dict]]:
    """Return ``{rsid: [eQTL rows]}`` for the given rsids (batched IN query)."""
    if not rsids:
        return {}
    out: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for i in range(0, len(rsids), 500):
            batch = rsids[i : i + 500]
            stmt = sa.select(gtex_eqtl).where(gtex_eqtl.c.rsid.in_(batch))
            for r in conn.execute(stmt):
                out.setdefault(r.rsid, []).append(dict(r._mapping))
    return out
