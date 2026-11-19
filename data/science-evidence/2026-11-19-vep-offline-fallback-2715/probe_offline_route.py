"""Measure a specific historical fallback through the local variant-detail router."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

EXPECTED_SHA = "1786b5bc1a6f5a0440239f40d5f5ac69d15ce213015a9cbf11affa05bbedfff0"
EXAMPLE = "rs1051266"


def measure(directory: Path) -> dict:
    # Import the app only after the caller sets the isolated data directory.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from backend.annotation.vep_bundle import annotate_sample_vep
    from backend.api.routes.ingest import _vep_bundle_blocks_ancestrydna
    from backend.api.routes.variant_detail import router
    from backend.config import Settings
    from backend.db import connection
    from backend.db.connection import DBRegistry
    from backend.db.database_registry import (
        DATABASES,
        get_database_status,
        install_committed_bundle,
    )
    from backend.db.tables import (
        annotated_variants,
        annotation_state,
        database_versions,
        raw_variants,
        reference_metadata,
        samples,
    )
    from backend.db.vep_version import resolve_effective_vep_bundle_version

    settings = Settings(data_dir=directory)
    registry = DBRegistry(settings)
    bundle = DATABASES["vep_bundle"]
    path = settings.vep_bundle_db_path
    sample_path = directory / "samples" / "sample_1.db"
    sample_path.parent.mkdir(exist_ok=True)
    try:
        reference = registry.reference_engine
        reference_metadata.create_all(reference)
        status = get_database_status(bundle, settings)
        if (
            not status["downloaded"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != EXPECTED_SHA
        ):
            raise ValueError(
                "Reproduce at the recorded historical revision in a clean source tree"
            )
        version_query = select(database_versions.c.version).where(
            database_versions.c.db_name == "vep_bundle"
        )
        with reference.connect() as conn:
            status_stamp = conn.scalar(version_query)
        effective = resolve_effective_vep_bundle_version(reference, path)
        assert install_committed_bundle(bundle, settings)
        with reference.begin() as conn:
            install_stamp = conn.scalar(version_query)
            conn.execute(
                samples.insert().values(
                    id=1,
                    name="synthetic",
                    db_path="samples/sample_1.db",
                    file_format="23andme_v5",
                    file_hash="synthetic",
                )
            )
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            chrom, pos, ref, alt, protein = conn.execute(
                "SELECT chrom,pos,ref,alt,hgvs_protein FROM vep_annotations WHERE rsid = ?",
                (EXAMPLE,),
            ).fetchone()
        sample = registry.get_sample_engine(sample_path)
        with sample.begin() as conn:
            # An artificial heterozygous call constructed solely from public reference alleles.
            conn.execute(
                raw_variants.insert().values(
                    rsid=EXAMPLE, chrom=chrom, pos=pos, genotype=ref + alt
                )
            )
        stats = annotate_sample_vep(sample, registry.vep_engine)
        with sample.begin() as conn:
            stored = conn.scalar(
                select(annotated_variants.c.hgvs_protein).where(
                    annotated_variants.c.rsid == EXAMPLE
                )
            )
            conn.execute(
                annotation_state.insert().values(key="vep_bundle_version", value=effective)
            )
        app = FastAPI()
        app.include_router(router, prefix="/api")
        with patch.object(connection, "_registry", registry), TestClient(app) as client:
            ok = client.get(f"/api/variants/{EXAMPLE}", params={"sample_id": 1})
            absent = client.get("/api/variants/rsSyntheticAbsent", params={"sample_id": 1})
        ok.raise_for_status()
        body = ok.json()
        return {
            "status_stamp": status_stamp,
            "effective_after_status": effective,
            "install_stamp": install_stamp,
            "ancestrydna_ingest_blocked": _vep_bundle_blocks_ancestrydna(reference, path)[0],
            "annotation_rows_written": stats.rows_written,
            "bundle_protein": protein,
            "stored_protein": stored,
            "primary_protein": body["hgvs_protein"],
            "live_transcript_proteins": [row["hgvs_protein"] for row in body["transcripts"]],
            "example_status": ok.status_code,
            "absent_status": absent.status_code,
        }
    finally:
        registry.dispose_all()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with patch.dict(
            os.environ,
            {
                "YELIZTLI_DATA_DIR": temporary,
                "YELIZTLI_MANIFEST_PATH": str(Path("bundles/manifest.json").resolve()),
            },
        ):
            print(json.dumps(measure(directory), indent=2))
