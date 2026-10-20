"""A SNP's recommendation reads the same on its module page and in All Findings (#2557).

``recommendation_text`` is panel prose persisted into each stored SNP finding,
so a panel correction reaches an existing sample only through a read-time
refresh. All Findings refreshes it through ``refreshed_detail_recommendation``
(#2021). Gene Health and Sleep refreshed their own pathway-detail pages, but
Allergy and Skin served the stored text, so one SNP gave two different pieces of
advice depending on where the user looked.

This walks every module with a panel loader. For each it patches the panel the
refresh reads, so the real ``_RECOMMENDATION_MODULES`` gate is exercised and not
restated here. It then asserts that the module's pathway-detail route and the
aggregator agree: on the corrected text where the module refreshes, and on the
stored text for Traits, which #2555 kept stored-only on both surfaces.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from backend.analysis import cross_module_links
from backend.analysis.cross_module_links import (
    _PANEL_LOADERS,
    _RECOMMENDATION_MODULES,
    refreshed_detail_recommendation,
)

RSID = "rs_parity"
STORED = "The recommendation stored when the sample was scored."
CORRECTED = "The panel's corrected recommendation."


@pytest.fixture(autouse=True)
def _fresh_recommendation_cache():
    cross_module_links._recommendations_for_module.cache_clear()
    yield
    cross_module_links._recommendations_for_module.cache_clear()


def _patch_panel(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    """Give ``RSID`` the corrected text in the panel the refresh loads for ``module``."""
    module_path, _, loader_name = _PANEL_LOADERS[module].partition(":")
    panel = SimpleNamespace(
        pathways=[
            SimpleNamespace(snps=[SimpleNamespace(rsid=RSID, recommendation_text=CORRECTED)])
        ]
    )
    real_import = cross_module_links.import_module

    def _import(name: str):
        if name == module_path:
            return SimpleNamespace(**{loader_name: lambda: panel})
        return real_import(name)

    monkeypatch.setattr(cross_module_links, "import_module", _import)


def _page_recommendation(
    monkeypatch: pytest.MonkeyPatch, module: str, *, stored_finding: bool = True
) -> str | None:
    """The recommendation the module's own pathway-detail route serves for ``RSID``."""
    routes = importlib.import_module(f"backend.api.routes.{module}")
    pathway = {
        "category": "pathway_summary",
        "pathway": "Parity pathway",
        "pathway_level": "Standard",
        "evidence_level": 1,
        "pmids": [],
        "rsid": None,
        "detail": {
            "pathway_id": "parity",
            "called_snps": 1,
            "total_snps": 1,
            "missing_snps": [],
            "no_call_snps": [],
            "snp_details": [{"rsid": RSID}],
        },
    }
    snp_finding = {
        "category": "snp_finding",
        "pathway": "Parity pathway",
        "rsid": RSID,
        "pmids": [],
        "detail": {"recommendation": STORED},
    }
    monkeypatch.setattr(routes, "_get_sample_engine", lambda _sample_id: None)
    rows = [pathway, snp_finding] if stored_finding else [pathway]
    monkeypatch.setattr(routes, f"_fetch_{module}_findings", lambda _engine: rows)
    # The presentability guard is not under test here, and it refuses a SNP
    # detail this minimal; let the route serve what it assembled.
    monkeypatch.setattr(routes, "is_patient_presentable_response_payload", lambda _payload: True)
    response = routes.pathway_detail(pathway_id="parity", sample_id=1)
    [snp] = [s for s in response.snp_details if s.rsid == RSID]
    return snp.recommendation


@pytest.mark.parametrize("module", sorted(_PANEL_LOADERS))
def test_module_page_and_all_findings_give_the_same_recommendation(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    _patch_panel(monkeypatch, module)

    page = _page_recommendation(monkeypatch, module)
    aggregator = refreshed_detail_recommendation(
        module, "snp_finding", RSID, {"recommendation": STORED}
    )["recommendation"]

    expected = CORRECTED if module in _RECOMMENDATION_MODULES else STORED
    assert (page, aggregator) == (expected, expected)


def test_traits_is_the_only_stored_only_module() -> None:
    """Pins the decision the parity test encodes, so changing it is deliberate."""
    assert set(_PANEL_LOADERS) - _RECOMMENDATION_MODULES == {"traits"}


@pytest.mark.parametrize("module", sorted(_PANEL_LOADERS))
def test_a_snp_with_no_stored_finding_gets_no_recommendation(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    """Non-carrier control: the refresh only replaces text a stored finding supplied.

    A called Standard non-carrier deliberately gets no ``snp_finding`` row. Looking
    the panel up regardless would hand them the carrier-facing advice the storage
    layer withheld.
    """
    _patch_panel(monkeypatch, module)

    assert _page_recommendation(monkeypatch, module, stored_finding=False) is None
