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

from backend.api.withheld import WithheldMarker

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
