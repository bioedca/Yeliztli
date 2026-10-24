"""Refresh a legacy sample's stored rare-variant derivation before any reader.

The old producer stored pooled MONDO/HPO fields and could suppress rows using
that inheritance. Clearing JSON fields cannot recover the missing membership.
Rebuild the canonical module once, using only the sample's existing inputs.
"""

import json
import os
from pathlib import Path

import sqlalchemy as sa

from backend.analysis.provenance import build_finding_provenance
from backend.analysis.rare_variant_finder import (
    PHENOTYPE_SCOPE_REVISION,
    PHENOTYPE_SCOPE_REVISION_KEY,
    RareVariantFilter,
    find_rare_variants,
    store_rare_variant_findings,
)
from backend.analysis.svg_renderer import render_finding_svg
from backend.db.tables import annotated_variants, annotation_state, findings, samples
from backend.services.sample_phenotype_scope import sample_phenotype_scope_is_current
from backend.services.sex_inference import (
    get_recorded_biological_sex,
    infer_biological_sex,
    resolve_biological_sex,
)
from backend.services.staleness import read_recorded_reference_versions


def refresh_legacy_rare_variant_findings(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    sample_db_path: str | Path,
    data_dir: Path,
) -> bool:
    """Rebuild before the registry publishes this engine; fail closed on error.

    A write reservation serializes competing processes and keeps the revision
    check, input read, replacement and marker in one transaction. The marker
    also covers an old run whose guard suppressed every row. A failed rebuild
    rolls back both findings and marker, and no reader receives the engine.
    Current-scope and non-MONDO samples are left untouched.
    """
    with sample_engine.connect() as conn:
        # Avoid taking a write lock (or inspecting reference identity) for the
        # overwhelmingly common unaffected/current case.
        if not _needs_refresh(conn):
            return False

    # Registry strings predate canonical path spelling. Compare normalized
    # absolute identities, including ./ and .. spellings, without guessing
    # between two registered samples that point at the same file.
    path = Path(os.path.abspath(sample_db_path))
    with reference_engine.connect() as conn:
        rows = conn.execute(sa.select(samples.c.id, samples.c.db_path)).all()
    ids = [
        row.id
        for row in rows
        if row.db_path and Path(os.path.abspath(data_dir / row.db_path)) == path
    ]
    if len(ids) != 1:
        raise ValueError("Cannot identify legacy sample for rare-variant refresh")
    recorded = get_recorded_biological_sex(reference_engine, ids[0])

    with sample_engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            # Another process may have completed the repair while we waited.
            if not _needs_refresh(conn):
                conn.rollback()
                return False
            # Infer from this locked snapshot as well; never open a second
            # sample connection inside the transaction (including in-memory DBs).
            biological_sex = resolve_biological_sex(
                recorded_sex=recorded,
                inferred_sex=infer_biological_sex(conn),
            ).sex
            result = find_rare_variants(
                RareVariantFilter(
                    carried_only=True,
                    inferred_sex=biological_sex,
                    biological_sex=biological_sex,
                ),
                conn,
            )
            prior_provenance = _recorded_finding_provenance(conn)
            store_rare_variant_findings(result, conn)
            _restore_finding_metadata(conn, prior_provenance)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return True


def _finding_locus(rsid: str | None, raw_detail: str | None) -> tuple | None:
    try:
        detail = json.loads(raw_detail) if raw_detail else None
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(detail, dict) or not {"chrom", "pos", "ref", "alt"} <= detail.keys():
        return None
    if not isinstance(detail["chrom"], str) or type(detail["pos"]) is not int:
        return None
    if any(detail[key] is not None and not isinstance(detail[key], str) for key in ("ref", "alt")):
        return None
    return rsid, detail["chrom"], detail["pos"], detail["ref"], detail["alt"]


def _recorded_finding_provenance(conn: sa.Connection) -> dict[tuple, str]:
    recorded: dict[tuple, set[str]] = {}
    for row in conn.execute(
        sa.select(findings.c.rsid, findings.c.detail_json, findings.c.provenance).where(
            findings.c.module == "rare_variants"
        )
    ):
        locus = _finding_locus(row.rsid, row.detail_json)
        if locus is not None and row.provenance is not None:
            recorded.setdefault(locus, set()).add(row.provenance)
    # A conflicting duplicate cannot establish one authoritative annotation
    # provenance. Reconstruct it from the recorded sample inputs instead.
    return {locus: next(iter(values)) for locus, values in recorded.items() if len(values) == 1}


def _restore_finding_metadata(conn: sa.Connection, prior: dict[tuple, str]) -> None:
    # Never stamp an upgraded user's old calls with the currently installed
    # releases. Compact annotation snapshots record versions but not builds;
    # unknown source builds remain explicitly unknown. Existing exact-locus
    # provenance retains its full original build/version metadata byte-for-byte.
    snapshot = {
        source: {"version": version, "genome_build": None}
        for source, version in (read_recorded_reference_versions(conn) or {}).items()
    }
    updates = []
    for row in conn.execute(
        sa.select(
            findings,
            annotated_variants.c.clinvar_accession,
            annotated_variants.c.annotation_coverage,
        )
        .select_from(
            findings.join(annotated_variants, findings.c.rsid == annotated_variants.c.rsid)
        )
        .where(findings.c.module == "rare_variants")
    ):
        provenance = prior.get(_finding_locus(row.rsid, row.detail_json))
        if provenance is None:
            provenance = json.dumps(
                build_finding_provenance(
                    snapshot,
                    rsid=row.rsid,
                    clinvar_accession=row.clinvar_accession,
                    coverage_mask=row.annotation_coverage,
                )
            )
        # Cards are rendered freshly by both the API and reports. Regenerate
        # only their safe availability marker, never a legacy artifact or its
        # untrusted stored path. Newly recovered rows can have cards as well.
        svg_marker = (
            f"svgs/{row.id}.svg" if render_finding_svg(dict(row._mapping)) is not None else None
        )
        updates.append({"_id": row.id, "_provenance": provenance, "_svg_path": svg_marker})
    if updates:
        conn.execute(
            findings.update()
            .where(findings.c.id == sa.bindparam("_id"))
            .values(provenance=sa.bindparam("_provenance"), svg_path=sa.bindparam("_svg_path")),
            updates,
        )


def _needs_refresh(conn: sa.Connection) -> bool:
    revision = conn.execute(
        sa.select(annotation_state.c.value).where(
            annotation_state.c.key == PHENOTYPE_SCOPE_REVISION_KEY
        )
    ).scalar_one_or_none()
    if revision == PHENOTYPE_SCOPE_REVISION or sample_phenotype_scope_is_current(conn):
        return False
    return bool(
        conn.execute(
            sa.select(sa.exists().where(annotated_variants.c.phenotype_source == "mondo_hpo"))
        ).scalar_one()
    )
