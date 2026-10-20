"""Guard-withheld pathway-module lists say so instead of reading as empty (#2519).

``is_patient_presentable_response_payload`` fails closed, and each route answers
a refusal with an empty response. Since #2297 the Allergy list marked that
fallback ``withheld``; #2519 gives every categorical pathway-module list the same
shared ``WithheldMarker`` so its page can say the results are withheld rather
than that there are none.
"""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from backend.api.withheld import PATIENT_RESPONSE_WITHHELD_CODE, WithheldMarker

LIST_ROUTES = [
    ("allergy", "list_pathways"),
    ("fitness", "list_pathways"),
    ("gene_health", "list_pathways"),
    ("methylation", "list_pathways"),
    ("nutrigenomics", "list_pathways"),
    ("skin", "list_pathways"),
    ("sleep", "list_pathways"),
    ("traits", "list_pathways"),
    ("traits", "list_prs"),
]


def _call(
    monkeypatch: pytest.MonkeyPatch,
    sample_engine: sa.Engine,
    module: str,
    function: str,
    *,
    refuse: bool,
) -> WithheldMarker:
    routes = importlib.import_module(f"backend.api.routes.{module}")
    monkeypatch.setattr(routes, "_get_sample_engine", lambda _sample_id: sample_engine)
    if refuse:
        monkeypatch.setattr(
            routes, "is_patient_presentable_response_payload", lambda _payload: False
        )
    return getattr(routes, function)(sample_id=1)


@pytest.mark.parametrize(("module", "function"), LIST_ROUTES)
def test_refused_response_is_marked_withheld(
    monkeypatch: pytest.MonkeyPatch, sample_engine: sa.Engine, module: str, function: str
) -> None:
    response = _call(monkeypatch, sample_engine, module, function, refuse=True)

    assert isinstance(response, WithheldMarker)
    assert response.withheld is True
    assert response.items == []
    assert response.total == 0


@pytest.mark.parametrize(("module", "function"), LIST_ROUTES)
def test_presentable_response_is_not_marked_withheld(
    monkeypatch: pytest.MonkeyPatch, sample_engine: sa.Engine, module: str, function: str
) -> None:
    """An ordinary empty result is still just empty: the marker never fires on it."""
    response = _call(monkeypatch, sample_engine, module, function, refuse=False)

    assert response.withheld is False
    assert response.model_dump(mode="json")["withheld"] is False


# ── Pathway detail: a refused detail is a *withheld* 404, not a missing one (#2520) ──

DETAIL_MODULES = [
    "allergy",
    "fitness",
    "gene_health",
    "methylation",
    "nutrigenomics",
    "skin",
    "sleep",
    "traits",
]


def _pathway_summary() -> dict[str, object]:
    return {
        "category": "pathway_summary",
        "pathway": "Example pathway",
        "pathway_level": "Standard",
        "evidence_level": 1,
        "pmids": [],
        "rsid": None,
        "detail": {
            "pathway_id": "example",
            "called_snps": 0,
            "total_snps": 0,
            "missing_snps": [],
            "no_call_snps": [],
            "snp_details": [],
        },
    }


def _detail(monkeypatch: pytest.MonkeyPatch, module: str, *, refuse: bool) -> object:
    routes = importlib.import_module(f"backend.api.routes.{module}")
    monkeypatch.setattr(routes, "_get_sample_engine", lambda _sample_id: None)
    monkeypatch.setattr(routes, f"_fetch_{module}_findings", lambda _engine: [_pathway_summary()])
    if refuse:
        monkeypatch.setattr(
            routes, "is_patient_presentable_response_payload", lambda _payload: False
        )
    return routes.pathway_detail(pathway_id="example", sample_id=1)


@pytest.mark.parametrize("module", DETAIL_MODULES)
def test_refused_pathway_detail_is_a_withheld_404(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    with pytest.raises(HTTPException) as raised:
        _detail(monkeypatch, module, refuse=True)

    assert raised.value.status_code == 404
    assert raised.value.detail == {
        "code": PATIENT_RESPONSE_WITHHELD_CODE,
        "message": "This pathway's details are withheld for this sample.",
    }


@pytest.mark.parametrize("module", DETAIL_MODULES)
def test_presentable_pathway_detail_is_served(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    response = _detail(monkeypatch, module, refuse=False)

    assert response.pathway_id == "example"


@pytest.mark.parametrize("module", DETAIL_MODULES)
def test_a_missing_pathway_is_still_a_plain_404(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    """Only the guard's refusal carries the withheld code; absence keeps its text."""
    routes = importlib.import_module(f"backend.api.routes.{module}")
    monkeypatch.setattr(routes, "_get_sample_engine", lambda _sample_id: None)
    monkeypatch.setattr(routes, f"_fetch_{module}_findings", lambda _engine: [])

    with pytest.raises(HTTPException) as raised:
        routes.pathway_detail(pathway_id="example", sample_id=1)

    assert raised.value.status_code == 404
    assert raised.value.detail == "Pathway 'example' not found for sample."
