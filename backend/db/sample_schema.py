"""Per-sample database schema.

Each sample gets its own SQLite file (sample_{id}.db). Tables are created
via create_sample_tables() when a new sample is imported — not via Alembic,
since each sample DB is a separate file created at runtime.

Table definitions live in ``backend.db.tables`` (sample_metadata_obj).
This module provides the creation function that materialises those tables
and seeds initial data.

For existing sample databases, ``ensure_sample_schema_current()`` adds missing
schema surfaces and applies narrowly scoped content migrations such as deleting
findings produced by a subsequently quarantined scientific model.
"""

import json
import re

import sqlalchemy as sa
import structlog

from backend.db.tables import PREDEFINED_TAGS, annotation_state, findings, sample_metadata_obj

logger = structlog.get_logger(__name__)

_FINDING_DIFF_STATE_KEY = "last_finding_diff_json"
_QUARANTINED_BREAST_PRS_TRAIT = "breast_cancer"

# Durable handoff from the per-sample v21 content migration to DBRegistry's
# reference-DB prompt synchronizer. ``prompted`` is flipped only after the
# user-visible prompt is persisted; a successful annotation deletes the key.
CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY = "cyp2c9_phenytoin_reanalysis_required_json"
CYP2C9_PHENYTOIN_REANALYSIS_REASON = "cyp2c9_phenytoin_activity_score"
CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION = "legacy phenotype-only phenytoin guidance"
CYP2C9_PHENYTOIN_BUNDLED_GUIDANCE_VERSION = "bundled activity-score guidance"


def _is_quarantined_or_unidentified_cancer_prs_trait(trait: object) -> bool:
    """Whether a legacy cancer-PRS trait cannot safely remain surfaceable."""
    return (
        trait == _QUARANTINED_BREAST_PRS_TRAIT or not isinstance(trait, str) or not trait.strip()
    )


def _is_legacy_cyp2c9_phenytoin_diff_entry(entry: object) -> bool:
    """Whether a finding-diff entry identifies the corrected prescribing alert."""
    return (
        isinstance(entry, dict)
        and entry.get("module") == "pharmacogenomics"
        and entry.get("category") == "prescribing_alert"
        and entry.get("gene_symbol") == "CYP2C9"
        and isinstance(entry.get("drug"), str)
        and entry["drug"].lower() == "phenytoin"
    )


_TPMT_POOR_METABOLIZER_RECOMMENDATION_UPDATES = {
    "azathioprine": (
        frozenset({"Reduce dose to 10% of standard or use alternative agent."}),
        "Consider alternative nonthiopurine immunosuppressant therapy.",
    ),
    "mercaptopurine": (
        frozenset({"Reduce dose to 10% of standard. Consider alternative agent."}),
        "For malignancy: initiate therapy with drastically reduced starting doses. "
        "Reduce starting dose by 10-fold and reduce frequency to thrice weekly instead of "
        "daily (e.g. 10 mg/m2/day given 3 days/week). During therapy, adjust mercaptopurine "
        "doses based on the degree of myelosuppression and disease-specific guidelines. It "
        "usually takes at least 4-6 weeks of stable dosing to reach steady state after each "
        "dose adjustment. If myelosuppression occurs, emphasis should be on reducing "
        "mercaptopurine over other agents. For nonmalignancy: consider alternative "
        "nonthiopurine immunosuppressant therapy.",
    ),
    "thioguanine": (
        frozenset(
            {
                "Start with drastically reduced doses (reduce by 50-75%) and titrate based "
                "on myelosuppression; for nonmalignant conditions consider an alternative "
                "agent.",
                "Start with drastically reduced doses (reduce daily dose by 10-fold and dose "
                "thrice weekly instead of daily) and titrate based on myelosuppression; for "
                "nonmalignant conditions consider an alternative agent.",
            }
        ),
        "Initiate therapy with drastically reduced starting doses. Reduce starting dose by "
        "10-fold and reduce frequency to thrice weekly instead of daily. During therapy, "
        "adjust thioguanine doses based on degree of myelosuppression and disease-specific "
        "guidelines. It usually takes at least 4-6 weeks of stable dosing to reach steady "
        "state after each dose adjustment. If myelosuppression occurs, emphasis should be "
        "on reducing thioguanine over other agents.",
    ),
}
_TPMT_THIOPURINE_GUIDELINE_URL = (
    "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/"
)

_CYP2B6_EFAVIRENZ_RECOMMENDATION_UPDATES = {
    "Intermediate Metabolizer": (
        "Use label-recommended dosing; consider a reduced dose if CNS side effects occur.",
        "Consider initiating efavirenz with decreased dose of 400 mg/day.",
    ),
    "Poor Metabolizer": (
        "Consider initiating at a decreased dose (e.g., 400 mg/day); higher plasma "
        "exposure raises CNS-toxicity risk.",
        "Consider initiating efavirenz with decreased dose of 400 or 200 mg/day.",
    ),
}
_CYP2B6_EFAVIRENZ_GUIDELINE_URL = (
    "https://cpicpgx.org/guidelines/cpic-guideline-for-efavirenz-based-on-cyp2b6-genotype/"
)

_PARKINSONS_RISK_CLASSIFICATION = (
    "LRRK2 G2019S — Parkinson's disease risk factor (reduced penetrance)"
)
_PARKINSONS_LEGACY_FINDING_TEMPLATES = (
    "LRRK2 G2019S (rs34637584 {genotype}) detected. This is the most common known "
    "genetic risk factor for Parkinson's disease, but its penetrance is reduced and "
    "age-dependent: lifetime risk for carriers is estimated at roughly 25-42.5% by "
    "age 80, so the majority of carriers never develop Parkinson's. Risk also varies "
    "by ancestry (the variant is more common in Ashkenazi Jewish and North African "
    "Berber populations) and is modified by other genetic and environmental factors. "
    "A positive result is not a diagnosis and not a prediction that you will develop "
    "Parkinson's.",
    "LRRK2 G2019S (rs34637584 {genotype}) detected. This is the most common known "
    "genetic risk factor for Parkinson's disease, but its penetrance is reduced and "
    "age-dependent. Published age-80 estimates vary by cohort design, ancestry, and "
    "modifier burden: recent cohorts report roughly 24-49%, with kin-cohort estimates "
    "around 25-42.5%, so many carriers never develop Parkinson's. Risk also varies by "
    "ancestry (the variant is more common in Ashkenazi Jewish and North African Berber "
    "populations) and is modified by other genetic and environmental factors. A "
    "positive result is not a diagnosis and not a prediction that you will develop "
    "Parkinson's.",
)
_PARKINSONS_CURRENT_FINDING_TEMPLATE = (
    "LRRK2 G2019S (rs34637584 {genotype}) detected. This is the most common known "
    "genetic risk factor for Parkinson's disease, but its penetrance is reduced and "
    "age-dependent. Published age-80 estimates vary by cohort design, ancestry, and "
    "modifier burden: recent cohorts report roughly 24-49%, with kin-cohort estimates "
    "around 25-42.5%. By age 80, most carriers in these cohorts had not developed "
    "Parkinson's disease. Risk also varies by ancestry (the variant is more common in "
    "Ashkenazi Jewish and North African Berber populations) and is modified by other "
    "genetic and environmental factors. A positive result is not a diagnosis and not "
    "a prediction that you will develop Parkinson's."
)
_PARKINSONS_CURRENT_PMIDS = ("26062626", "28639421", "38804604", "40926580")


def _updated_tpmt_poor_metabolizer_finding_text(
    finding_text: object,
    drug: str,
    legacy_recommendation: str,
    bundled_recommendation: str,
) -> str | None:
    """Replace one generated legacy recommendation without touching its suffix."""
    if not isinstance(finding_text, str):
        return None

    marker = f" -- {drug}: {legacy_recommendation}"
    if finding_text.count(marker) != 1:
        return None

    prefix, suffix = finding_text.split(marker, maxsplit=1)
    if not prefix.startswith("TPMT ") or not prefix.endswith(": Poor Metabolizer"):
        return None
    if suffix not in {
        "",
        " (provisional -- see call confidence note)",
        " (conservative partial call -- see call confidence note)",
    }:
        return None
    return f"{prefix} -- {drug}: {bundled_recommendation}{suffix}"


def _is_legacy_tpmt_poor_metabolizer_diff_entry(entry: object) -> bool:
    """Whether a diff entry exactly identifies a superseded TPMT alert."""
    if not (
        isinstance(entry, dict)
        and entry.get("module") == "pharmacogenomics"
        and entry.get("category") == "prescribing_alert"
        and entry.get("gene_symbol") == "TPMT"
        and entry.get("metabolizer_status") == "Poor Metabolizer"
        and isinstance(entry.get("drug"), str)
    ):
        return False

    update = _TPMT_POOR_METABOLIZER_RECOMMENDATION_UPDATES.get(entry["drug"])
    if update is None:
        return False
    legacy_recommendations, bundled_recommendation = update
    return any(
        _updated_tpmt_poor_metabolizer_finding_text(
            entry.get("finding_text"), entry["drug"], legacy_recommendation, bundled_recommendation
        )
        is not None
        for legacy_recommendation in legacy_recommendations
    )


def _updated_cyp2b6_efavirenz_finding_text(
    finding_text: object,
    phenotype: str,
    legacy_recommendation: str,
    bundled_recommendation: str,
) -> str | None:
    """Replace one generated legacy efavirenz recommendation exactly."""
    if not isinstance(finding_text, str):
        return None

    marker = f" -- efavirenz: {legacy_recommendation}"
    if finding_text.count(marker) != 1:
        return None

    prefix, suffix = finding_text.split(marker, maxsplit=1)
    if not prefix.startswith("CYP2B6 ") or not prefix.endswith(f": {phenotype}"):
        return None
    if suffix not in {
        "",
        " (provisional -- see call confidence note)",
        " (conservative partial call -- see call confidence note)",
    }:
        return None
    return f"{prefix} -- efavirenz: {bundled_recommendation}{suffix}"


def _is_legacy_cyp2b6_efavirenz_diff_entry(entry: object) -> bool:
    """Whether a diff entry exactly identifies a superseded efavirenz alert."""
    if not (
        isinstance(entry, dict)
        and entry.get("module") == "pharmacogenomics"
        and entry.get("category") == "prescribing_alert"
        and entry.get("gene_symbol") == "CYP2B6"
        and entry.get("drug") == "efavirenz"
        and isinstance(entry.get("metabolizer_status"), str)
    ):
        return False

    phenotype = entry["metabolizer_status"]
    update = _CYP2B6_EFAVIRENZ_RECOMMENDATION_UPDATES.get(phenotype)
    if update is None:
        return False
    legacy_recommendation, bundled_recommendation = update
    return (
        _updated_cyp2b6_efavirenz_finding_text(
            entry.get("finding_text"),
            phenotype,
            legacy_recommendation,
            bundled_recommendation,
        )
        is not None
    )


def _updated_parkinsons_finding_text(
    finding_text: object,
    genotype_call: object | None = None,
) -> str | None:
    """Replace one exact generated legacy LRRK2 finding, preserving its call."""
    if not isinstance(finding_text, str):
        return None

    if genotype_call is None:
        match = re.match(
            r"^LRRK2 G2019S \(rs34637584 (rs34637584 [ACGT]{2})\)",
            finding_text,
        )
        candidates = [] if match is None else [match.group(1)]
    elif isinstance(genotype_call, str) and re.fullmatch(r"[ACGT]{2}", genotype_call):
        candidates = [f"rs34637584 {genotype_call}"]
    else:
        return None

    for genotype_text in candidates:
        for legacy_template in _PARKINSONS_LEGACY_FINDING_TEMPLATES:
            if finding_text == legacy_template.format(genotype=genotype_text):
                return _PARKINSONS_CURRENT_FINDING_TEMPLATE.format(genotype=genotype_text)
    return None


def _updated_parkinsons_diff_entry(entry: object) -> dict[str, object] | None:
    """Return a repaired exact Parkinson's finding-diff entry, if applicable."""
    if not (
        isinstance(entry, dict)
        and entry.get("module") == "parkinsons"
        and entry.get("category") == "risk_genotype"
        and entry.get("gene_symbol") == "LRRK2"
        and entry.get("rsid") == "rs34637584"
    ):
        return None
    updated_text = _updated_parkinsons_finding_text(entry.get("finding_text"))
    if updated_text is None:
        return None
    return {**entry, "finding_text": updated_text}


# Current schema version. Bump for per-sample schema or content migrations.
# v7: Add watched_variants table (P4-21g — VUS tracking)
# v8: Add provenance columns to raw_variants + merge_provenance table
#     (AncestryDNA Plan §10.4 — multi-source sample merging)
# v9: Add deleterious_total_assessed to annotated_variants
#     (validation strategy F25 — k-of-present ensemble denominator)
# v10: Add gnomad_af_popmax to annotated_variants
#      (validation strategy F15 — population-max rarity denominator)
# v11: Add provenance column to findings
#      (SW-A4 #8 — per-finding source-release + version pinning, audit metadata)
# v12: Add AlphaMissense context-only columns to annotated_variants
#      (missense pathogenicity prediction metadata; never ACMG evidence)
# v13: Add gnomad_af_asj to annotated_variants
#      (gnomAD r2.1 ASJ population for population-max AF and ancestry display)
# v14: Add imputed_variants table (Wave C — firewall-cleared imputed variants;
#      created on existing sample DBs via create_all(checkfirst=True))
# v15: Add gnomad_source_status to annotated_variants
#      (distinguish observed AFs from variants not assessed by the exome-only source)
# v16: Add dosage column to imputed_variants (SW-C5 — Beagle DS, for PRS scoring;
#      added on existing sample DBs via ALTER TABLE in _add_missing_columns)
# v17: Add best_guess_copies column to imputed_variants (SW-C6 — FORMAT GT/MAP
#      ALT copy count for imputed ClinVar carriage; DS remains PRS metadata)
# v18: Add gnomAD AN columns to annotated_variants so frequency-based ACMG benign
#      criteria can verify the supporting dataset has enough observed alleles.
# v19: Add hla_calls table (Wave D — persisted HIBAG classical-HLA genotype calls;
#      created on existing sample DBs via create_all(checkfirst=True))
# v20: Quarantine persisted outputs from the source-unverified legacy 25-marker
#      breast-cancer PRS (issue #1934). The bundled rows remain as an audit record,
#      but old per-sample findings must not remain surfaceable after deployment.
# v21: Quarantine persisted CYP2C9/phenytoin prescribing alerts created from the
#      pre-#1989 phenotype-only guidance. Fresh analysis regenerates score-keyed
#      alerts from the corrected CPIC rows.
# v22: Repair persisted TPMT Poor Metabolizer thiopurine alerts that contain the
#      exact superseded recommendations corrected by issue #2000.
# v23: Repair persisted CYP2B6 efavirenz Intermediate/Poor Metabolizer alerts
#      whose exact released recommendations were corrected by issue #2012.
# v24: Repair exact persisted LRRK2 G2019S findings and finding-diff entries
#      whose lifetime wording exceeded the cited age-80 evidence (issue #2091).
SAMPLE_SCHEMA_VERSION = 24


# AncestryDNA Plan §10.4(a): merged-sample raw_variants uses (chrom, pos) PK
# instead of rsid PK so the canonical merge key matches the physical PK. The
# in-place v7→v8 upgrade path (existing single-vendor sample DBs) keeps rsid
# PK forever per the plan's "divergence does not apply to in-place v7→v8
# upgrades" clause; this DDL only fires when create_sample_tables is invoked
# with is_merged_sample=True, which only the sample-merge service does.
_RAW_VARIANTS_MERGED_DDL = """
CREATE TABLE raw_variants (
    rsid TEXT NOT NULL,
    chrom TEXT NOT NULL,
    pos INTEGER NOT NULL,
    genotype TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    concordance TEXT NOT NULL DEFAULT '',
    discordant_alt_genotype TEXT NOT NULL DEFAULT '',
    alt_rsid TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (chrom, pos)
)
"""


def create_sample_tables(engine: sa.Engine, *, is_merged_sample: bool = False) -> None:
    """Create all per-sample tables in the given SQLite database.

    Sets WAL journal mode, creates tables from the Core definitions,
    and seeds predefined tags.

    Args:
        engine: SQLAlchemy engine connected to a sample database file.
        is_merged_sample: When ``True``, materialises ``raw_variants`` with
            a composite ``(chrom, pos)`` primary key instead of the default
            ``rsid`` PK (AncestryDNA Plan §10.4a). Passed by
            ``backend/services/sample_merge.py`` when creating a freshly
            merged sample DB; defaults to ``False`` for every other caller
            (regular file ingest, fixtures, tests).
    """
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA journal_mode=WAL"))
        conn.commit()

    if is_merged_sample:
        # Pre-create raw_variants with (chrom, pos) PK via raw DDL; the
        # subsequent sample_metadata_obj.create_all(checkfirst=True) sees
        # the table already exists and skips it, while still creating every
        # other table (annotated_variants, merge_provenance, annotation_state,
        # tags, watched_variants, etc.) from the module-level definitions.
        with engine.begin() as conn:
            conn.execute(sa.text(_RAW_VARIANTS_MERGED_DDL))

    # Create all tables defined in sample_metadata_obj
    sample_metadata_obj.create_all(engine, checkfirst=True)

    # Seed predefined tags (batch insert)
    with engine.connect() as conn:
        conn.execute(
            sa.text("INSERT OR IGNORE INTO tags (name, is_predefined) VALUES (:name, 1)"),
            [{"name": tag_name} for tag_name in PREDEFINED_TAGS],
        )
        conn.commit()

    # Stamp the schema version
    _stamp_schema_version(engine, SAMPLE_SCHEMA_VERSION)


def ensure_sample_schema_current(engine: sa.Engine) -> bool:
    """Ensure an existing sample database has all current tables.

    Uses ``CREATE TABLE IF NOT EXISTS`` (via ``checkfirst=True``) plus
    version-gated forward migrations, so it is safe to call on every sample DB
    open. Returns True when tables, columns, or content changed; a version-only
    stamp preserves the historical False return.

    This replaces Alembic for sample databases (P3-33): since each sample
    is an independent SQLite file created at runtime, a lightweight
    version-check + ``create_all(checkfirst=True)`` is sufficient.

    Args:
        engine: SQLAlchemy engine for a sample database file.

    Returns:
        True if the schema was updated, False if already current.
    """
    current_version = _get_schema_version(engine)

    if current_version >= SAMPLE_SCHEMA_VERSION:
        return False

    # Inspect existing tables before upgrade
    inspector = sa.inspect(engine)
    existing = set(inspector.get_table_names())

    # Add any missing tables (checkfirst=True prevents recreation)
    sample_metadata_obj.create_all(engine, checkfirst=True)

    # Check what was added
    inspector2 = sa.inspect(engine)
    after = set(inspector2.get_table_names())
    added = after - existing

    if added:
        logger.info(
            "sample_schema_upgraded",
            added_tables=sorted(added),
            from_version=current_version,
            to_version=SAMPLE_SCHEMA_VERSION,
        )

    # Add missing columns to existing tables (v3 → v4: findings cross-link columns)
    columns_added = _add_missing_columns(engine, current_version)

    _stamp_schema_version(engine, SAMPLE_SCHEMA_VERSION)
    return bool(added) or columns_added


def _add_missing_columns(engine: sa.Engine, from_version: int) -> bool:
    """Apply per-sample forward migrations after initial table creation.

    Most migrations use ``ALTER TABLE ADD COLUMN`` (with an existing-column
    guard). Content quarantines also live here because the restore workflow
    invokes this helper directly for older sample databases.

    Returns True if columns or content changed.
    """
    added = False

    if from_version < 4:
        # P3-67: Add cross-module link columns to findings table
        inspector = sa.inspect(engine)
        if "findings" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("findings")}
            with engine.begin() as conn:
                if "related_module" not in existing_cols:
                    conn.execute(sa.text("ALTER TABLE findings ADD COLUMN related_module TEXT"))
                    added = True
                if "related_finding_id" not in existing_cols:
                    conn.execute(
                        sa.text("ALTER TABLE findings ADD COLUMN related_finding_id INTEGER")
                    )
                    added = True
            if added:
                # Create index on related_module for cross-module queries
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "CREATE INDEX IF NOT EXISTS idx_findings_related_module "
                            "ON findings (related_module)"
                        )
                    )
                logger.info(
                    "findings_columns_added",
                    columns=["related_module", "related_finding_id"],
                    from_version=from_version,
                )

    if from_version < 6:
        # P4-19: Add GRCh38 liftover columns to annotated_variants
        added_liftover = False
        inspector = sa.inspect(engine)
        if "annotated_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
            with engine.begin() as conn:
                if "chrom_grch38" not in existing_cols:
                    conn.execute(
                        sa.text("ALTER TABLE annotated_variants ADD COLUMN chrom_grch38 TEXT")
                    )
                    added_liftover = True
                if "pos_grch38" not in existing_cols:
                    conn.execute(
                        sa.text("ALTER TABLE annotated_variants ADD COLUMN pos_grch38 INTEGER")
                    )
                    added_liftover = True
            if added_liftover:
                logger.info(
                    "liftover_columns_added",
                    columns=["chrom_grch38", "pos_grch38"],
                    from_version=from_version,
                )
                added = True

    if from_version < 8:
        # AncestryDNA Plan §10.4b: provenance columns on raw_variants.
        # Unmerged samples keep '' defaults; merge service populates on
        # newly-created merged sample DBs.
        added_provenance = False
        inspector = sa.inspect(engine)
        if "raw_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("raw_variants")}
            new_cols = ("source", "concordance", "discordant_alt_genotype", "alt_rsid")
            with engine.begin() as conn:
                for col in new_cols:
                    if col not in existing_cols:
                        conn.execute(
                            sa.text(
                                f"ALTER TABLE raw_variants ADD COLUMN {col} "
                                "TEXT NOT NULL DEFAULT ''"
                            )
                        )
                        added_provenance = True
            if added_provenance:
                logger.info(
                    "raw_variants_provenance_columns_added",
                    columns=list(new_cols),
                    from_version=from_version,
                )
                added = True

    if from_version < 9:
        # Validation strategy F25: record the in-silico ensemble denominator
        # (independent axes actually assessed) so the pathogenic flag is
        # k-of-present, not k-of-a-fixed-5. NULL on existing rows until the
        # sample is re-annotated.
        inspector = sa.inspect(engine)
        if "annotated_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
            if "deleterious_total_assessed" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE annotated_variants "
                            "ADD COLUMN deleterious_total_assessed INTEGER"
                        )
                    )
                logger.info(
                    "deleterious_total_assessed_column_added",
                    from_version=from_version,
                )
                added = True

    if from_version < 10:
        # Validation strategy F15: store the population-max AF so rarity is
        # judged on the most-common ancestry, not the global average. NULL on
        # existing rows until the sample is re-annotated (the rare-variant finder
        # falls back to gnomad_af_global when popmax is NULL).
        inspector = sa.inspect(engine)
        if "annotated_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
            if "gnomad_af_popmax" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(
                        sa.text("ALTER TABLE annotated_variants ADD COLUMN gnomad_af_popmax REAL")
                    )
                logger.info("gnomad_af_popmax_column_added", from_version=from_version)
                added = True

    if from_version < 11:
        # SW-A4 (#8): per-finding provenance + version pinning. A JSON audit
        # snapshot stamped on each finding after analysis. NULL on existing rows
        # until the sample is re-annotated (the provenance pass runs post-analysis).
        inspector = sa.inspect(engine)
        if "findings" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("findings")}
            if "provenance" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(sa.text("ALTER TABLE findings ADD COLUMN provenance TEXT"))
                logger.info("findings_provenance_column_added", from_version=from_version)
                added = True

    if from_version < 12:
        # AlphaMissense is stored as context-only metadata alongside dbNSFP/REVEL.
        # NULL on existing rows until the sample is re-annotated.
        inspector = sa.inspect(engine)
        if "annotated_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
            added_alpha = False
            with engine.begin() as conn:
                if "alphamissense_pathogenicity" not in existing_cols:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE annotated_variants "
                            "ADD COLUMN alphamissense_pathogenicity REAL"
                        )
                    )
                    added_alpha = True
                if "alphamissense_class" not in existing_cols:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE annotated_variants ADD COLUMN alphamissense_class TEXT"
                        )
                    )
                    added_alpha = True
            if added_alpha:
                logger.info("alphamissense_columns_added", from_version=from_version)
                added = True

    if from_version < 13:
        # gnomAD r2.1 includes Ashkenazi Jewish (ASJ) AF. Store it alongside the
        # other per-population AFs so popmax and ancestry-matched displays can
        # use the most-common ancestry instead of silently falling back to global.
        inspector = sa.inspect(engine)
        if "annotated_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
            if "gnomad_af_asj" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(
                        sa.text("ALTER TABLE annotated_variants ADD COLUMN gnomad_af_asj REAL")
                    )
                logger.info("gnomad_af_asj_column_added", from_version=from_version)
                added = True

    if from_version < 15:
        # Issue #1121: the shipped AF bundle is gnomAD r2.1.1 exomes, not a
        # genome-wide source. NULL on existing rows until re-annotation; new
        # rows distinguish observed matches from non-coding variants outside the
        # selected source's assessment scope.
        inspector = sa.inspect(engine)
        if "annotated_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
            if "gnomad_source_status" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE annotated_variants ADD COLUMN gnomad_source_status TEXT"
                        )
                    )
                logger.info("gnomad_source_status_column_added", from_version=from_version)
                added = True

    if from_version < 16:
        # SW-C5: per-sample imputed dosage (Beagle DS) on imputed_variants, used to
        # score PRSs from imputed common variants. Nullable + range CHECK so the
        # ADD COLUMN is valid on an existing (possibly populated) table.
        inspector = sa.inspect(engine)
        if "imputed_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("imputed_variants")}
            if "dosage" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE imputed_variants ADD COLUMN dosage REAL "
                            "CHECK (dosage IS NULL OR (dosage >= 0 AND dosage <= 2))"
                        )
                    )
                logger.info("imputed_variants_dosage_column_added", from_version=from_version)
                added = True

    if from_version < 17:
        # SW-C6: per-sample best-guess genotype copies from FORMAT GT. Nullable so
        # old imputation snapshots are not silently converted from DS rounding; the
        # ClinVar finding source withholds rows without this discrete call.
        inspector = sa.inspect(engine)
        if "imputed_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("imputed_variants")}
            if "best_guess_copies" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "ALTER TABLE imputed_variants "
                            "ADD COLUMN best_guess_copies INTEGER "
                            "CHECK (best_guess_copies IS NULL OR "
                            "best_guess_copies IN (0, 1, 2))"
                        )
                    )
                logger.info(
                    "imputed_variants_best_guess_copies_column_added",
                    from_version=from_version,
                )
                added = True

    if from_version < 18:
        # Issue #1361: gnomAD observed allele counts for BA1/BS1 data-quality
        # guards. NULL on existing rows until samples are re-annotated.
        inspector = sa.inspect(engine)
        if "annotated_variants" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("annotated_variants")}
            new_cols = (
                "gnomad_an_global",
                "gnomad_an_afr",
                "gnomad_an_amr",
                "gnomad_an_asj",
                "gnomad_an_eas",
                "gnomad_an_eur",
                "gnomad_an_fin",
                "gnomad_an_sas",
                "gnomad_an_popmax",
            )
            added_an = False
            with engine.begin() as conn:
                for col in new_cols:
                    if col not in existing_cols:
                        conn.execute(
                            sa.text(f"ALTER TABLE annotated_variants ADD COLUMN {col} INTEGER")
                        )
                        added_an = True
            if added_an:
                logger.info(
                    "gnomad_an_columns_added",
                    columns=list(new_cols),
                    from_version=from_version,
                )
                added = True

    if from_version < 20:
        # Issue #1934: the shipped 25-marker breast-cancer model cannot be
        # reproduced from its cited paper. A runtime scoring gate prevents new
        # results, but existing sample DBs can contain findings from older
        # releases. Delete those rows, plus unidentified cancer-PRS rows whose
        # metadata is absent or malformed, during the first DB open after upgrade
        # so every API, report, SVG, and single-card reader is contained without
        # relying on each surface to duplicate a filter. Active scores with valid
        # metadata are preserved; unidentified rows can be regenerated safely.
        inspector = sa.inspect(engine)
        if "findings" in inspector.get_table_names():
            existing_cols = {c["name"] for c in inspector.get_columns("findings")}
            required_cols = {"id", "module", "category"}
            quarantined_ids: list[int] = []
            if required_cols <= existing_cols:
                with engine.begin() as conn:
                    predicate = (
                        findings.c.module == "cancer",
                        findings.c.category == "prs",
                    )
                    if "detail_json" not in existing_cols:
                        quarantined_ids = list(
                            conn.execute(sa.select(findings.c.id).where(*predicate)).scalars()
                        )
                    else:
                        candidates = conn.execute(
                            sa.select(findings.c.id, findings.c.detail_json).where(*predicate)
                        ).fetchall()
                        for row in candidates:
                            try:
                                detail = json.loads(row.detail_json) if row.detail_json else {}
                            except (json.JSONDecodeError, TypeError):
                                detail = {}
                            trait = detail.get("trait") if isinstance(detail, dict) else None
                            if _is_quarantined_or_unidentified_cancer_prs_trait(trait):
                                quarantined_ids.append(row.id)

                    if quarantined_ids:
                        conn.execute(sa.delete(findings).where(findings.c.id.in_(quarantined_ids)))
                        added = True

                if quarantined_ids:
                    logger.warning(
                        "legacy_cancer_prs_findings_quarantined",
                        count=len(quarantined_ids),
                        from_version=from_version,
                    )

        # Finding-change banners are stored separately from the findings table.
        # Filter the same quarantined PRS identity from every diff bucket so an
        # old added/changed/removed entry cannot survive as a historical side
        # channel after the current result is deleted.
        inspector = sa.inspect(engine)
        if "annotation_state" in inspector.get_table_names():
            with engine.begin() as conn:
                state_row = conn.execute(
                    sa.select(annotation_state.c.value).where(
                        annotation_state.c.key == _FINDING_DIFF_STATE_KEY
                    )
                ).fetchone()
                if state_row is not None:
                    try:
                        diff = json.loads(state_row.value)
                    except (json.JSONDecodeError, TypeError):
                        diff = None

                    removed_diff_entries = 0
                    diff_repaired = False
                    if isinstance(diff, dict):
                        for bucket in ("changed", "added", "removed"):
                            entries = diff.get(bucket)
                            if not isinstance(entries, list):
                                diff[bucket] = []
                                diff_repaired = True
                                continue
                            kept = [
                                entry
                                for entry in entries
                                if not (
                                    isinstance(entry, dict)
                                    and entry.get("module") == "cancer"
                                    and entry.get("category") == "prs"
                                    and _is_quarantined_or_unidentified_cancer_prs_trait(
                                        entry.get("trait")
                                    )
                                )
                            ]
                            removed_diff_entries += len(entries) - len(kept)
                            diff[bucket] = kept
                            diff_repaired = diff_repaired or len(kept) != len(entries)

                        if diff_repaired:
                            diff["counts"] = {
                                bucket: (
                                    len(diff.get(bucket, []))
                                    if isinstance(diff.get(bucket), list)
                                    else 0
                                )
                                for bucket in ("changed", "added", "removed")
                            }
                            conn.execute(
                                annotation_state.update()
                                .where(annotation_state.c.key == _FINDING_DIFF_STATE_KEY)
                                .values(value=json.dumps(diff))
                            )
                            added = True

            if state_row is not None and removed_diff_entries:
                logger.warning(
                    "breast_prs_finding_diff_entries_quarantined",
                    count=removed_diff_entries,
                    from_version=from_version,
                )

    if from_version < 21:
        # Issue #1989: phenotype-only CYP2C9/phenytoin rows gave AS-1.5
        # Intermediate Metabolizers the AS-1.0 dose reduction, conflated loading
        # with maintenance dosing, and imported an HLA-B alternative-drug steer.
        # Stored findings have no safe independent reconstruction context, so
        # quarantine only this structured identity; a fresh analysis regenerates
        # the canonical score-keyed recommendation. Preserve CYP2C9/warfarin and
        # every other pharmacogenomics or phenytoin finding.
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        state_cols = (
            {c["name"] for c in inspector.get_columns("annotation_state")}
            if "annotation_state" in table_names
            else set()
        )
        can_persist_reanalysis_marker = {"key", "value"} <= state_cols
        removed_findings = 0
        if "findings" in table_names:
            existing_cols = {c["name"] for c in inspector.get_columns("findings")}
            required_cols = {"module", "category", "gene_symbol", "drug"}
            if required_cols <= existing_cols:
                legacy_finding = sa.and_(
                    findings.c.module == "pharmacogenomics",
                    findings.c.category == "prescribing_alert",
                    findings.c.gene_symbol == "CYP2C9",
                    sa.func.lower(findings.c.drug) == "phenytoin",
                )
                if not can_persist_reanalysis_marker:
                    with engine.connect() as conn:
                        has_legacy_finding = conn.execute(
                            sa.select(sa.literal(True))
                            .select_from(findings)
                            .where(legacy_finding)
                            .limit(1)
                        ).scalar_one_or_none()
                    if has_legacy_finding:
                        raise sa.exc.InvalidRequestError(
                            "Cannot quarantine legacy CYP2C9/phenytoin findings: "
                            "annotation_state must contain key and value columns"
                        )
                else:
                    with engine.begin() as conn:
                        result = conn.execute(sa.delete(findings).where(legacy_finding))
                        removed_findings = max(result.rowcount or 0, 0)
                        if removed_findings:
                            marker = json.dumps(
                                {
                                    "database": "cpic",
                                    "prompted": False,
                                    "reason": CYP2C9_PHENYTOIN_REANALYSIS_REASON,
                                    "sample_schema_version": 21,
                                }
                            )
                            existing_marker = conn.execute(
                                sa.select(annotation_state.c.key).where(
                                    annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                                )
                            ).fetchone()
                            if existing_marker is None:
                                conn.execute(
                                    annotation_state.insert(),
                                    {
                                        "key": CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
                                        "value": marker,
                                    },
                                )
                            else:
                                conn.execute(
                                    annotation_state.update()
                                    .where(
                                        annotation_state.c.key
                                        == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                                    )
                                    .values(value=marker)
                                )
                if removed_findings:
                    added = True
                    logger.warning(
                        "legacy_cyp2c9_phenytoin_findings_quarantined",
                        count=removed_findings,
                        reanalysis_marker_persisted=can_persist_reanalysis_marker,
                        from_version=from_version,
                    )

        # Finding-change banners are a separate persisted surface. Remove the
        # same exact identity from every bucket while preserving all release
        # metadata, unrelated entries, ordering, and other top-level keys.
        inspector = sa.inspect(engine)
        if "annotation_state" in inspector.get_table_names():
            removed_diff_entries = 0
            state_cols = {c["name"] for c in inspector.get_columns("annotation_state")}
            if {"key", "value"} <= state_cols:
                with engine.begin() as conn:
                    state_row = conn.execute(
                        sa.select(annotation_state.c.value).where(
                            annotation_state.c.key == _FINDING_DIFF_STATE_KEY
                        )
                    ).fetchone()
                    if state_row is not None:
                        try:
                            diff = json.loads(state_row.value)
                        except (json.JSONDecodeError, TypeError):
                            diff = None

                        if isinstance(diff, dict):
                            for bucket in ("changed", "added", "removed"):
                                entries = diff.get(bucket)
                                if not isinstance(entries, list):
                                    continue
                                kept = [
                                    entry
                                    for entry in entries
                                    if not _is_legacy_cyp2c9_phenytoin_diff_entry(entry)
                                ]
                                removed_diff_entries += len(entries) - len(kept)
                                if len(kept) != len(entries):
                                    diff[bucket] = kept

                            if removed_diff_entries:
                                diff["counts"] = {
                                    bucket: (
                                        len(diff.get(bucket, []))
                                        if isinstance(diff.get(bucket), list)
                                        else 0
                                    )
                                    for bucket in ("changed", "added", "removed")
                                }
                                conn.execute(
                                    annotation_state.update()
                                    .where(annotation_state.c.key == _FINDING_DIFF_STATE_KEY)
                                    .values(value=json.dumps(diff))
                                )
                                added = True

            if removed_diff_entries:
                logger.warning(
                    "cyp2c9_phenytoin_finding_diff_entries_quarantined",
                    count=removed_diff_entries,
                    from_version=from_version,
                )

    if from_version < 22:
        # Issue #2000: the bundled TPMT Poor Metabolizer rows abbreviated or
        # conflated current malignancy and nonmalignancy thiopurine guidance.
        # Existing alerts retain enough structured context for a deterministic
        # in-place repair. Match the full identity, parsed legacy recommendation,
        # and generated finding-text shape before changing anything. This keeps
        # custom, malformed, already-current, and near-match findings untouched.
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        findings_cols = (
            {c["name"] for c in inspector.get_columns("findings")}
            if "findings" in table_names
            else set()
        )
        state_cols = (
            {c["name"] for c in inspector.get_columns("annotation_state")}
            if "annotation_state" in table_names
            else set()
        )
        required_finding_cols = {
            "id",
            "module",
            "category",
            "gene_symbol",
            "metabolizer_status",
            "drug",
            "finding_text",
            "detail_json",
        }
        can_repair_findings = required_finding_cols <= findings_cols
        can_repair_diff = {"key", "value"} <= state_cols
        repaired_findings = 0
        removed_diff_entries = 0

        if can_repair_findings or can_repair_diff:
            # Python's sqlite3 legacy transaction mode does not start a DB
            # transaction for SELECT. Reserve the writer lock before either
            # snapshot so no concurrent annotation can change a finding or diff
            # between its exact-match check and the corresponding update.
            with engine.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    if can_repair_findings:
                        candidates = conn.execute(
                            sa.select(
                                findings.c.id,
                                findings.c.drug,
                                findings.c.finding_text,
                                findings.c.detail_json,
                            )
                            .where(
                                findings.c.module == "pharmacogenomics",
                                findings.c.category == "prescribing_alert",
                                findings.c.gene_symbol == "TPMT",
                                findings.c.metabolizer_status == "Poor Metabolizer",
                                findings.c.drug.in_(
                                    tuple(_TPMT_POOR_METABOLIZER_RECOMMENDATION_UPDATES)
                                ),
                            )
                            .order_by(findings.c.id)
                        ).fetchall()
                        for row in candidates:
                            try:
                                detail = json.loads(row.detail_json)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not isinstance(detail, dict):
                                continue

                            legacy_recommendations, bundled_recommendation = (
                                _TPMT_POOR_METABOLIZER_RECOMMENDATION_UPDATES[row.drug]
                            )
                            legacy_recommendation = detail.get("recommendation")
                            if (
                                not isinstance(legacy_recommendation, str)
                                or legacy_recommendation not in legacy_recommendations
                                or detail.get("classification") != "A"
                                or detail.get("guideline_url") != _TPMT_THIOPURINE_GUIDELINE_URL
                            ):
                                continue
                            updated_text = _updated_tpmt_poor_metabolizer_finding_text(
                                row.finding_text,
                                row.drug,
                                legacy_recommendation,
                                bundled_recommendation,
                            )
                            if updated_text is None:
                                continue

                            detail["recommendation"] = bundled_recommendation
                            update_values: dict[str, object] = {
                                "finding_text": updated_text,
                                "detail_json": json.dumps(detail),
                            }
                            if "provenance" in findings_cols:
                                update_values["provenance"] = None
                            result = conn.execute(
                                findings.update()
                                .where(
                                    findings.c.id == row.id,
                                    findings.c.module == "pharmacogenomics",
                                    findings.c.category == "prescribing_alert",
                                    findings.c.gene_symbol == "TPMT",
                                    findings.c.metabolizer_status == "Poor Metabolizer",
                                    findings.c.drug == row.drug,
                                    findings.c.finding_text == row.finding_text,
                                    findings.c.detail_json == row.detail_json,
                                )
                                .values(**update_values)
                            )
                            repaired_findings += max(result.rowcount or 0, 0)

                    if can_repair_diff:
                        state_row = conn.execute(
                            sa.select(annotation_state.c.value).where(
                                annotation_state.c.key == _FINDING_DIFF_STATE_KEY
                            )
                        ).fetchone()
                        if state_row is not None:
                            try:
                                diff = json.loads(state_row.value)
                            except (json.JSONDecodeError, TypeError):
                                diff = None

                            if isinstance(diff, dict):
                                for bucket in ("changed", "added", "removed"):
                                    entries = diff.get(bucket)
                                    if not isinstance(entries, list):
                                        continue
                                    kept = [
                                        entry
                                        for entry in entries
                                        if not _is_legacy_tpmt_poor_metabolizer_diff_entry(entry)
                                    ]
                                    removed_diff_entries += len(entries) - len(kept)
                                    if len(kept) != len(entries):
                                        diff[bucket] = kept

                                if removed_diff_entries:
                                    diff["counts"] = {
                                        bucket: (
                                            len(diff.get(bucket, []))
                                            if isinstance(diff.get(bucket), list)
                                            else 0
                                        )
                                        for bucket in ("changed", "added", "removed")
                                    }
                                    conn.execute(
                                        annotation_state.update()
                                        .where(
                                            annotation_state.c.key == _FINDING_DIFF_STATE_KEY,
                                            annotation_state.c.value == state_row.value,
                                        )
                                        .values(value=json.dumps(diff))
                                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

        if repaired_findings or removed_diff_entries:
            added = True
            logger.warning(
                "legacy_tpmt_poor_metabolizer_guidance_repaired",
                findings_count=repaired_findings,
                finding_diff_count=removed_diff_entries,
                from_version=from_version,
            )

    if from_version < 23:
        # Issue #2012: the bundled CYP2B6 efavirenz Intermediate row delayed
        # dose reduction until CNS side effects, while the Poor row omitted
        # CPIC's 200 mg option. Repair only persisted alerts whose structured
        # identity, detail payload, and generated text all match those exact
        # released strings. Custom, malformed, current, and near-match findings
        # remain untouched.
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        findings_cols = (
            {c["name"] for c in inspector.get_columns("findings")}
            if "findings" in table_names
            else set()
        )
        state_cols = (
            {c["name"] for c in inspector.get_columns("annotation_state")}
            if "annotation_state" in table_names
            else set()
        )
        required_finding_cols = {
            "id",
            "module",
            "category",
            "gene_symbol",
            "metabolizer_status",
            "drug",
            "finding_text",
            "detail_json",
        }
        can_repair_findings = required_finding_cols <= findings_cols
        can_repair_diff = {"key", "value"} <= state_cols
        repaired_findings = 0
        removed_diff_entries = 0

        if can_repair_findings or can_repair_diff:
            with engine.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    if can_repair_findings:
                        candidates = conn.execute(
                            sa.select(
                                findings.c.id,
                                findings.c.metabolizer_status,
                                findings.c.finding_text,
                                findings.c.detail_json,
                            )
                            .where(
                                findings.c.module == "pharmacogenomics",
                                findings.c.category == "prescribing_alert",
                                findings.c.gene_symbol == "CYP2B6",
                                findings.c.metabolizer_status.in_(
                                    tuple(_CYP2B6_EFAVIRENZ_RECOMMENDATION_UPDATES)
                                ),
                                findings.c.drug == "efavirenz",
                            )
                            .order_by(findings.c.id)
                        ).fetchall()
                        for row in candidates:
                            try:
                                detail = json.loads(row.detail_json)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not isinstance(detail, dict):
                                continue

                            legacy_recommendation, bundled_recommendation = (
                                _CYP2B6_EFAVIRENZ_RECOMMENDATION_UPDATES[row.metabolizer_status]
                            )
                            if (
                                detail.get("recommendation") != legacy_recommendation
                                or detail.get("classification") != "A"
                                or detail.get("guideline_url") != _CYP2B6_EFAVIRENZ_GUIDELINE_URL
                            ):
                                continue
                            updated_text = _updated_cyp2b6_efavirenz_finding_text(
                                row.finding_text,
                                row.metabolizer_status,
                                legacy_recommendation,
                                bundled_recommendation,
                            )
                            if updated_text is None:
                                continue

                            detail["recommendation"] = bundled_recommendation
                            update_values: dict[str, object] = {
                                "finding_text": updated_text,
                                "detail_json": json.dumps(detail),
                            }
                            if "provenance" in findings_cols:
                                update_values["provenance"] = None
                            result = conn.execute(
                                findings.update()
                                .where(
                                    findings.c.id == row.id,
                                    findings.c.module == "pharmacogenomics",
                                    findings.c.category == "prescribing_alert",
                                    findings.c.gene_symbol == "CYP2B6",
                                    findings.c.metabolizer_status == row.metabolizer_status,
                                    findings.c.drug == "efavirenz",
                                    findings.c.finding_text == row.finding_text,
                                    findings.c.detail_json == row.detail_json,
                                )
                                .values(**update_values)
                            )
                            repaired_findings += max(result.rowcount or 0, 0)

                    if can_repair_diff:
                        state_row = conn.execute(
                            sa.select(annotation_state.c.value).where(
                                annotation_state.c.key == _FINDING_DIFF_STATE_KEY
                            )
                        ).fetchone()
                        if state_row is not None:
                            try:
                                diff = json.loads(state_row.value)
                            except (json.JSONDecodeError, TypeError):
                                diff = None

                            if isinstance(diff, dict):
                                for bucket in ("changed", "added", "removed"):
                                    entries = diff.get(bucket)
                                    if not isinstance(entries, list):
                                        continue
                                    kept = [
                                        entry
                                        for entry in entries
                                        if not _is_legacy_cyp2b6_efavirenz_diff_entry(entry)
                                    ]
                                    removed_diff_entries += len(entries) - len(kept)
                                    if len(kept) != len(entries):
                                        diff[bucket] = kept

                                if removed_diff_entries:
                                    diff["counts"] = {
                                        bucket: (
                                            len(diff.get(bucket, []))
                                            if isinstance(diff.get(bucket), list)
                                            else 0
                                        )
                                        for bucket in ("changed", "added", "removed")
                                    }
                                    conn.execute(
                                        annotation_state.update()
                                        .where(
                                            annotation_state.c.key == _FINDING_DIFF_STATE_KEY,
                                            annotation_state.c.value == state_row.value,
                                        )
                                        .values(value=json.dumps(diff))
                                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

        if repaired_findings or removed_diff_entries:
            added = True
            logger.warning(
                "legacy_cyp2b6_efavirenz_guidance_repaired",
                findings_count=repaired_findings,
                finding_diff_count=removed_diff_entries,
                from_version=from_version,
            )

    if from_version < 24:
        # Issue #2091: generated LRRK2 findings used an unbounded "never develop"
        # statement even though the cited estimates end at age 80. Existing
        # sample DBs persist that generated text, so updating the panel alone
        # does not repair what those users see. Match the complete production
        # identity, structured genotype evidence, and one of the two historical
        # templates before rewriting. Custom, malformed, and near-match rows are
        # left untouched. The same exact public wording is repaired in the
        # persisted finding-change banner without changing its counts.
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        findings_cols = (
            {c["name"] for c in inspector.get_columns("findings")}
            if "findings" in table_names
            else set()
        )
        state_cols = (
            {c["name"] for c in inspector.get_columns("annotation_state")}
            if "annotation_state" in table_names
            else set()
        )
        required_finding_cols = {
            "id",
            "module",
            "category",
            "gene_symbol",
            "rsid",
            "conditions",
            "finding_text",
            "detail_json",
        }
        can_repair_findings = required_finding_cols <= findings_cols
        can_repair_diff = {"key", "value"} <= state_cols
        repaired_findings = 0
        repaired_diff_entries = 0

        if can_repair_findings or can_repair_diff:
            # Reserve the SQLite writer lock before reading either persisted
            # surface so every exact-match check and update is one transaction.
            with engine.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    if can_repair_findings:
                        candidates = conn.execute(
                            sa.select(
                                findings.c.id,
                                findings.c.finding_text,
                                findings.c.detail_json,
                                *(
                                    [findings.c.pmid_citations]
                                    if "pmid_citations" in findings_cols
                                    else []
                                ),
                            )
                            .where(
                                findings.c.module == "parkinsons",
                                findings.c.category == "risk_genotype",
                                findings.c.gene_symbol == "LRRK2",
                                findings.c.rsid == "rs34637584",
                                findings.c.conditions == _PARKINSONS_RISK_CLASSIFICATION,
                            )
                            .order_by(findings.c.id)
                        ).fetchall()
                        for row in candidates:
                            try:
                                detail = json.loads(row.detail_json)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not isinstance(detail, dict):
                                continue
                            genotype_calls = detail.get("genotype_calls")
                            if (
                                detail.get("model_id") != "lrrk2_g2019s"
                                or detail.get("classification") != _PARKINSONS_RISK_CLASSIFICATION
                                or not isinstance(genotype_calls, dict)
                                or set(genotype_calls) != {"rs34637584"}
                            ):
                                continue
                            updated_text = _updated_parkinsons_finding_text(
                                row.finding_text,
                                genotype_calls["rs34637584"],
                            )
                            if updated_text is None:
                                continue

                            update_values: dict[str, object] = {"finding_text": updated_text}
                            if "pmid_citations" in findings_cols:
                                update_values["pmid_citations"] = json.dumps(
                                    _PARKINSONS_CURRENT_PMIDS
                                )
                            if "provenance" in findings_cols:
                                update_values["provenance"] = None
                            identity_filters = [
                                findings.c.id == row.id,
                                findings.c.module == "parkinsons",
                                findings.c.category == "risk_genotype",
                                findings.c.gene_symbol == "LRRK2",
                                findings.c.rsid == "rs34637584",
                                findings.c.conditions == _PARKINSONS_RISK_CLASSIFICATION,
                                findings.c.finding_text == row.finding_text,
                                findings.c.detail_json == row.detail_json,
                            ]
                            if "pmid_citations" in findings_cols:
                                identity_filters.append(
                                    findings.c.pmid_citations == row.pmid_citations
                                )
                            result = conn.execute(
                                findings.update().where(*identity_filters).values(**update_values)
                            )
                            repaired_findings += max(result.rowcount or 0, 0)

                    if can_repair_diff:
                        state_row = conn.execute(
                            sa.select(annotation_state.c.value).where(
                                annotation_state.c.key == _FINDING_DIFF_STATE_KEY
                            )
                        ).fetchone()
                        if state_row is not None:
                            try:
                                diff = json.loads(state_row.value)
                            except (json.JSONDecodeError, TypeError):
                                diff = None

                            if isinstance(diff, dict):
                                for bucket in ("changed", "added", "removed"):
                                    entries = diff.get(bucket)
                                    if not isinstance(entries, list):
                                        continue
                                    for index, entry in enumerate(entries):
                                        updated_entry = _updated_parkinsons_diff_entry(entry)
                                        if updated_entry is not None:
                                            entries[index] = updated_entry
                                            repaired_diff_entries += 1

                                if repaired_diff_entries:
                                    conn.execute(
                                        annotation_state.update()
                                        .where(
                                            annotation_state.c.key == _FINDING_DIFF_STATE_KEY,
                                            annotation_state.c.value == state_row.value,
                                        )
                                        .values(value=json.dumps(diff))
                                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

        if repaired_findings or repaired_diff_entries:
            added = True
            logger.warning(
                "legacy_parkinsons_penetrance_wording_repaired",
                findings_count=repaired_findings,
                finding_diff_count=repaired_diff_entries,
                from_version=from_version,
            )

    return added


def _get_schema_version(engine: sa.Engine) -> int:
    """Read the schema_version from the sample DB's user_version PRAGMA."""
    with engine.connect() as conn:
        row = conn.execute(sa.text("PRAGMA user_version")).fetchone()
        return row[0] if row else 0


def _stamp_schema_version(engine: sa.Engine, version: int) -> None:
    """Write the schema version into SQLite's user_version PRAGMA."""
    if not isinstance(version, int):
        raise TypeError(f"version must be int, got {type(version).__name__}")
    with engine.connect() as conn:
        conn.execute(sa.text(f"PRAGMA user_version = {version}"))
        conn.commit()
