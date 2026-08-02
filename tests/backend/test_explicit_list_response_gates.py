"""Regression coverage for explicit aggregate response gates (#2019)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from backend.analysis.pharmacogenomics import (
    is_patient_presentable_finding_payload,
    is_patient_presentable_response_payload,
)
from backend.api.routes import (
    apoe,
    cancer,
    cardiovascular,
    carrier,
    hemochromatosis,
    kinship,
    metabolic,
    parkinsons,
    rare_variants,
    risk_common,
)


class _Rows:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def fetchall(self) -> list[SimpleNamespace]:
        return self._rows

    def __iter__(self) -> Iterator[SimpleNamespace]:
        return iter(self._rows)


class _Connection:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _statement: object) -> _Rows:
        return _Rows(self._rows)


class _Engine:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def connect(self) -> _Connection:
        return _Connection(self._rows)


def _finding_row(**overrides: Any) -> SimpleNamespace:
    """Make a complete, individually presentable stored-finding test row."""
    values: dict[str, Any] = {
        "id": 1,
        "module": "test",
        "category": "risk_genotype",
        "rsid": "rs_safe",
        "gene_symbol": "SAFE1",
        "drug": None,
        "genotype": None,
        "zygosity": "het",
        "clinvar_significance": "Pathogenic",
        "conditions": "Safe condition",
        "evidence_level": 1,
        "finding_text": "Safe finding",
        "detail_json": json.dumps({}),
        "provenance": None,
        "pmid_citations": None,
        "phenotype": None,
        "diplotype": None,
        "prs_percentile": None,
    }
    values.update(overrides)
    mapping = {
        key: values[key]
        for key in (
            "module",
            "category",
            "gene_symbol",
            "drug",
            "finding_text",
            "detail_json",
            "provenance",
            "pmid_citations",
        )
    }
    values["_mapping"] = mapping
    return SimpleNamespace(**values)


def _assert_individually_presentable(rows: list[SimpleNamespace]) -> None:
    assert all(is_patient_presentable_finding_payload(row._mapping) for row in rows)


class _CapturedStreamingResponse:
    """Capture a route's iterator without starting Starlette's thread pool."""

    def __init__(self, content: Iterator[str], **_kwargs: object) -> None:
        self.content = "".join(content)


def test_variant_list_aggregates_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate safe variant DTOs cannot recreate a held prescribing pair."""
    cancer_rows = [
        {"rsid": "rs1", "gene_symbol": "CYP2D6", "clinvar_significance": "Pathogenic"},
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "clinvar_significance": "Pathogenic",
            "clinical_caveat": "tamoxifen",
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in cancer_rows)
    monkeypatch.setattr(cancer, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(cancer, "_fetch_cancer_findings", lambda _engine: cancer_rows)

    cancer_response = cancer.list_cancer_variants(sample_id=1)

    assert cancer_response == cancer.CancerVariantsListResponse(items=[], total=0)

    cardiovascular_rows = [
        {"rsid": "rs1", "gene_symbol": "CYP2D6", "clinvar_significance": "Pathogenic"},
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "clinvar_significance": "Pathogenic",
            "conditions": ["tamoxifen"],
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in cardiovascular_rows)
    monkeypatch.setattr(cardiovascular, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(
        cardiovascular,
        "_fetch_cardiovascular_findings",
        lambda _engine: cardiovascular_rows,
    )

    cardiovascular_response = cardiovascular.list_cardiovascular_variants(sample_id=1)

    assert cardiovascular_response == cardiovascular.CardiovascularVariantsListResponse(
        items=[], total=0
    )

    carrier_rows = [
        {"rsid": "rs1", "gene_symbol": "CYP2D6", "clinvar_significance": "Pathogenic"},
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "clinvar_significance": "Pathogenic",
            "notes": "tamoxifen",
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in carrier_rows)
    monkeypatch.setattr(carrier, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(carrier, "_fetch_carrier_findings", lambda _engine: carrier_rows)

    carrier_response = carrier.list_carrier_variants(sample_id=1)

    assert carrier_response == carrier.CarrierVariantsListResponse(
        items=[], total=0, genes_with_findings=[]
    )


def test_prs_and_anchor_aggregates_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final PRS and anchor DTOs are checked after their fields are decoded."""
    prs_rows = [
        _finding_row(id=1, category="prs", detail_json=json.dumps({"trait": "CYP2D6"})),
        _finding_row(id=2, category="prs", detail_json=json.dumps({"trait": "tamoxifen"})),
    ]
    _assert_individually_presentable(prs_rows)
    prs_engine = _Engine(prs_rows)
    monkeypatch.setattr(cancer, "_get_sample_engine", lambda _sample_id: prs_engine)
    monkeypatch.setattr(metabolic, "_get_sample_engine", lambda _sample_id: prs_engine)

    cancer_response = cancer.list_cancer_prs(sample_id=1)
    metabolic_response = metabolic.list_metabolic_prs(sample_id=1)

    assert cancer_response == cancer.CancerPRSListResponse(
        items=[], total=0, sufficient_count=0, insufficient_traits=[]
    )
    assert metabolic_response == metabolic.MetabolicPRSListResponse(items=[], total=0)

    anchor_rows = [
        _finding_row(
            id=3,
            category="anchor_snp",
            detail_json=json.dumps({"gene": "CYP2D6"}),
        ),
        _finding_row(
            id=4,
            category="anchor_snp",
            detail_json=json.dumps({"summary": "tamoxifen"}),
        ),
    ]
    _assert_individually_presentable(anchor_rows)
    monkeypatch.setattr(metabolic, "_get_sample_engine", lambda _sample_id: _Engine(anchor_rows))

    anchors_response = metabolic.list_metabolic_anchors(sample_id=1)

    assert anchors_response == metabolic.MetabolicAnchorListResponse(items=[], total=0)


def test_finding_list_aggregates_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named and generated findings routes use an explicit final response gate."""
    rows = [
        _finding_row(id=1, gene_symbol="CYP2D6", finding_text="Safe finding"),
        _finding_row(id=2, gene_symbol="SAFE1", finding_text="tamoxifen"),
    ]
    _assert_individually_presentable(rows)
    engine = _Engine(rows)

    monkeypatch.setattr(hemochromatosis, "_get_sample_engine", lambda _sample_id: engine)
    monkeypatch.setattr(
        hemochromatosis,
        "_fetch_findings",
        lambda _engine: [
            {
                "rsid": row.rsid,
                "gene_symbol": row.gene_symbol,
                "risk_classification": row.conditions,
                "finding_text": row.finding_text,
            }
            for row in rows
        ],
    )
    assert hemochromatosis.list_hemochromatosis_findings(sample_id=1) == (
        hemochromatosis.HemochromatosisFindingsListResponse(items=[], total=0)
    )

    text_rows = [
        _finding_row(id=3, finding_text="CYP2D6"),
        _finding_row(id=4, finding_text="tamoxifen"),
    ]
    _assert_individually_presentable(text_rows)
    text_engine = _Engine(text_rows)

    monkeypatch.setattr(apoe, "_get_sample_engine", lambda _sample_id: text_engine)
    monkeypatch.setattr(apoe, "_ensure_gate_acknowledged", lambda _engine: None)
    assert apoe.list_apoe_findings(sample_id=1) == apoe.APOEFindingsListResponse(items=[], total=0)

    monkeypatch.setattr(kinship, "resolve_sample_engine", lambda _sample_id: text_engine)
    assert kinship.list_findings(sample_id=1) == kinship.KinshipListResponse(items=[], total=0)

    risk_rows = [
        {
            "rsid": "rs1",
            "gene_symbol": "CYP2D6",
            "risk_classification": "safe",
            "finding_text": "Safe finding",
        },
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "risk_classification": "safe",
            "finding_text": "tamoxifen",
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in risk_rows)
    monkeypatch.setattr(risk_common, "resolve_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(risk_common, "fetch_risk_findings", lambda _engine, _module: risk_rows)
    router = risk_common.make_risk_router(
        module="test",
        prefix="/test",
        tags=["test"],
        disclaimer_title="Test",
        disclaimer_text="Test",
        runner=lambda _engine: (0, []),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/test/findings")

    assert endpoint(sample_id=1) == risk_common.RiskFindingsListResponse(items=[], total=0)

    monkeypatch.setattr(parkinsons, "resolve_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(parkinsons, "_ensure_gate_acknowledged", lambda _engine: None)
    monkeypatch.setattr(parkinsons, "fetch_risk_findings", lambda _engine, _module: risk_rows)
    assert parkinsons.list_findings(sample_id=1) == risk_common.RiskFindingsListResponse(
        items=[], total=0
    )


def test_rare_findings_and_exports_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON list and TSV/VCF exports collapse unsafe aggregate DTOs."""
    rows = [
        _finding_row(
            id=1,
            module="rare_variants",
            category="clinvar_pathogenic",
            gene_symbol="CYP2D6",
            detail_json=json.dumps({"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}),
        ),
        _finding_row(
            id=2,
            module="rare_variants",
            category="clinvar_pathogenic",
            gene_symbol="SAFE1",
            detail_json=json.dumps(
                {
                    "chrom": "1",
                    "pos": 200,
                    "ref": "A",
                    "alt": "T",
                    "consequence": "tamoxifen",
                }
            ),
        ),
    ]
    _assert_individually_presentable(rows)
    monkeypatch.setattr(rare_variants, "_get_sample_engine", lambda _sample_id: _Engine(rows))
    monkeypatch.setattr(rare_variants, "StreamingResponse", _CapturedStreamingResponse)

    findings_response = rare_variants.list_rare_variant_findings(
        sample_id=1,
        limit=None,
        offset=0,
    )
    tsv_response = rare_variants.export_rare_variants_tsv(sample_id=1)
    vcf_response = rare_variants.export_rare_variants_vcf(sample_id=1)

    assert findings_response == rare_variants.RareVariantFindingsListResponse(items=[], total=0)
    tsv = tsv_response.content
    vcf = vcf_response.content
    assert tsv.count("\n") == 1
    assert "CYP2D6" not in tsv
    assert "tamoxifen" not in tsv
    assert "CYP2D6" not in vcf
    assert "tamoxifen" not in vcf
    assert "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" in vcf
