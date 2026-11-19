"""Measure a specific historical fallback through the local variant-detail router."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

EXPECTED_SHA = "1786b5bc1a6f5a0440239f40d5f5ac69d15ce213015a9cbf11affa05bbedfff0"
EXAMPLE = "rs1051266"


def measure(directory: Path) -> dict:
    # Import the app only after the caller sets the isolated data directory.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from backend.api.routes.export import router as export_router
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
        jobs,
        raw_variants,
        reference_metadata,
        samples,
    )
    from backend.db.vep_version import resolve_effective_vep_bundle_version

    settings = Settings(data_dir=directory)
    registry = DBRegistry(settings)
    bundle = DATABASES["vep_bundle"]
    path = settings.vep_bundle_db_path
    (directory / "samples").mkdir(exist_ok=True)
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
        if not install_committed_bundle(bundle, settings):
            raise ValueError("Explicit fallback installation failed")
        with reference.begin() as conn:
            install_stamp = conn.scalar(version_query)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            chrom, pos, ref, alt, transcript, protein = conn.execute(
                "SELECT chrom,pos,ref,alt,transcript_id,hgvs_protein "
                "FROM vep_annotations WHERE rsid = ?",
                (EXAMPLE,),
            ).fetchone()
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.include_router(export_router, prefix="/api")
        controls = {}
        with patch.object(connection, "_registry", registry), TestClient(app) as client:
            from backend.tasks.huey_tasks import create_annotation_job, run_annotation_task

            # Both calls are manufactured from the public worked example. The
            # archived GRCh37 reference base is T. No person's data is an input.
            for sample_id, (label, call) in enumerate(
                [("heterozygous", ref + alt), ("reference_homozygous", "TT")], start=1
            ):
                relative_path = f"samples/sample_{sample_id}.db"
                with reference.begin() as conn:
                    conn.execute(
                        samples.insert().values(
                            id=sample_id,
                            name=label,
                            db_path=relative_path,
                            file_format="23andme_v5",
                            file_hash=f"synthetic-{sample_id}",
                        )
                    )
                sample = registry.get_sample_engine(directory / relative_path)
                with sample.begin() as conn:
                    conn.execute(
                        raw_variants.insert().values(
                            rsid=EXAMPLE, chrom=chrom, pos=pos, genotype=call
                        )
                    )
                job_id = create_annotation_job(sample_id)
                run_annotation_task.call_local(sample_id, job_id)
                with reference.connect() as conn:
                    job_state = conn.scalar(select(jobs.c.status).where(jobs.c.job_id == job_id))
                with sample.connect() as conn:
                    stamp = conn.scalar(
                        select(annotation_state.c.value).where(
                            annotation_state.c.key == "vep_bundle_version"
                        )
                    )
                    count = conn.scalar(select(func.count()).select_from(annotated_variants))
                    stored = conn.execute(
                        select(
                            annotated_variants.c.ref,
                            annotated_variants.c.alt,
                            annotated_variants.c.zygosity,
                            annotated_variants.c.hgvs_protein,
                        ).where(annotated_variants.c.rsid == EXAMPLE)
                    ).one()
                if job_state not in {"complete", "partial"} or count != 1 or stamp != effective:
                    raise ValueError(
                        f"Unexpected historical annotation state: {job_state}, {count}, {stamp}"
                    )
                params = {"sample_id": sample_id}
                ok = client.get(f"/api/variants/{EXAMPLE}", params=params)
                absent = client.get("/api/variants/rsSyntheticAbsent", params=params)
                ok.raise_for_status()
                body = ok.json()
                payload = {**params, "filter": {"combinator": "and", "rules": []}, "format": "vcf"}
                exports = {}
                for field, all_positions in [("carried", False), ("all_positions", True)]:
                    resp = client.post(
                        "/api/export/query",
                        json={**payload, "include_all_positions": all_positions},
                    )
                    resp.raise_for_status()
                    rows = [
                        line.split("\t")
                        for line in resp.text.splitlines()
                        if not line.startswith("#")
                    ]
                    if any(len(row) != 10 or row[2] != EXAMPLE for row in rows):
                        raise ValueError("Unexpected VCF records")
                    exports[field] = [{"ref": row[3], "alt": row[4], "gt": row[9]} for row in rows]
                controls[label] = {
                    "job_state": job_state,
                    "sample_annotation_stamp": stamp,
                    "stored_rows": count,
                    "stored": dict(stored._mapping),
                    "primary_protein": body["hgvs_protein"],
                    "live_transcript_proteins": [r["hgvs_protein"] for r in body["transcripts"]],
                    "example_status": ok.status_code,
                    "absent_status": absent.status_code,
                    "exports": exports,
                }
        return {
            "status_stamp": status_stamp,
            "effective_after_status": effective,
            "install_stamp": install_stamp,
            "ancestrydna_ingest_blocked": _vep_bundle_blocks_ancestrydna(reference, path)[0],
            "bundle_transcript": transcript,
            "bundle_protein": protein,
            "synthetic_controls": controls,
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
            with (
                redirect_stdout(sys.stderr),
                patch("socket.socket.connect", side_effect=OSError("Offline reproduction")),
            ):
                observed = measure(directory)
            print(json.dumps(observed, indent=2))
