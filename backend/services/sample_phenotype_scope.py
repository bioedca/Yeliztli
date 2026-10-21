"""Withhold a sample's pre-migration, gene-wide MONDO/HPO phenotype columns.

A sample annotated before the disease-scope migration (#2238) holds gene-wide
phenotype columns in its own ``annotated_variants`` table: every disease's
terms pooled onto the gene. The live reference gate does not reach them,
because they were copied into the sample at annotation time and are served
straight from its own table - so rebuilding the reference install alone does
not stop an upgraded user seeing them, or a filter acting on them.

Every sample reader that serves or filters on these columns goes through this
module, so the rule lives in one place (#2317). The choice was per-reader
suppression rather than turning a legacy snapshot into a ``require_fresh_sample``
staleness block: the latter would start returning 423 on every sample-scoped
route for existing users until they re-annotate, which is a product change,
and ``find_stale_reference_versions`` already reports ``mondo_hpo`` as stale
so the re-annotation banner fires.

Scoped to ``mondo_hpo``-sourced rows only. OMIM enrichment writes these same
columns from a separate loader with its own phenotype-specific disease ID and
inheritance; it never went through the gene-wide MONDO/HPO merge, so clearing
it would cost a user valid context for a problem it does not have.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from backend.annotation.mondo_hpo import mondo_hpo_install_is_serviceable
from backend.services.staleness import read_recorded_reference_versions

LEGACY_SCOPED_PHENOTYPE_FIELDS: tuple[str, ...] = (
    "disease_name",
    "disease_id",
    "phenotype_source",
    "hpo_terms",
    "inheritance_pattern",
)

_MONDO_HPO_SOURCE = "mondo_hpo"


def sample_phenotype_scope_is_current(sample_engine: sa.Engine) -> bool:
    """Whether this sample's stored phenotype columns came from a scoped install.

    Reads the sample's own recorded reference snapshot rather than the current
    reference install: the columns were copied in at annotation time, so what
    matters is the MONDO/HPO revision that run used, not what is installed now.

    Fails closed. A sample with no recorded snapshot predates the mechanism, so
    its provenance cannot be established and its pooled gene-wide terms are the
    ones this gate exists to withhold.
    """
    versions = read_recorded_reference_versions(sample_engine)
    if not versions:
        return False
    return mondo_hpo_install_is_serviceable(versions.get("mondo_hpo"))


def withhold_legacy_phenotype_fields(data: dict[str, Any]) -> None:
    """Clear a legacy-scope row's ``mondo_hpo`` phenotype fields in place.

    The caller has already established that the sample's scope is not current;
    rows from any other phenotype source are left untouched.
    """
    if data.get("phenotype_source") != _MONDO_HPO_SOURCE:
        return
    for field in LEGACY_SCOPED_PHENOTYPE_FIELDS:
        if field in data:
            data[field] = None


def legacy_phenotype_column_masks(table: sa.Table) -> dict[str, sa.ColumnElement]:
    """SQL expressions that read each phenotype column as NULL on ``mondo_hpo`` rows.

    For a legacy-scope sample, a WHERE clause, projection or post-query filter
    must see the withheld value, not the stored one; otherwise a filter on
    ``inheritance_pattern`` or ``disease_name`` still selects rows by the
    gene-wide merge even though the response then hides it. A NULL
    ``phenotype_source`` compares as unknown, so those rows keep their values.
    """
    is_mondo_hpo = table.c.phenotype_source == _MONDO_HPO_SOURCE
    return {
        field: sa.case((is_mondo_hpo, sa.null()), else_=table.c[field])
        for field in LEGACY_SCOPED_PHENOTYPE_FIELDS
    }
