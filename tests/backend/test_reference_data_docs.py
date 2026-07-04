from __future__ import annotations

import re
from pathlib import Path

from backend.db.database_registry import DATABASES

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DATA_DOC = REPO_ROOT / "docs" / "install" / "reference-data.md"

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
