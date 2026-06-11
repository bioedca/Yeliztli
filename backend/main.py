"""Yeliztli FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routes.acmg import router as acmg_router
from backend.api.routes.admin import router as admin_router
from backend.api.routes.allergy import router as allergy_router
from backend.api.routes.alpha1 import router as alpha1_router
from backend.api.routes.amd import router as amd_router
from backend.api.routes.ancestry import router as ancestry_router
from backend.api.routes.annotation import router as annotation_router
from backend.api.routes.annotations_api import router as annotations_api_router
from backend.api.routes.apoe import router as apoe_router
from backend.api.routes.apol1 import router as apol1_router
from backend.api.routes.array_confidence import router as array_confidence_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.backup import router as backup_router
from backend.api.routes.cancer import router as cancer_router
from backend.api.routes.cardiovascular import router as cardiovascular_router
from backend.api.routes.carrier import router as carrier_router
from backend.api.routes.column_presets import router as column_presets_router
from backend.api.routes.custom_panels import router as custom_panels_router
from backend.api.routes.databases import (
    cleanup_interrupted_sessions,
    shutdown_executor,
)
from backend.api.routes.databases import (
    router as databases_router,
)
from backend.api.routes.encode_ccres import router as encode_ccres_router
from backend.api.routes.export import router as export_router
from backend.api.routes.findings import router as findings_router
from backend.api.routes.fitness import router as fitness_router
from backend.api.routes.gene_health import router as gene_health_router
from backend.api.routes.gene_validity import router as gene_validity_router
from backend.api.routes.genes import cache_router as uniprot_cache_router
from backend.api.routes.genes import router as genes_router
from backend.api.routes.gout import router as gout_router
from backend.api.routes.hemochromatosis import router as hemochromatosis_router
from backend.api.routes.igv_tracks import router as igv_tracks_router
from backend.api.routes.individuals import router as individuals_router
from backend.api.routes.ingest import router as ingest_router
from backend.api.routes.kinship import router as kinship_router
from backend.api.routes.lhon import router as lhon_router
from backend.api.routes.liftover import router as liftover_router
from backend.api.routes.methylation import router as methylation_router
from backend.api.routes.mt_rnr1 import router as mt_rnr1_router
from backend.api.routes.nuclear import router as nuclear_router
from backend.api.routes.nutrigenomics import router as nutrigenomics_router
from backend.api.routes.overlays import router as overlays_router
from backend.api.routes.parkinsons import router as parkinsons_router
from backend.api.routes.pharma import router as pharma_router
from backend.api.routes.preferences import router as preferences_router
from backend.api.routes.qc import router as qc_router
from backend.api.routes.query_builder import router as query_builder_router
from backend.api.routes.rare_variants import router as rare_variants_router
from backend.api.routes.reports import router as reports_router
from backend.api.routes.roh import router as roh_router
from backend.api.routes.samples import router as samples_router
from backend.api.routes.saved_queries import router as saved_queries_router
from backend.api.routes.setup import router as setup_router
from backend.api.routes.sex_aneuploidy import router as sex_aneuploidy_router
from backend.api.routes.skin import router as skin_router
from backend.api.routes.sleep import router as sleep_router
from backend.api.routes.tags import router as tags_router
from backend.api.routes.thrombophilia import router as thrombophilia_router
from backend.api.routes.traits import router as traits_router
from backend.api.routes.updates import router as updates_router
from backend.api.routes.variant_detail import router as variant_detail_router
from backend.api.routes.variants import router as variants_router
from backend.api.routes.warfarin import router as warfarin_router
from backend.api.routes.watches import router as watches_router
from backend.auth import AuthMiddleware
from backend.config import get_settings
from backend.db.connection import get_registry, reset_registry
from backend.db.database_registry import (
    PIPELINE_GENOME_BUILD,
    check_genome_build_consistency,
)
from backend.db.db_health import recover_orphaned_downloads
from backend.db.reference_schema import ensure_reference_schema_current
from backend.db.tables import reference_metadata
from backend.logging_config import configure_logging
from backend.tasks.huey_tasks import recover_orphaned_jobs

logger = logging.getLogger(__name__)

VERSION = "0.2.0"


# ── Lifespan ──────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle for the FastAPI app."""
    # Startup: ensure data directory exists before DB initialization
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "samples").mkdir(exist_ok=True)
    # Initialize the DB registry (creates reference engine, etc.)
    registry = get_registry()
    # Ensure reference tables exist (safe on existing DBs via checkfirst)
    reference_metadata.create_all(registry.reference_engine, checkfirst=True)
    # create_all only creates missing *tables*, never adds *columns* to
    # pre-existing ones. Backfill additive columns (e.g. samples.individual_id)
    # so DBs that predate a column-adding revision keep working.
    ensure_reference_schema_current(registry.reference_engine)
    # Cross-source genome-build provenance check (F30): warn — never fail — when
    # a recorded source's build deviates from its expected assembly (e.g. a
    # GRCh38 gnomAD bundle where the GRCh37 pipeline expects GRCh37). dbNSFP's
    # legitimate GRCh38 coordinates are expected and not flagged.
    build_skew = check_genome_build_consistency(registry.reference_engine)
    if build_skew:
        logger.warning(
            "genome_build_skew_detected",
            sources=build_skew,
            pipeline_build=PIPELINE_GENOME_BUILD,
        )
    # Configure structured logging with DB persistence
    configure_logging(engine_getter=lambda: registry.reference_engine)
    # Mark any leftover in-progress download sessions as interrupted/stale
    cleanup_interrupted_sessions(registry.reference_engine)
    # Mark any orphaned jobs (worker killed mid-task) as failed
    recover_orphaned_jobs(registry.reference_engine)
    # Mark any download checkpoints stuck mid-transfer as failed so the partial
    # surfaces as honestly resumable instead of a phantom "downloading" forever.
    recover_orphaned_downloads(registry.reference_engine)
    logger.info("DBRegistry initialised (reference.db engine ready)")
    yield
    # Shutdown: stop download executor and dispose all engines
    shutdown_executor()
    reset_registry()
    logger.info("DBRegistry disposed - all engines closed")


# ── App factory ───────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="Yeliztli",
        version=VERSION,
        lifespan=lifespan,
    )

    # CORS - restrict to localhost dev origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:8000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth middleware — enforces session auth when enabled
    app.add_middleware(AuthMiddleware)

    # Create a fresh API router per app instance to avoid duplicate routes
    api_router = APIRouter(prefix="/api")

    @api_router.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint. Always exempt from auth."""
        return {"status": "ok", "version": VERSION}

    # API routes (must be included BEFORE static mount)
    api_router.include_router(acmg_router)
    api_router.include_router(admin_router)
    api_router.include_router(auth_router)
    api_router.include_router(allergy_router)
    api_router.include_router(alpha1_router)
    api_router.include_router(amd_router)
    api_router.include_router(ancestry_router)
    api_router.include_router(apoe_router)
    api_router.include_router(apol1_router)
    api_router.include_router(array_confidence_router)
    api_router.include_router(backup_router)
    api_router.include_router(annotation_router)
    api_router.include_router(annotations_api_router)
    api_router.include_router(cancer_router)
    api_router.include_router(carrier_router)
    api_router.include_router(cardiovascular_router)
    api_router.include_router(column_presets_router)
    api_router.include_router(custom_panels_router)
    api_router.include_router(databases_router)
    api_router.include_router(encode_ccres_router)
    api_router.include_router(export_router)
    api_router.include_router(findings_router)
    api_router.include_router(fitness_router)
    api_router.include_router(gene_health_router)
    api_router.include_router(gene_validity_router)
    api_router.include_router(gout_router)
    api_router.include_router(hemochromatosis_router)
    api_router.include_router(genes_router)
    api_router.include_router(uniprot_cache_router)
    api_router.include_router(igv_tracks_router)
    api_router.include_router(individuals_router)
    api_router.include_router(ingest_router)
    api_router.include_router(kinship_router)
    api_router.include_router(lhon_router)
    api_router.include_router(liftover_router)
    api_router.include_router(methylation_router)
    api_router.include_router(mt_rnr1_router)
    api_router.include_router(nuclear_router)
    api_router.include_router(nutrigenomics_router)
    api_router.include_router(overlays_router)
    api_router.include_router(parkinsons_router)
    api_router.include_router(pharma_router)
    api_router.include_router(preferences_router)
    api_router.include_router(qc_router)
    api_router.include_router(query_builder_router)
    api_router.include_router(rare_variants_router)
    api_router.include_router(reports_router)
    api_router.include_router(roh_router)
    api_router.include_router(saved_queries_router)
    api_router.include_router(samples_router)
    api_router.include_router(setup_router)
    api_router.include_router(sex_aneuploidy_router)
    api_router.include_router(skin_router)
    api_router.include_router(sleep_router)
    api_router.include_router(tags_router)
    api_router.include_router(thrombophilia_router)
    api_router.include_router(traits_router)
    api_router.include_router(updates_router)
    api_router.include_router(variants_router)
    api_router.include_router(warfarin_router)
    api_router.include_router(watches_router)
    api_router.include_router(variant_detail_router)
    app.include_router(api_router)

    # Static files - SPA fallback (only if frontend has been built)
    frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")

    return app


app = create_app()

# ── Direct execution ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
