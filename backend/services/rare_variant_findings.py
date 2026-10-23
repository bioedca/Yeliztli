"""Refresh a legacy sample's stored rare-variant derivation before any reader.

The old producer stored pooled MONDO/HPO fields and could suppress rows using
that inheritance. Clearing JSON fields cannot recover the missing membership.
Rebuild the canonical module once, using only the sample's existing inputs.
"""

from pathlib import Path

import sqlalchemy as sa

from backend.analysis.rare_variant_finder import (
    PHENOTYPE_SCOPE_REVISION,
    PHENOTYPE_SCOPE_REVISION_KEY,
    RareVariantFilter,
    find_rare_variants,
    store_rare_variant_findings,
)
from backend.db.tables import annotated_variants, annotation_state, samples
from backend.services.sample_phenotype_scope import sample_phenotype_scope_is_current
from backend.services.sex_inference import (
    get_recorded_biological_sex,
    infer_biological_sex,
    resolve_biological_sex,
)


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

    path = Path(sample_db_path).absolute()
    candidates = {str(path)}
    try:
        candidates.add(str(path.relative_to(data_dir.absolute())))
    except ValueError:
        pass
    with reference_engine.connect() as conn:
        ids = (
            conn.execute(sa.select(samples.c.id).where(samples.c.db_path.in_(candidates)))
            .scalars()
            .all()
        )
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
            store_rare_variant_findings(result, conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return True


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
