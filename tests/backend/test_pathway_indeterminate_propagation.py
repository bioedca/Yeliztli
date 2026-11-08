"""Synthetic scoring-to-presentation regression matrix for #2405.

These panels exercise software categories, not biological associations. No
production scorer, storage routine, or response serializer is mocked.
"""

import json
from importlib import import_module

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import require_fresh_sample
from backend.db.sample_schema import create_sample_tables, ensure_sample_schema_current
from backend.db.tables import annotation_state, findings, raw_variants, reference_metadata
from backend.reports.generator import _load_findings
from backend.reports.variant_card import _load_single_finding

MODULES = ["nutrigenomics", "methylation", "sleep", "skin", "allergy", "gene_health", "fitness"]
CASES = [
    ("all_indeterminate", ["AC", "AT"], [0, 1], "Not Assessed"),
    ("mixed", ["AC", "AA"], [0], "Standard"),
    ("missing", ["AG", None], [], "Tested Standard"),
    ("none_called", [None, None], [], "Not Assessed"),
    ("indeterminate_and_missing", ["AC", None], [0], "Not Assessed"),
    ("palindromic", ["AA", "TT"], [0, 1], "Not Assessed"),
    ("no_call", ["AA", "--"], [], "Tested Standard"),
    ("true_standard", ["AA", "AA"], [], "Standard"),
    ("elevated", ["AC", "GG"], [0], "Elevated"),
]


@pytest.fixture
def scored_pathway(request, tmp_path):
    module_name, case = request.param
    case_name, genotypes, indeterminate_indices, label = case
    module = import_module(f"backend.analysis.{module_name}")
    class_prefix = "".join(part.title() for part in module_name.split("_"))
    snps = [
        module.PanelSNP(
            rsid=f"rsYELIZTLI{i}",
            gene="SYNTHETIC",
            variant_name=f"Synthetic {i}",
            hgvs_protein=None,
            ref_allele="A",
            risk_allele="G",
            evidence_level=3,
            pmids=[],
            recommendation_text="Synthetic test only",
            genotype_effects={
                "AA": {"category": "Standard", "effect_summary": "Synthetic baseline"},
                "AG": {"category": "Standard", "effect_summary": "Synthetic baseline"},
                "GG": {"category": "Elevated", "effect_summary": "Synthetic elevated"},
            },
        )
        for i in range(2)
    ]
    if case_name == "palindromic":
        for snp in snps:
            snp.genotype_effects = {
                "AA": {"category": "Elevated", "effect_summary": "Synthetic elevated"},
                "TT": {"category": "Standard", "effect_summary": "Synthetic baseline"},
            }
    panel = getattr(module, f"{class_prefix}Panel")(
        module=module_name,
        version="synthetic",
        pathways=[
            module.Pathway(id="synthetic", name="Synthetic pathway", description="Test", snps=snps)
        ],
    )
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
    ref_engine = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    create_sample_tables(engine)
    reference_metadata.create_all(ref_engine)
    with engine.begin() as conn:
        for i, genotype in enumerate(genotypes):
            if genotype is not None:
                conn.execute(
                    raw_variants.insert().values(
                        rsid=snps[i].rsid,
                        chrom="1",
                        pos=i + 1,
                        genotype=genotype,
                    )
                )
    result = getattr(module, f"score_{module_name}_pathways")(panel, engine, ref_engine)
    assert len(result.pathway_results) == 1
    pathway = result.pathway_results[0]
    expected = [snps[i].rsid for i in indeterminate_indices]
    assert [s.rsid for s in pathway.called_snps if s.category == "Indeterminate"] == expected
    assert getattr(module, f"store_{module_name}_findings")(result, engine) > 0
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(
                findings.c.module == module_name,
                findings.c.category == "pathway_summary",
            )
        ).one()
    yield module_name, engine, row, expected, label
    engine.dispose()
    ref_engine.dispose()


@pytest.mark.parametrize("scored_pathway", [(m, c) for m in MODULES for c in CASES], indirect=True)
def test_scored_summary_and_endpoints(scored_pathway, monkeypatch):
    module_name, engine, row, expected, label = scored_pathway
    detail = json.loads(row.detail_json)
    assert detail["indeterminate_snps"] == expected
    if expected and row.pathway_level == "Standard":
        assert "no variants of concern" not in row.finding_text
        assert "observed but not interpreted (indeterminate)" in row.finding_text
        if label == "Not Assessed":
            assert "no variant scored" in row.finding_text
        else:
            assert "Standard for scored variants" in row.finding_text

    route = import_module(f"backend.api.routes.{module_name}")
    monkeypatch.setattr(route, "_get_sample_engine", lambda sample_id: engine)
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[require_fresh_sample] = lambda: None
    with TestClient(app) as client:
        response = client.get(f"/api{route.router.prefix}/pathways?sample_id=1")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["indeterminate_snps"] == expected
        response = client.get(f"/api{route.router.prefix}/pathway/synthetic?sample_id=1")
        assert response.status_code == 200
        assert response.json()["indeterminate_snps"] == expected

    report_rows = _load_findings(engine, modules=[module_name])
    assert report_rows
    summary = next(r for r in report_rows if r["id"] == row.id)
    assert summary["pathway_level_display"] == label
    assert _load_single_finding(engine, row.id)["pathway_level_display"] == label


@pytest.mark.parametrize(
    "scored_pathway",
    [
        (m, c)
        for m in MODULES
        for c in CASES
        if c[0] in ("all_indeterminate", "mixed", "indeterminate_and_missing")
    ],
    indirect=True,
)
def test_legacy_summary_is_corrected_across_read_paths(scored_pathway, monkeypatch):
    module_name, engine, row, expected, label = scored_pathway
    # Deliberately reconstruct the pre-deployment storage shape, retaining the
    # scorer-produced per-SNP categories. This is a separate legacy-read test.
    detail = json.loads(row.detail_json)
    detail.pop("indeterminate_snps", None)
    legacy_text = "Synthetic pathway — Standard (no variants of concern)"
    if detail["missing_snps"]:
        coverage = (
            "No variants of concern among tested SNPs; 1 tracked SNP (1 off-chip) not assessed."
        )
        detail["coverage_interpretation"] = coverage
        legacy_text = f"Synthetic pathway — {coverage}"
    with engine.begin() as conn:
        conn.execute(
            findings.update()
            .where(findings.c.id == row.id)
            .values(
                detail_json=json.dumps(detail),
                finding_text=legacy_text,
            )
        )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 33")
    assert ensure_sample_schema_current(engine) is True
    with engine.connect() as conn:
        repaired = conn.execute(sa.select(findings).where(findings.c.id == row.id)).one()
    assert repaired.finding_text == row.finding_text
    assert json.loads(repaired.detail_json) == json.loads(row.detail_json)
    assert ensure_sample_schema_current(engine) is False
    expected_phrase = (
        "no variant scored" if label == "Not Assessed" else "Standard for scored variants"
    )
    route = import_module(f"backend.api.routes.{module_name}")
    general = import_module("backend.api.routes.findings")
    monkeypatch.setattr(route, "_get_sample_engine", lambda sample_id: engine)
    monkeypatch.setattr(general, "_get_sample_engine", lambda sample_id: engine)
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.include_router(general.router, prefix="/api")
    app.dependency_overrides[require_fresh_sample] = lambda: None
    with TestClient(app) as client:
        for path in ["pathways", "pathway/synthetic"]:
            response = client.get(f"/api{route.router.prefix}/{path}?sample_id=1")
            assert response.status_code == 200
            body = response.json()
            if path == "pathways":
                assert len(body["items"]) == 1
                body = body["items"][0]
            assert body["indeterminate_snps"] == expected
        response = client.get("/api/analysis/findings?sample_id=1")
        assert response.status_code == 200
        assert response.json()
        summary = next(r for r in response.json() if r["id"] == row.id)
        assert summary["detail"]["indeterminate_snps"] == expected
        assert expected_phrase in summary["finding_text"]
        assert "no variants of concern" not in summary["finding_text"]
    report_rows = _load_findings(engine, modules=[module_name])
    assert report_rows
    report = next(r for r in report_rows if r["id"] == row.id)
    for rendered in [report, _load_single_finding(engine, row.id)]:
        assert rendered["pathway_level_display"] == label
        assert expected_phrase in rendered["finding_text"]
        assert "no variants of concern" not in rendered["finding_text"]


@pytest.mark.parametrize("scored_pathway", [(m, CASES[-1]) for m in MODULES], indirect=True)
def test_legacy_elevated_response_retains_indeterminate_coverage(scored_pathway, monkeypatch):
    module_name, engine, row, expected, _ = scored_pathway
    detail = json.loads(row.detail_json)
    detail.pop("indeterminate_snps")
    with engine.begin() as conn:
        conn.execute(
            findings.update()
            .where(findings.c.id == row.id)
            .values(
                detail_json=json.dumps(detail),
            )
        )
    route = import_module(f"backend.api.routes.{module_name}")
    monkeypatch.setattr(route, "_get_sample_engine", lambda sample_id: engine)
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[require_fresh_sample] = lambda: None
    with TestClient(app) as client:
        for path in ["pathways", "pathway/synthetic"]:
            response = client.get(f"/api{route.router.prefix}/{path}?sample_id=1")
            assert response.status_code == 200
            body = response.json()
            if path == "pathways":
                assert len(body["items"]) == 1
                body = body["items"][0]
            assert body["level"] == "Elevated"
            assert body["indeterminate_snps"] == expected


@pytest.mark.parametrize("scored_pathway", [("nutrigenomics", CASES[0])], indirect=True)
def test_migration_banner_repairs_are_scoped_by_module(scored_pathway):
    _, engine, row, _, _ = scored_pathway
    detail = json.loads(row.detail_json)
    detail.pop("indeterminate_snps")
    legacy_text = "Synthetic pathway — Standard (no variants of concern)"
    other_detail = json.loads(json.dumps(detail))
    other_detail["snp_details"][1]["category"] = "Standard"
    base = dict(row._mapping)
    base.pop("id")
    base.update(finding_text=legacy_text, detail_json=json.dumps(detail))
    entry = {
        "module": "nutrigenomics",
        "category": "pathway_summary",
        "pathway": "Synthetic pathway",
        "finding_text": legacy_text,
    }
    added = [
        entry,
        {**entry, "module": "allergy"},
        {**entry, "module": "unrelated"},
        {**entry, "module": []},
    ]
    diff = {"added": added, "changed": [], "removed": [entry], "unchanged_count": 8}
    with engine.begin() as conn:
        conn.execute(findings.delete())
        conn.execute(findings.insert().values(**base))
        conn.execute(
            findings.insert().values(
                **{
                    **base,
                    "module": "allergy",
                    "detail_json": json.dumps(other_detail),
                }
            )
        )
        conn.execute(
            annotation_state.insert().values(
                key="last_finding_diff_json",
                value=json.dumps(diff),
            )
        )
        conn.exec_driver_sql("PRAGMA user_version = 33")
    assert ensure_sample_schema_current(engine) is True
    with engine.connect() as conn:
        repaired = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json",
                )
            ).scalar_one()
        )
    assert "no variant scored" in repaired["added"][0]["finding_text"]
    assert "Standard for scored variants" in repaired["added"][1]["finding_text"]
    assert repaired["added"][2:] == added[2:]
    assert repaired["removed"] == diff["removed"]
    assert repaired["changed"] == []
    assert repaired["unchanged_count"] == 8
