"""Tests for ``backend.api.dependencies.require_fresh_sample``.

Plan §7.5 — two locked contracts:

1. **Unit behaviour.** The dependency returns ``sample_id`` unchanged
   when the sample is fresh, raises ``HTTPException(423, ...)`` when
   stale, and the ``detail`` payload carries the four keys mandated by
   the plan (``installed_version`` / ``required_version`` /
   ``update_url`` / ``reannotate_url``). Major-only comparison: minor /
   patch differences are not stale.

2. **Drift guard.** Every route under ``backend/api/routes/*.py`` with a
   ``sample_id`` (or alias ``merged_id``) path/query parameter must be
   declared in exactly one of the gated / opt-out lists below. A new
   sample-scoped route added later — without an explicit list entry —
   fails this test, forcing the author into the Plan §7.5 enumeration.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException
from fastapi.params import Body

from backend.api.dependencies import _sample_existence, require_fresh_sample
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotation_state,
    database_versions,
    reference_metadata,
    samples,
)

# ── Plan §7.5 lists ────────────────────────────────────────────────────

# Fully gated route modules (every route in the module declares
# ``Depends(require_fresh_sample)`` after step 13).
_FULLY_GATED_MODULES = frozenset(
    {
        "acmg",
        "allergy",
        "alpha1",
        "amd",
        "ancestry",
        "annotations_api",
        "apoe",
        "apol1",
        "array_confidence",
        "bche",
        "cancer",
        "cardiovascular",
        "carrier",
        "custom_panels",
        "export",
        "fh",
        "findings",
        "fitness",
        "g6pd",
        "gene_health",
        "gene_validity",
        "genes",
        "gout",
        "hemochromatosis",
        "igv_tracks",
        "kinship",
        "lhon",
        "liftover",
        "metabolic",
        "methylation",
        "mt_rnr1",
        "nutrigenomics",
        "overlays",
        "parkinsons",
        "pgx_guidelines",
        "pharma",
        "qc",
        "query_builder",
        "rare_variants",
        "reports",
        "roh",
        "sex_aneuploidy",
        "skin",
        "sleep",
        "tags",
        "thrombophilia",
        "traits",
        "variant_detail",
        "variants",
        "warfarin",
        "watches",
    }
)

# Fully opt-out modules (must NOT declare the dependency).
_FULLY_OPT_OUT_MODULES = frozenset(
    {
        "admin",
        "annotation",
        "auth",
        "backup",
        "column_presets",
        "databases",
        "ebmd",
        "encode_ccres",
        "individuals",  # added in Phase 2 (step 47); no `sample_id` today
        "ingest",
        "nuclear",
        "preferences",
        # Shared risk-genotype router *factory* (make_risk_router). It exports no
        # router of its own — the gated /findings + /run routes it builds live on
        # the consuming risk modules, which are in _FULLY_GATED_MODULES.
        "risk_common",
        "saved_queries",
        "setup",
        "updates",
    }
)

# samples.py — partial gate, declared at the subroute level (Plan §7.5).
# Routes that exist today and are bare-metadata (opt-out):
_SAMPLES_OPT_OUT_PATHS = frozenset(
    {
        ("GET", "/api/samples"),
        ("GET", "/api/samples/{sample_id}"),
        ("PATCH", "/api/samples/{sample_id}"),
        ("DELETE", "/api/samples/{sample_id}"),
        # Step 66 / MRG-02a — cascade preview. Registry-level walk over
        # samples.file_format == 'merged_v1'; not analysis output. Must
        # stay reachable when the source is stale so the user can delete
        # without first re-annotating.
        ("GET", "/api/samples/{sample_id}/merged-children"),
    }
)
# Analysis-scoped subroutes (gated). Some are introduced in later steps;
# the membership set is declared up-front so step 13 needs no edits
# here.
_SAMPLES_GATED_PATHS = frozenset(
    {
        ("GET", "/api/samples/{sample_id}/merge-provenance"),
        ("GET", "/api/samples/{sample_id}/concordance-report"),
        (
            "GET",
            "/api/samples/{merged_id}/watched-variants/migrate-from-sources",
        ),
    }
)


_REPO_MANIFEST = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def manifest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point manifest fetches at the in-repo manifest (deterministic, offline)."""
    monkeypatch.setenv("YELIZTLI_MANIFEST_PATH", str(_REPO_MANIFEST))
    from backend.db.manifest import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def gate_env(tmp_data_dir: Path):
    """Reference DB seeded with one sample row; per-sample DB created lazily."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Sample 1",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc",
            )
        )
    ref_engine.dispose()

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        yield {"settings": settings, "sample_id": 1}
        reset_registry()


def _seed_installed_bundle(settings: Settings, version: str) -> None:
    engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="vep_bundle",
                    version=version,
                    downloaded_at=datetime.now(UTC),
                )
            )
    finally:
        engine.dispose()


def _make_sample_db(
    settings: Settings,
    *,
    create_state_table: bool = True,
    seed_version: str | None,
) -> None:
    sample_db_path = settings.data_dir / "samples" / "sample_1.db"
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    try:
        if create_state_table:
            create_sample_tables(engine)
            if seed_version is not None:
                with engine.begin() as conn:
                    conn.execute(
                        annotation_state.insert().values(
                            key="vep_bundle_version",
                            value=seed_version,
                        )
                    )
        else:
            with engine.begin() as conn:
                conn.execute(sa.text("CREATE TABLE _placeholder (id INTEGER)"))
    finally:
        engine.dispose()


# ── Unit tests for require_fresh_sample ────────────────────────────────


class TestRequireFreshSample:
    def test_fresh_sample_returns_id(self, manifest_env, gate_env):
        _seed_installed_bundle(gate_env["settings"], "v2.0.0")
        _make_sample_db(gate_env["settings"], seed_version="v2.0.0")

        assert require_fresh_sample(gate_env["sample_id"]) == 1

    def test_minor_patch_difference_passes(self, manifest_env, gate_env):
        # Plan §7.4 — only major-version drift gates.
        _seed_installed_bundle(gate_env["settings"], "v2.3.1")
        _make_sample_db(gate_env["settings"], seed_version="v2.0.0")

        assert require_fresh_sample(gate_env["sample_id"]) == 1

    def test_stale_sample_raises_423(self, manifest_env, gate_env):
        _seed_installed_bundle(gate_env["settings"], "v2.0.0")
        _make_sample_db(gate_env["settings"], seed_version="v1.0.0")

        with pytest.raises(HTTPException) as exc:
            require_fresh_sample(gate_env["sample_id"])
        assert exc.value.status_code == 423

    def test_423_payload_carries_required_keys(self, manifest_env, gate_env):
        _seed_installed_bundle(gate_env["settings"], "v2.0.0")
        _make_sample_db(gate_env["settings"], seed_version="v1.0.0")

        with pytest.raises(HTTPException) as exc:
            require_fresh_sample(gate_env["sample_id"])
        detail = exc.value.detail

        assert {
            "installed_version",
            "required_version",
            "update_url",
            "reannotate_url",
        } <= set(detail.keys())

        # The sample's recorded version (the "installed" annotation state) and
        # the live bundle's version (the "required" target). required_version is
        # the manifest's vep_bundle version, bumped to v3.0.0 for the G1
        # re-annotation trigger.
        assert detail["installed_version"] == "v1.0.0"
        assert detail["required_version"] == "v3.0.0"
        # Plan §7.5 — escape hatch points at annotation.py.
        assert detail["reannotate_url"] == "/api/annotation/1"
        # Manifest fixture exposes the published URL.
        assert detail["update_url"]

    def test_never_annotated_sample_not_gated(self, manifest_env, gate_env):
        # A sample whose per-sample DB has no annotation_state table has
        # never completed an annotation run (the row is written only on a
        # successful annotation). It is NOT stale — it needs its *first*
        # annotation, surfaced by the dashboard's "Run Annotation" CTA, not
        # the re-annotation banner. require_fresh_sample lets it through so
        # analysis routes can return their own empty/404 state.
        _seed_installed_bundle(gate_env["settings"], "v2.0.0")
        _make_sample_db(
            gate_env["settings"],
            create_state_table=False,
            seed_version=None,
        )

        assert require_fresh_sample(gate_env["sample_id"]) == 1

    def test_fresh_import_missing_version_row_not_gated(self, manifest_env, gate_env):
        # annotation_state table present (fresh per-sample schema) but no
        # vep_bundle_version row yet — a freshly imported, never-annotated
        # sample. Must not be gated as stale (the bug: it otherwise showed
        # "re-annotate against v2.0.0" against the current bundle).
        _seed_installed_bundle(gate_env["settings"], "v2.0.0")
        _make_sample_db(gate_env["settings"], seed_version=None)

        assert require_fresh_sample(gate_env["sample_id"]) == 1

    def test_required_version_falls_back_to_db_row(self, monkeypatch, gate_env):
        # When the manifest is unreachable, the payload still reports the
        # installed bundle's version (read from database_versions).
        monkeypatch.setattr(
            "backend.api.dependencies.get_bundle_info",
            lambda name: None,
        )
        _seed_installed_bundle(gate_env["settings"], "v2.0.0")
        _make_sample_db(gate_env["settings"], seed_version="v1.0.0")

        with pytest.raises(HTTPException) as exc:
            require_fresh_sample(gate_env["sample_id"])
        assert exc.value.detail["required_version"] == "v2.0.0"

    def test_missing_sample_raises_404(self, manifest_env, gate_env):
        # #453 — a *missing* samples row is answered 404, not 423. Existence is
        # checked before staleness, so the gate no longer leaks-vs-blocks by
        # bundle state. gate_env seeds only sample id=1, so 9999 is absent.
        with pytest.raises(HTTPException) as exc:
            require_fresh_sample(9999)
        assert exc.value.status_code == 404
        assert "9999" in str(exc.value.detail)

    def test_missing_sample_404_is_independent_of_bundle_baseline(self, manifest_env, gate_env):
        # Determinism (#453): even with a newer baseline installed — which would
        # have made the Plan §7.4 v1.0.0 missing-state fallback look "stale" and
        # answered 423 under the old contract — a missing sample is still 404.
        _seed_installed_bundle(gate_env["settings"], "v3.0.0")
        with pytest.raises(HTTPException) as exc:
            require_fresh_sample(9999)
        assert exc.value.status_code == 404


class TestSampleExistence:
    """Tri-state ``_sample_existence`` underpinning the #453 missing → 404 gate."""

    def test_existing_sample_is_true(self, gate_env):
        assert _sample_existence(gate_env["sample_id"]) is True

    def test_absent_sample_is_false(self, gate_env):
        # samples table is readable and has no id=9999 row → definitively absent.
        assert _sample_existence(9999) is False

    def test_unreadable_table_is_none(self, tmp_data_dir):
        # No reference_metadata.create_all → the ``samples`` table does not exist,
        # so existence cannot be affirmed. Must be None (unknown), NOT False — the
        # gate falls through to staleness instead of 404-ing on a read it could
        # not make (preserving the "never raise" contract).
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with patch("backend.db.connection.get_settings", return_value=settings):
            reset_registry()
            try:
                assert _sample_existence(1) is None
            finally:
                reset_registry()


# ── Drift guard ────────────────────────────────────────────────────────


_ROUTES_DIR = Path(__file__).resolve().parents[2] / "backend" / "api" / "routes"


def _iter_routers() -> list[tuple[str, APIRouter]]:
    """Yield ``(module_stem, router)`` for every ``APIRouter`` exported by
    ``backend.api.routes.*``. ``genes.py`` exports two routers
    (``router`` + ``cache_router``); both are walked."""
    rows: list[tuple[str, APIRouter]] = []
    for path in sorted(_ROUTES_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        mod = importlib.import_module(f"backend.api.routes.{path.stem}")
        for attr in vars(mod).values():
            if isinstance(attr, APIRouter):
                rows.append((path.stem, attr))
    return rows


def _full_path(router: APIRouter, route) -> str:
    # FastAPI bakes ``router.prefix`` into ``route.path`` at registration
    # time; only the ``/api`` mount prefix from ``backend.main`` remains
    # to be prepended.
    del router  # unused — kept for call-site clarity
    return f"/api{route.path}"


def _route_takes_sample_id(route) -> bool:
    """Path or query parameter named ``sample_id`` / ``merged_id``.

    Per Plan §7.5 the drift guard is scoped to path/query parameters.
    Routes that carry ``sample_id`` only inside a Pydantic body model
    are outside its strict wording (gating those is handled
    out-of-band in step 13).
    """
    if "{sample_id}" in route.path or "{merged_id}" in route.path:
        return True
    try:
        sig = inspect.signature(route.endpoint)
    except (TypeError, ValueError):
        return False
    for name, param in sig.parameters.items():
        if name not in {"sample_id", "merged_id"}:
            continue
        # Skip explicit Body(...) defaults.
        if isinstance(param.default, Body):
            continue
        return True
    return False


def _classify(module: str, method: str, full_path: str) -> str | None:
    if module in _FULLY_GATED_MODULES:
        return "gated"
    if module in _FULLY_OPT_OUT_MODULES:
        return "opt_out"
    if module == "samples":
        if (method, full_path) in _SAMPLES_GATED_PATHS:
            return "gated"
        if (method, full_path) in _SAMPLES_OPT_OUT_PATHS:
            return "opt_out"
    return None


def _collect_sample_id_routes() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for module, router in _iter_routers():
        for route in router.routes:
            if not hasattr(route, "methods") or route.methods is None:
                continue
            if not _route_takes_sample_id(route):
                continue
            full_path = _full_path(router, route)
            for method in sorted(route.methods):
                if method == "HEAD":
                    continue
                rows.append((module, method, full_path))
    return rows


def test_module_lists_disjoint() -> None:
    assert _FULLY_GATED_MODULES.isdisjoint(_FULLY_OPT_OUT_MODULES)


def test_samples_subroute_lists_disjoint() -> None:
    assert _SAMPLES_GATED_PATHS.isdisjoint(_SAMPLES_OPT_OUT_PATHS)


def test_every_route_module_declared() -> None:
    """Every routes module is in exactly one of the two module lists or
    is the special-cased ``samples``."""
    modules = {p.stem for p in _ROUTES_DIR.glob("*.py") if p.stem != "__init__"}
    declared = _FULLY_GATED_MODULES | _FULLY_OPT_OUT_MODULES | {"samples"}
    missing = modules - declared
    assert not missing, f"Route modules not declared in either Plan §7.5 list: {sorted(missing)}."


@pytest.mark.parametrize(
    "module,method,full_path",
    _collect_sample_id_routes(),
)
def test_every_sample_id_route_classified(module: str, method: str, full_path: str) -> None:
    """Plan §7.5 drift guard.

    Every route under ``backend/api/routes/*.py`` that takes a
    ``sample_id`` (or alias ``merged_id``) path/query parameter must
    map to exactly one of the gated / opt-out lists above. A new
    sample-scoped route added later trips this test until the author
    declares which list it belongs to.
    """
    classification = _classify(module, method, full_path)
    assert classification is not None, (
        f"{method} {full_path} (in {module}.py) takes sample_id but is "
        "not classified by the Plan §7.5 lists in "
        "tests/backend/test_stale_sample_dependency.py — add it to "
        "the gated or opt-out enumeration."
    )
