"""Regression coverage for the shared patient-facing finding loaders (#2019)."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
import sqlalchemy as sa

from backend.api.routes.allergy import _fetch_allergy_findings
from backend.api.routes.cancer import _fetch_cancer_findings
from backend.api.routes.cardiovascular import _fetch_cardiovascular_findings
from backend.api.routes.carrier import _fetch_carrier_findings
from backend.api.routes.fitness import _fetch_fitness_findings
from backend.api.routes.gene_health import _fetch_gene_health_findings
from backend.api.routes.hemochromatosis import _fetch_findings as fetch_hemochromatosis_findings
from backend.api.routes.methylation import _fetch_methylation_findings
from backend.api.routes.nutrigenomics import _fetch_nutrigenomics_findings
from backend.api.routes.risk_common import fetch_risk_findings
from backend.api.routes.skin import _fetch_skin_findings
from backend.api.routes.sleep import _fetch_sleep_findings
from backend.api.routes.traits import _fetch_traits_findings
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings

Loader = Callable[[sa.Engine], list[dict]]


@pytest.mark.parametrize(
    ("loader", "module", "category"),
    [
        pytest.param(
            lambda engine: fetch_risk_findings(engine, "alpha1"),
            "alpha1",
            "risk_genotype",
            id="risk-common",
        ),
        pytest.param(_fetch_allergy_findings, "allergy", "drug_hypersensitivity", id="allergy"),
        pytest.param(_fetch_fitness_findings, "fitness", "fitness_trait", id="fitness"),
        pytest.param(_fetch_gene_health_findings, "gene_health", "disease_risk", id="gene-health"),
        pytest.param(
            _fetch_methylation_findings, "methylation", "pathway_summary", id="methylation"
        ),
        pytest.param(
            _fetch_nutrigenomics_findings,
            "nutrigenomics",
            "pathway_summary",
            id="nutrigenomics",
        ),
        pytest.param(_fetch_skin_findings, "skin", "trait", id="skin"),
        pytest.param(_fetch_sleep_findings, "sleep", "trait", id="sleep"),
        pytest.param(_fetch_traits_findings, "traits", "trait", id="traits"),
        pytest.param(_fetch_cancer_findings, "cancer", "monogenic_variant", id="cancer"),
        pytest.param(
            _fetch_cardiovascular_findings,
            "cardiovascular",
            "monogenic_variant",
            id="cardiovascular",
        ),
        pytest.param(_fetch_carrier_findings, "carrier", "carrier_status", id="carrier"),
        pytest.param(
            fetch_hemochromatosis_findings,
            "hemochromatosis",
            "risk_genotype",
            id="hemochromatosis",
        ),
    ],
)
def test_shared_loaders_omit_nested_held_payload(
    tmp_path,
    loader: Loader,
    module: str,
    category: str,
) -> None:
    sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
    try:
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert(),
                [
                    {
                        "id": 1,
                        "module": module,
                        "category": category,
                        "gene_symbol": "SAFE1",
                        "finding_text": "Safe control finding",
                        "detail_json": json.dumps({}),
                    },
                    {
                        "id": 2,
                        "module": module,
                        "category": category,
                        "evidence_level": 6,
                        "gene_symbol": "CYP2C19",
                        "drug": "clopidogrel",
                        "finding_text": "Scalar-safe legacy shell",
                        "detail_json": json.dumps(
                            {
                                "legacy": {
                                    " Gene ": "CYP2D6",
                                    "DRUG": "tamoxifen",
                                    "recommendation": "Must not reach a patient response.",
                                }
                            }
                        ),
                    },
                ],
            )

        result = loader(sample_engine)

        assert len(result) == 1
        rendered = json.dumps(result).lower()
        assert "safe1" in rendered
        assert "scalar-safe legacy shell" not in rendered
        assert "tamoxifen" not in rendered
    finally:
        sample_engine.dispose()
