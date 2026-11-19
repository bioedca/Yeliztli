"""Measure a specific historical fallback through the local variant-detail router."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import socket
import sqlite3
import sys
import tempfile
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

EXPECTED_SHA = "1786b5bc1a6f5a0440239f40d5f5ac69d15ce213015a9cbf11affa05bbedfff0"
EXAMPLE = "rs1051266"


def require_local_ipc() -> None:
    """The in-process HTTP client's event loop must be able to wake its worker."""
    try:
        reader, writer = socket.socketpair()
        with reader, writer:
            reader.settimeout(2)
            writer.settimeout(2)
            writer.sendall(b"x")
            if reader.recv(1) != b"x":
                raise OSError("Unexpected local socket-pair result")
    except OSError as error:
        raise RuntimeError(
            "This offline probe requires local socket-pair communication"
        ) from error


def vcf_observation(text: str, expected_chrom: str, expected_pos: int) -> list[dict]:
    """Read the one-sample export and bind the genotype to its declared locus and FORMAT."""
    lines = text.splitlines()
    gt_declarations = [line for line in lines if line.startswith("##FORMAT=<ID=GT,")]
    headers = [i for i, line in enumerate(lines) if line.startswith("#CHROM\t")]
    if (
        len(gt_declarations) != 1
        or not gt_declarations[0].startswith("##FORMAT=<ID=GT,Number=1,Type=String,")
        or len(headers) != 1
    ):
        raise ValueError("Expected a single VCF header and GT declaration")
    header = lines[headers[0]].split("\t")
    columns = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"]
    if (
        header[:-1] != columns
        or not header[-1]
        or any(not line.startswith("##") for line in lines[: headers[0]])
    ):
        raise ValueError("Expected one sample and the standard VCF columns")
    records = []
    for line in lines[headers[0] + 1 :]:
        row = line.split("\t")
        if len(row) != 10 or row[:3] != [expected_chrom, str(expected_pos), EXAMPLE]:
            raise ValueError("Unexpected exported locus or VCF record")
        keys, values = row[8].split(":"), row[9].split(":")
        if keys.count("GT") != 1 or len(set(keys)) != len(keys) or len(keys) != len(values):
            raise ValueError("Missing or ambiguous sample genotype in VCF FORMAT")
        records.append(
            {
                "chrom": row[0],
                "pos": int(row[1]),
                "rsid": row[2],
                "ref": row[3],
                "alt": row[4],
                "format": row[8],
                "gt": values[keys.index("GT")],
            }
        )
    return records


def measure(directory: Path) -> dict:
    require_local_ipc()
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
        clinvar_variants,
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

            # The later controls add only public site identity from a repository
            # fixture. Every sample is manufactured, and the earlier controls
            # complete before this shared reference gains its ClinVar row.
            fixture_path = Path("tests/fixtures/seed_csvs/clinvar_seed.csv")
            with fixture_path.open(newline="") as handle:
                (site,) = [row for row in csv.DictReader(handle) if row["rsid"] == EXAMPLE]
            if (site["chrom"], int(site["pos"]), site["ref"], site["alt"]) != (
                "21",
                46957794,
                "T",
                "C",
            ):
                raise ValueError("The historical ClinVar control fixture has changed")
            reference_call = site["ref"] * 2
            for sample_id, (label, call) in enumerate(
                [
                    ("heterozygous", ref + alt),
                    ("reference_homozygous", reference_call),
                    ("clinvar_reference_homozygous", reference_call),
                    ("clinvar_heterozygous", ref + alt),
                ],
                start=1,
            ):
                if label == "clinvar_reference_homozygous":
                    with reference.begin() as conn:
                        conn.execute(
                            clinvar_variants.insert().values(
                                rsid=EXAMPLE,
                                chrom=site["chrom"],
                                pos=int(site["pos"]),
                                ref=site["ref"],
                                alt=site["alt"],
                                review_stars=int(site["review_stars"]),
                            )
                        )
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
                    exports[field] = vcf_observation(resp.text, site["chrom"], int(site["pos"]))
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
        observation = {
            "status_stamp": status_stamp,
            "effective_after_status": effective,
            "install_stamp": install_stamp,
            "ancestrydna_ingest_blocked": _vep_bundle_blocks_ancestrydna(reference, path)[0],
            "bundle_transcript": transcript,
            "bundle_protein": protein,
            "synthetic_controls": controls,
        }
        baseline_path = Path(__file__).parent / "raw/fallback-baseline.json"
        baseline = json.loads(baseline_path.read_text())["isolated_route"]
        if observation != baseline:
            raise ValueError(
                "Observed worker, API or VCF output differs from the historical baseline"
            )
        return observation
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
