"""Measure site overlap and storage using two pinned, public SQLite artifacts."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def read_only(path, digest):
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
            raise ValueError(f"Unexpected SHA-256 for {path.name}")
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


def check_sentinel(conn):
    """Verify the published T>C sentinel against the retained GRCh37 response."""
    sources = json.loads((Path(__file__).parent / "raw/source-metadata.json").read_text())
    key = "/vep/human/region/21:46957794-46957794:1/C"
    location = sources["grch37_sentinel"][key]["selected_fields"]
    (transcript,) = location["transcript_consequences"]
    ref, alt = location["allele_string"].split("/")
    tx_id, _version = transcript["transcript_id"].rsplit(".", 1)
    expected = (
        location["seq_region_name"],
        location["start"],
        ref,
        alt,
        tx_id,
        {1: "+", -1: "-"}[transcript["strand"]],
        transcript["hgvsc"].split(":", 1)[1],
        transcript["hgvsp"].split(":", 1)[1],
    )
    columns = (
        "chrom",
        "pos",
        "ref",
        "alt",
        "transcript_id",
        "strand",
        "hgvs_coding",
        "hgvs_protein",
    )
    rows = conn.execute(
        "SELECT " + ", ".join(columns) + " FROM full_release.vep_annotations "
        "WHERE rsid = ? AND alt = ? AND transcript_id = ?",
        ("rs1051266", alt, tx_id),
    ).fetchall()
    if rows != [expected]:
        raise ValueError("Published sentinel differs from the retained versioned GRCh37 response")
    return dict(zip(columns, rows[0], strict=True))


def compare(release):
    legacy = read_only(
        Path("bundles/vep_bundle.db"),
        "1786b5bc1a6f5a0440239f40d5f5ac69d15ce213015a9cbf11affa05bbedfff0",
    )
    verified = read_only(
        release, "c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3"
    )
    with closing(sqlite3.connect(":memory:", uri=True)) as conn:
        conn.execute("ATTACH ? AS old", (legacy,))
        conn.execute("ATTACH ? AS full_release", (verified,))
        for namespace in ("old", "full_release"):
            if conn.execute(f"SELECT count(*) FROM {namespace}.vep_annotations").fetchone()[0] < 1:
                raise ValueError("Empty comparison source")
        # Use the release columns and equivalent indexes. Keep only the site list
        # from the legacy file. Alleles and annotations come from the release.
        conn.execute("""
            CREATE TABLE main.vep_annotations AS
            SELECT r.* FROM old.vep_annotations AS l
            CROSS JOIN full_release.vep_annotations AS r INDEXED BY idx_vep_rsid
            WHERE r.rsid=l.rsid AND r.chrom=l.chrom AND r.pos=l.pos
        """)
        conn.execute("CREATE INDEX main.idx_vep_rsid ON vep_annotations(rsid)")
        conn.execute("CREATE INDEX main.idx_vep_chrom_pos ON vep_annotations(chrom, pos)")
        rows, covered, proteins = conn.execute("""
            SELECT count(*), count(DISTINCT rsid), sum(length(coalesce(hgvs_protein, '')) > 0)
            FROM main.vep_annotations
        """).fetchone()
        total = conn.execute("SELECT count(*) FROM old.vep_annotations").fetchone()[0]
        if (
            total
            != conn.execute("SELECT count(DISTINCT rsid) FROM old.vep_annotations").fetchone()[0]
        ):
            raise ValueError("Legacy sites must be unique by rsID for these denominators")
        rsid_only = conn.execute("""
            SELECT count(*) FROM old.vep_annotations l WHERE EXISTS
            (SELECT 1 FROM full_release.vep_annotations r WHERE r.rsid=l.rsid)
        """).fetchone()[0]
        resolved = conn.execute("""
            SELECT count(DISTINCT l.rsid)
            FROM old.vep_annotations l CROSS JOIN main.vep_annotations r INDEXED BY idx_vep_rsid
            WHERE l.rsid=r.rsid
              AND ((l.ref=r.ref AND l.alt=r.alt) OR (l.ref=r.alt AND l.alt=r.ref))
        """).fetchone()[0]
        pages = conn.execute("PRAGMA main.page_count").fetchone()[0]
        page_bytes = conn.execute("PRAGMA main.page_size").fetchone()[0]
        return {
            "v4_sentinel": check_sentinel(conn),
            "legacy_sites": total,
            "sites_matching_rsid": rsid_only,
            "sites_matching_rsid_and_position": covered,
            "sites_without_matching_rsid_and_position": total - covered,
            "sites_with_exact_unordered_allele_pair": resolved,
            "release_rows_at_matching_sites": rows,
            "release_rows_with_protein_name": proteins,
            "two_index_annotation_table_size_bytes": pages * page_bytes,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    print(json.dumps(compare(parser.parse_args().release), indent=2))
