from __future__ import annotations

import re
from pathlib import Path

from backend.db.database_registry import DATABASES
from backend.db.update_manager import AUTO_UPDATE_DEFAULTS, CHECK_FNS

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DATA_DOC = REPO_ROOT / "docs" / "install" / "reference-data.md"
UPDATING_DOC = REPO_ROOT / "docs" / "install" / "updating.md"

DOC_ROW_LABELS = {
    "clinvar": "ClinVar",
    "vep_bundle": "VEP consequence bundle",
    "gnomad": "gnomAD allele frequencies",
    "dbnsfp": "dbNSFP",
    "alphamissense": "AlphaMissense",
    "gtex_eqtl": "GTEx eQTL",
    "spliceai": "SpliceAI",
    "pgs_scores": "PGS scores",
    "cpic": "CPIC",
    "clingen": "ClinGen",
    "ancestry_pca": "Ancestry PCA bundle",
    "lai_bundle": "Ancestry LAI bundle",
    "encode_ccres": "ENCODE cCREs",
    "gwas_catalog": "GWAS Catalog",
    "dbsnp": "dbSNP",
    "mondo_hpo": "MONDO/HPO",
}

STATIC_MANUAL_LABELS = {
    "alphamissense": "AlphaMissense",
    "gtex_eqtl": "GTEx eQTL",
    "clingen": "ClinGen",
    "spliceai": "SpliceAI",
}


def _setup_role_for(label: str, doc: str) -> str:
    row = re.search(
        rf"^\|\s*\*\*{re.escape(label)}\*\*[^|]*\|\s*(?P<role>[^|]+?)\s*\|",
        doc,
        re.MULTILINE,
    )
    assert row is not None, f"Missing reference-data table row for {label}"
    return row.group("role").strip()


def test_reference_data_doc_marks_registry_setup_roles() -> None:
    assert set(DATABASES) == set(DOC_ROW_LABELS)

    doc = REFERENCE_DATA_DOC.read_text(encoding="utf-8")

    for db_name, db_info in DATABASES.items():
        role = _setup_role_for(DOC_ROW_LABELS[db_name], doc)
        if db_info.required:
            assert role == "**Required**", db_name
        else:
            assert role.startswith("Optional"), db_name


def test_reference_data_doc_discloses_sources_outside_update_manager() -> None:
    """Docs must name registered sources that lack auto-update/check coverage."""
    assert set(AUTO_UPDATE_DEFAULTS) == set(CHECK_FNS)
    assert set(DATABASES) - set(AUTO_UPDATE_DEFAULTS) == set(STATIC_MANUAL_LABELS)

    reference_doc = REFERENCE_DATA_DOC.read_text(encoding="utf-8")
    updating_doc = UPDATING_DOC.read_text(encoding="utf-8")
    _, static_section = reference_doc.split("### Static / manual-refresh sources", maxsplit=1)

    assert "Static / manual-refresh sources" in reference_doc
    assert "outside that update-manager registry" in updating_doc
    for label in STATIC_MANUAL_LABELS.values():
        assert label in static_section
        assert label in updating_doc
