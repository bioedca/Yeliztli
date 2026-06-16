"""FHIR R4 DiagnosticReport export (P4-12a).

Generates a minimal FHIR R4 Bundle containing:
- One DiagnosticReport resource (genomic report metadata)
- One Observation resource per annotated variant

Scope is intentionally limited to genomic core — no Condition,
no MedicationStatement, no full FHIR server (R-17 mitigation).
Output is JSON conforming to FHIR R4 (4.0.1).

Usage::

    from backend.reports.fhir_export import build_fhir_bundle

    bundle = build_fhir_bundle(sample_id=1)
    # Returns a dict ready for json.dumps()
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import CARRIED_ZYGOSITIES
from backend.db.connection import get_registry
from backend.db.tables import annotated_variants, samples

logger = structlog.get_logger(__name__)

# ── LOINC / system constants ─────────────────────────────────────────

LOINC_SYSTEM = "http://loinc.org"
HGNC_SYSTEM = "http://www.genenames.org"
DBSNP_SYSTEM = "http://www.ncbi.nlm.nih.gov/snp"
SEQUENCE_ONTOLOGY = "http://www.sequenceontology.org"

# LOINC codes for genomics reporting
LOINC_MASTER_PANEL = "81247-9"  # Master HL7 genetic variant reporting panel
LOINC_VARIANT_ASSESSMENT = "69548-6"  # Genetic variant assessment
LOINC_GENE_STUDIED = "48018-6"  # Gene studied [ID]
LOINC_ALLELIC_STATE = "53034-5"  # Allelic state
LOINC_GENOMIC_COORD_SYSTEM = "92822-6"  # Genomic coordinate system
LOINC_VARIANT_EXACT_START = "81254-5"  # Genomic structural variant start
LOINC_REF_ALLELE = "69547-8"  # Genomic ref allele [ID]
LOINC_ALT_ALLELE = "69551-0"  # Genomic alt allele [ID]
LOINC_DBSNP_ID = "81255-2"  # dbSNP [ID]
LOINC_CLINVAR_SIGNIFICANCE = "53037-8"  # Genetic variation clinical significance
LOINC_POPULATION_AF = "92821-8"  # Allelic frequency in Population

# Allelic state LOINC answer codes.  Only carried zygosities are mapped:
# build_fhir_bundle carriage-gates to het / hom_alt (#890), so hom_ref never
# reaches the exporter.  Deliberately omitting hom_ref here is defense in depth —
# if a hom_ref row ever did slip through, _variant_to_observation drops the
# allelic-state component (see the ``in ALLELIC_STATE_MAP`` guard) rather than
# mislabel a reference position as "Homozygous" (LA6705-3, shared with hom_alt).
ALLELIC_STATE_MAP: dict[str, dict[str, str]] = {
    "het": {
        "system": LOINC_SYSTEM,
        "code": "LA6706-1",
        "display": "Heterozygous",
    },
    "hom_alt": {
        "system": LOINC_SYSTEM,
        "code": "LA6705-3",
        "display": "Homozygous",
    },
}

ACMG_CLINICAL_SIGNIFICANCE_MAP: dict[str, dict[str, str]] = {
    "pathogenic": {
        "code": "LA6668-3",
        "display": "Pathogenic",
    },
    "likely pathogenic": {
        "code": "LA26332-9",
        "display": "Likely pathogenic",
    },
    "uncertain significance": {
        "code": "LA26333-7",
        "display": "Uncertain significance",
    },
    "likely benign": {
        "code": "LA26334-5",
        "display": "Likely benign",
    },
    "benign": {
        "code": "LA6675-8",
        "display": "Benign",
    },
}


# ── Bundle builder ───────────────────────────────────────────────────


def _make_uuid() -> str:
    """Generate a urn:uuid for FHIR resource fullUrl."""
    return f"urn:uuid:{uuid.uuid4()}"


def _coding(system: str, code: str, display: str | None = None) -> dict[str, str]:
    """Build a FHIR Coding element."""
    c: dict[str, str] = {"system": system, "code": code}
    if display:
        c["display"] = display
    return c


def _codeable_concept(
    system: str, code: str, display: str | None = None, text: str | None = None
) -> dict[str, Any]:
    """Build a FHIR CodeableConcept element."""
    cc: dict[str, Any] = {"coding": [_coding(system, code, display)]}
    if text:
        cc["text"] = text
    return cc


def _text_codeable_concept(text: str) -> dict[str, Any]:
    """Build a CodeableConcept when no terminology-valid code is available."""
    return {"text": text}


def _component(
    code_system: str,
    code_value: str,
    code_display: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Build a FHIR Observation.component element."""
    return {
        "code": _codeable_concept(code_system, code_value, code_display),
        **value,
    }


def _normalise_clinvar_significance(significance: str) -> str:
    return " ".join(significance.replace("_", " ").strip().lower().split())


def _clinical_significance_value(significance: str) -> dict[str, Any]:
    mapped = ACMG_CLINICAL_SIGNIFICANCE_MAP.get(_normalise_clinvar_significance(significance))
    if mapped is None:
        return {"valueCodeableConcept": _text_codeable_concept(significance)}

    return {
        "valueCodeableConcept": _codeable_concept(
            LOINC_SYSTEM,
            mapped["code"],
            mapped["display"],
            text=significance,
        )
    }


def _gene_studied_value(row: dict[str, Any]) -> dict[str, Any]:
    gene_symbol = str(row["gene_symbol"]).strip()
    hgnc_id = str(row.get("hgnc_id") or "").strip()
    if hgnc_id.startswith("HGNC:"):
        return {"valueCodeableConcept": _codeable_concept(HGNC_SYSTEM, hgnc_id, gene_symbol)}
    return {"valueString": gene_symbol}


def _variant_to_observation(
    row: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Convert an annotated_variants row to a FHIR Observation resource.

    Returns (fullUrl, resource_dict).
    """
    full_url = _make_uuid()
    components: list[dict[str, Any]] = []

    # Gene studied
    if row.get("gene_symbol"):
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_GENE_STUDIED,
                "Gene studied [ID]",
                _gene_studied_value(row),
            )
        )

    # Genomic coordinate system (0-based vs 1-based) — we use 1-based
    components.append(
        _component(
            LOINC_SYSTEM,
            LOINC_GENOMIC_COORD_SYSTEM,
            "Genomic coordinate system [Type]",
            {
                "valueCodeableConcept": _codeable_concept(
                    LOINC_SYSTEM, "LA30102-0", "1-based character counting"
                )
            },
        )
    )

    # Exact start position
    if row.get("pos") is not None:
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_VARIANT_EXACT_START,
                "Variant exact start-end",
                {"valueInteger": row["pos"]},
            )
        )

    # Reference allele
    if row.get("ref"):
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_REF_ALLELE,
                "Genomic ref allele [ID]",
                {"valueString": row["ref"]},
            )
        )

    # Alternate allele
    if row.get("alt"):
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_ALT_ALLELE,
                "Genomic alt allele [ID]",
                {"valueString": row["alt"]},
            )
        )

    # dbSNP ID
    if row.get("rsid"):
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_DBSNP_ID,
                "dbSNP [ID]",
                {
                    "valueCodeableConcept": _codeable_concept(
                        DBSNP_SYSTEM, row["rsid"], row["rsid"]
                    )
                },
            )
        )

    # Allelic state
    zygosity = row.get("zygosity")
    if zygosity and zygosity in ALLELIC_STATE_MAP:
        allelic = ALLELIC_STATE_MAP[zygosity]
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_ALLELIC_STATE,
                "Allelic state",
                {
                    "valueCodeableConcept": {
                        "coding": [allelic],
                    }
                },
            )
        )

    # ClinVar clinical significance
    if row.get("clinvar_significance"):
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_CLINVAR_SIGNIFICANCE,
                "Genetic variation clinical significance [Imp]",
                _clinical_significance_value(row["clinvar_significance"]),
            )
        )

    # gnomAD is external population context; do not encode it as sample VAF
    # (LOINC 81258-6).
    if row.get("gnomad_af_global") is not None:
        components.append(
            _component(
                LOINC_SYSTEM,
                LOINC_POPULATION_AF,
                "Allelic frequency in Population",
                {
                    "valueQuantity": {
                        "value": row["gnomad_af_global"],
                        "system": "http://unitsofmeasure.org",
                        "code": "1",
                    }
                },
            )
        )

    # Consequence (Sequence Ontology term)
    if row.get("consequence"):
        components.append(
            _component(
                LOINC_SYSTEM,
                "48004-6",
                "DNA change type",
                {
                    "valueCodeableConcept": _codeable_concept(
                        SEQUENCE_ONTOLOGY,
                        row["consequence"],
                        row["consequence"],
                    )
                },
            )
        )

    observation: dict[str, Any] = {
        "resourceType": "Observation",
        "id": str(uuid.uuid4()),
        "status": "final",
        "category": [
            _codeable_concept(
                "http://terminology.hl7.org/CodeSystem/observation-category",
                "laboratory",
                "Laboratory",
            )
        ],
        "code": _codeable_concept(
            LOINC_SYSTEM,
            LOINC_VARIANT_ASSESSMENT,
            "Genetic variant assessment",
        ),
        "component": components,
    }

    # Add HGVS interpretation if available
    if row.get("hgvs_coding") or row.get("hgvs_protein"):
        texts = []
        if row.get("hgvs_coding"):
            texts.append(row["hgvs_coding"])
        if row.get("hgvs_protein"):
            texts.append(row["hgvs_protein"])
        observation["valueCodeableConcept"] = {
            "text": "; ".join(texts),
        }

    return full_url, observation


def _load_annotated_variants(engine: sa.Engine) -> list[dict[str, Any]]:
    """Load all annotated variants from a sample database."""
    # Canonical chromosome sort order
    chrom_order: dict[str, int] = {
        **{str(i): i for i in range(1, 23)},
        "X": 23,
        "Y": 24,
        "MT": 25,
    }
    chrom_expr = sa.case(
        *[(annotated_variants.c.chrom == k, v) for k, v in chrom_order.items()],
        else_=99,
    )
    query = sa.select(annotated_variants).order_by(
        chrom_expr.asc(), annotated_variants.c.pos.asc()
    )

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    return [
        {col.name: getattr(r, col.name, None) for col in annotated_variants.columns} for r in rows
    ]


def build_fhir_bundle(
    sample_id: int,
    *,
    include_all: bool = True,
) -> dict[str, Any]:
    """Build a FHIR R4 Bundle (type=collection) for a sample.

    Parameters
    ----------
    sample_id:
        Numeric ID of the sample in the reference DB.
    include_all:
        If True, include all annotated variants. If False, only include
        variants with ClinVar annotations (non-null clinvar_significance).

    Returns
    -------
    dict
        FHIR R4 Bundle resource as a dict (ready for ``json.dumps``).

    Raises
    ------
    ValueError
        If sample not found or has no annotated variants.
    """
    registry = get_registry()

    # Look up sample
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path, samples.c.name).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise ValueError(f"Sample {sample_id} not found")

    sample_name = row.name or f"Sample {sample_id}"
    sample_db_path = registry.settings.data_dir / row.db_path
    if not sample_db_path.exists():
        raise ValueError(f"Sample database file not found: {sample_db_path}")

    sample_engine = registry.get_sample_engine(sample_db_path)

    # Load variants
    all_variants = _load_annotated_variants(sample_engine)
    if not all_variants:
        raise ValueError(f"Sample {sample_id} has no annotated variants. Run annotation first.")

    # Carriage-gate the exported Observations (#890). A FHIR genetic-variant
    # Observation asserts the variant is *present* in the sample, so only export
    # positions actually carried — het / hom_alt — matching the carriage gate the
    # cancer/carrier/cardiovascular pipelines apply (CARRIED_ZYGOSITIES). hom_ref
    # positions carry no variant; exporting them (with the variant's ClinVar
    # significance and a "Homozygous" allelic state indistinguishable from hom_alt)
    # is a false clinical assertion — thousands of false homozygous-pathogenic rows
    # per healthy sample.
    all_variants = [v for v in all_variants if v.get("zygosity") in CARRIED_ZYGOSITIES]

    # Filter if requested
    if not include_all:
        all_variants = [v for v in all_variants if v.get("clinvar_significance")]

    # Build Observation entries
    observation_entries: list[dict[str, Any]] = []
    observation_refs: list[dict[str, str]] = []

    for variant in all_variants:
        full_url, obs_resource = _variant_to_observation(variant)
        observation_entries.append(
            {
                "fullUrl": full_url,
                "resource": obs_resource,
            }
        )
        observation_refs.append({"reference": full_url})

    # Build DiagnosticReport
    report_url = _make_uuid()
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    diagnostic_report: dict[str, Any] = {
        "resourceType": "DiagnosticReport",
        "id": str(uuid.uuid4()),
        "status": "final",
        "category": [
            _codeable_concept(
                "http://terminology.hl7.org/CodeSystem/v2-0074",
                "GE",
                "Genetics",
            )
        ],
        "code": _codeable_concept(
            LOINC_SYSTEM,
            LOINC_MASTER_PANEL,
            "Master HL7 genetic variant reporting panel",
        ),
        "subject": {"display": sample_name},
        "issued": now,
        "result": observation_refs,
    }

    # Build Bundle
    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "collection",
        "timestamp": now,
        "meta": {
            "lastUpdated": now,
            "profile": [
                "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomics-report"
            ],
        },
        "entry": [
            {
                "fullUrl": report_url,
                "resource": diagnostic_report,
            },
            *observation_entries,
        ],
    }

    logger.info(
        "fhir_bundle_generated",
        sample_id=sample_id,
        variant_count=len(observation_entries),
        include_all=include_all,
    )

    return bundle
