"""Route coverage for GET /api/hla/rule-outs (Wave D / SW-D3)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import backend.api.routes.hla as hla_routes
from backend.analysis.hla_resolver import ResolvedHLACall
from backend.analysis.hla_rule_outs import assess_rule_outs
from backend.api.routes.hla import (
    CeliacRuleOutResponse,
    NarcolepsyRuleOutResponse,
)


def _app(monkeypatch: pytest.MonkeyPatch, calls: list[ResolvedHLACall]) -> FastAPI:
    app = FastAPI()

    def fake_get_sample_engine(sample_id: int) -> object:
        if sample_id != 1:
            raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")
        return object()

    monkeypatch.setattr(hla_routes, "_get_sample_engine", fake_get_sample_engine)
    monkeypatch.setattr(hla_routes, "read_hla_calls", lambda _engine: calls)

    class RuleOutsShim:
        async def __call__(self, sample_id: int) -> hla_routes.RuleOutsResponse:
            return hla_routes.get_hla_rule_outs(sample_id=sample_id)

    app.add_api_route(
        "/api/hla/rule-outs",
        RuleOutsShim(),
        methods=["GET"],
        response_model=hla_routes.RuleOutsResponse,
    )

    return app


def _get(app: FastAPI, path: str) -> httpx.Response:
    return asyncio.run(_async_get(app, path))


async def _async_get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


def _resolved_call(locus, a1, a2, *, low=False) -> ResolvedHLACall:
    return ResolvedHLACall(
        locus=locus,
        allele1=a1,
        allele2=a2,
        prob=0.4 if low else 0.95,
        low_confidence=low,
        source="hibag",
        ancestry_model="European",
    )


def test_low_confidence_response_models_preserve_indeterminate() -> None:
    report = assess_rule_outs(
        [
            _resolved_call("DQA1", "01:01", "04:01", low=True),
            _resolved_call("DQB1", "05:01", "06:03", low=True),
        ]
    )

    celiac = CeliacRuleOutResponse(**vars(report.celiac)).model_dump()
    narcolepsy = NarcolepsyRuleOutResponse(**vars(report.narcolepsy)).model_dump()

    assert celiac["status"] == "indeterminate"
    assert celiac["low_confidence"] is True
    assert narcolepsy["status"] == "indeterminate"
    assert narcolepsy["low_confidence"] is True


def test_rule_outs_route_registered_with_response_model() -> None:
    route = next(
        route
        for route in hla_routes.router.routes
        if getattr(route, "path", None) == "/hla/rule-outs"
        and "GET" in getattr(route, "methods", set())
    )

    assert route.endpoint is hla_routes.get_hla_rule_outs
    assert route.response_model is hla_routes.RuleOutsResponse


class TestRuleOutsRoute:
    def test_surfaces_celiac_and_narcolepsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # DQ2.5 (DQA1*05:01 + DQB1*02:01) present; DQB1*06:02 absent -> narcolepsy negative.
        app = _app(
            monkeypatch,
            [
                _resolved_call("DQA1", "05:01", "01:01"),
                _resolved_call("DQB1", "02:01", "05:01"),
            ],
        )

        resp = _get(app, "/api/hla/rule-outs?sample_id=1")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["research_use_only"] is True
        assert body["caveat"]
        assert body["citations"]
        assert body["celiac"]["status"] == "permissive_present"
        assert any("DQ2.5" in d for d in body["celiac"]["detected"])
        assert body["narcolepsy"]["status"] == "absent_lowers"
        assert body["narcolepsy"]["carried"] is False

    def test_low_confidence_rule_outs_are_indeterminate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _app(
            monkeypatch,
            [
                _resolved_call("DQA1", "01:01", "04:01", low=True),
                _resolved_call("DQB1", "05:01", "06:03", low=True),
            ],
        )

        resp = _get(app, "/api/hla/rule-outs?sample_id=1")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["celiac"]["status"] == "indeterminate"
        assert body["celiac"]["low_confidence"] is True
        assert body["narcolepsy"]["status"] == "indeterminate"
        assert body["narcolepsy"]["low_confidence"] is True

    def test_empty_sample_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _app(monkeypatch, [])

        resp = _get(app, "/api/hla/rule-outs?sample_id=1")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is False
        assert body["celiac"] is None
        assert body["narcolepsy"] is None
        assert body["unavailable_note"]

    def test_missing_sample_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _app(monkeypatch, [])

        resp = _get(app, "/api/hla/rule-outs?sample_id=999")

        assert resp.status_code == 404
