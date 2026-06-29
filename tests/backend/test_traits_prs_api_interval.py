"""Direct PRS API regression for unsupported interval suppression."""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.api.routes import traits as traits_routes
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings


def test_traits_prs_route_suppresses_legacy_interval_fields(monkeypatch) -> None:
    """Stale stored bootstrap values must not be exposed by the PRS API."""
    sample_engine = sa.create_engine("sqlite://")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            {
                "module": "traits",
                "category": "prs",
                "evidence_level": 1,
                "finding_text": "Educational Attainment PRS - 62nd percentile",
                "pathway": "Cognitive Traits",
                "prs_percentile": 62.3,
                "pmid_citations": json.dumps(["35361970"]),
                "detail_json": json.dumps(
                    {
                        "trait": "educational_attainment",
                        "name": "Educational Attainment",
                        "z_score": 0.31,
                        "bootstrap_ci_lower": 48.1,
                        "bootstrap_ci_upper": 74.5,
                        "source_ancestry": "EUR",
                        "source_study": "Okbay et al. 2022",
                        "snps_used": 180,
                        "snps_total": 210,
                        "coverage_fraction": 0.857,
                        "is_sufficient": True,
                        "calibrated": True,
                        "research_use_only": True,
                    }
                ),
            },
        )

    monkeypatch.setattr(traits_routes, "_get_sample_engine", lambda sample_id: sample_engine)

    response = traits_routes.list_prs(sample_id=1)

    assert response.total == 1
    item = response.items[0]
    assert item.percentile == 62.3
    assert item.bootstrap_ci_lower is None
    assert item.bootstrap_ci_upper is None
