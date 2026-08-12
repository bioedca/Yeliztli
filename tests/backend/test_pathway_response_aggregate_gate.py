"""Regression coverage for assembled pathway-list response presentation (#2019)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.analysis.pharmacogenomics import is_patient_presentable_response_payload
from backend.api.routes import (
    allergy,
    fitness,
    gene_health,
    methylation,
    nutrigenomics,
    skin,
    sleep,
    traits,
)


def _pathway_summary(pathway_name: str, *, module_disclaimer: str | None = None) -> dict:
    detail: dict[str, object] = {
        "pathway_id": pathway_name.lower().replace(" ", "_"),
        "called_snps": 0,
        "total_snps": 0,
    }
    if module_disclaimer is not None:
        detail["module_disclaimer"] = module_disclaimer
    return {
        "category": "pathway_summary",
        "detail": detail,
        "pathway": pathway_name,
        "pathway_level": "Standard",
        "evidence_level": 1,
        "pmids": [],
        "finding_text": "",
        "gene_symbol": "SAFE1",
        "rsid": "",
    }


@pytest.mark.parametrize(
    ("route_module", "fetch_name"),
    [
        pytest.param(allergy, "_fetch_allergy_findings", id="allergy"),
        pytest.param(fitness, "_fetch_fitness_findings", id="fitness"),
        pytest.param(methylation, "_fetch_methylation_findings", id="methylation"),
        pytest.param(nutrigenomics, "_fetch_nutrigenomics_findings", id="nutrigenomics"),
        pytest.param(skin, "_fetch_skin_findings", id="skin"),
        pytest.param(sleep, "_fetch_sleep_findings", id="sleep"),
        pytest.param(traits, "_fetch_traits_findings", id="traits"),
        pytest.param(gene_health, "_fetch_gene_health_findings", id="gene-health"),
    ],
)
def test_list_pathways_withholds_pair_assembled_from_separate_safe_rows(
    monkeypatch: pytest.MonkeyPatch,
    route_module: object,
    fetch_name: str,
) -> None:
    """A final response gate prevents cross-row CYP2D6/tamoxifen reassembly."""
    source_rows = [_pathway_summary("CYP2D6"), _pathway_summary("tamoxifen")]
    assert all(is_patient_presentable_response_payload(row) for row in source_rows)

    monkeypatch.setattr(route_module, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(route_module, fetch_name, lambda _engine: source_rows)
    if route_module is traits:
        monkeypatch.setattr(
            "backend.analysis.traits.load_traits_panel",
            lambda: SimpleNamespace(module_disclaimer="Reference panel disclaimer."),
        )

    response = route_module.list_pathways(sample_id=1)

    assert response.total == 0
    assert response.items == []
    assert "cyp2d6" not in response.model_dump_json().lower()
    assert "tamoxifen" not in response.model_dump_json().lower()


def test_traits_list_keeps_dynamic_results_when_static_disclaimer_names_held_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static panel reference text must not poison the dynamic response gate."""
    disclaimer = "Reference scope: CYP2D6 and tamoxifen are outside this panel."
    monkeypatch.setattr(traits, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(
        traits,
        "_fetch_traits_findings",
        lambda _engine: [_pathway_summary("Personality")],
    )
    monkeypatch.setattr(
        "backend.analysis.traits.load_traits_panel",
        lambda: SimpleNamespace(module_disclaimer=disclaimer),
    )

    response = traits.list_pathways(sample_id=1)

    assert response.total == 1
    assert response.module_disclaimer == disclaimer


def test_gene_health_list_withholds_pair_split_between_stored_disclaimer_and_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted disclaimer is dynamic data and cannot bypass the final gate."""
    disclaimer_row = _pathway_summary("Safe Pathway", module_disclaimer="tamoxifen")
    item_row = _pathway_summary("CYP2D6")
    assert is_patient_presentable_response_payload(disclaimer_row)
    assert is_patient_presentable_response_payload(item_row)

    monkeypatch.setattr(gene_health, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(
        gene_health,
        "_fetch_gene_health_findings",
        lambda _engine: [disclaimer_row, item_row],
    )

    response = gene_health.list_pathways(sample_id=1)

    assert response.total == 0
    assert response.items == []
    assert response.module_disclaimer is None
    assert "cyp2d6" not in response.model_dump_json().lower()
    assert "tamoxifen" not in response.model_dump_json().lower()


def test_traits_prs_withholds_pair_assembled_from_separate_safe_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PRS endpoint applies the same final gate as pathway lists."""
    source_rows = [
        {
            "category": "prs",
            "detail": {"trait": "safe trait", "name": "CYP2D6"},
            "evidence_level": 1,
            "prs_percentile": None,
        },
        {
            "category": "prs",
            "detail": {
                "trait": "other safe trait",
                "name": "safe PRS",
                "monogenic_note": "tamoxifen dose guidance",
            },
            "evidence_level": 1,
            "prs_percentile": None,
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in source_rows)

    monkeypatch.setattr(traits, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(traits, "_fetch_traits_findings", lambda _engine: source_rows)
    monkeypatch.setattr(
        "backend.analysis.traits.load_traits_panel",
        lambda: SimpleNamespace(module_disclaimer="Reference panel disclaimer."),
    )

    response = traits.list_prs(sample_id=1)

    assert response.total == 0
    assert response.items == []
    assert "cyp2d6" not in response.model_dump_json().lower()
    assert "tamoxifen" not in response.model_dump_json().lower()


@pytest.mark.parametrize(
    ("route_module", "fetch_name"),
    [
        pytest.param(allergy, "_fetch_allergy_findings", id="allergy"),
        pytest.param(fitness, "_fetch_fitness_findings", id="fitness"),
        pytest.param(gene_health, "_fetch_gene_health_findings", id="gene-health"),
        pytest.param(methylation, "_fetch_methylation_findings", id="methylation"),
        pytest.param(nutrigenomics, "_fetch_nutrigenomics_findings", id="nutrigenomics"),
        pytest.param(skin, "_fetch_skin_findings", id="skin"),
        pytest.param(sleep, "_fetch_sleep_findings", id="sleep"),
        pytest.param(traits, "_fetch_traits_findings", id="traits"),
    ],
)
def test_pathway_detail_withholds_pair_assembled_from_safe_snp_details(
    monkeypatch: pytest.MonkeyPatch,
    route_module: object,
    fetch_name: str,
) -> None:
    """Detail endpoints must not reassemble a pair after individual SNP gates."""
    source_row = _pathway_summary("Safe Pathway")
    source_row["detail"]["snp_details"] = [
        {
            "rsid": "rs-safe-gene",
            "gene": "CYP2D6",
            "variant_name": "safe one",
            "category": "Standard",
            "effect_summary": "Synthetic source detail one.",
            "evidence_level": 1,
        },
        {
            "rsid": "rs-safe-drug",
            "gene": "SAFE1",
            "variant_name": "safe two",
            "category": "Standard",
            "effect_summary": "Synthetic source detail two.",
            "evidence_level": 1,
        },
    ]
    # The recommendation comes from a distinct source row. Neither the pathway
    # summary nor the SNP finding contains both held identifiers on its own.
    source_snp_finding = {
        "category": "snp_finding",
        "detail": {"recommendation": "tamoxifen dose guidance"},
        "pathway": "Safe Pathway",
        "pathway_level": None,
        "evidence_level": 1,
        "pmids": [],
        "finding_text": "",
        "gene_symbol": "SAFE1",
        "rsid": "rs-safe-drug",
    }
    assert is_patient_presentable_response_payload(source_row)
    assert is_patient_presentable_response_payload(source_snp_finding)

    monkeypatch.setattr(route_module, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(
        route_module,
        fetch_name,
        lambda _engine: [source_row, source_snp_finding],
    )

    with pytest.raises(HTTPException) as caught:
        route_module.pathway_detail(pathway_id="safe_pathway", sample_id=1)
    assert caught.value.status_code == 404
