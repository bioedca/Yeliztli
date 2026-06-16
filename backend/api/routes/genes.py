"""Gene detail page API (P3-41).

Provides protein domain data (UniProt, cached with 30-day TTL),
gene-phenotype records, PubMed literature, and per-gene variant
summaries for the Nightingale protein visualization page.

GET  /api/genes/{symbol}          — Full gene detail
GET  /api/genes/{symbol}/variants — Variants in gene for a sample
POST /api/genes/{symbol}/refresh-uniprot — Force refresh UniProt cache
GET  /api/uniprot-cache/stats     — UniProt cache statistics
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import require_fresh_sample
from backend.config import get_settings
from backend.db.connection import get_registry
from backend.db.tables import (
    annotated_variants,
    gene_phenotype,
    samples,
    uniprot_cache,
)
from backend.utils.pubmed import PubMedFetcher
from backend.utils.uniprot import UniProtCacheFetcher

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/genes", tags=["gene-detail"])

# Default TTL for UniProt cache entries
_UNIPROT_TTL_DAYS = 30

# UniProt REST API base URL
_UNIPROT_API_BASE = "https://rest.uniprot.org/uniprotkb"


# ── Response models ──────────────────────────────────────────────────


class ProteinDomain(BaseModel):
    """A single protein domain from UniProt."""

    type: str  # e.g. "Domain", "Region", "Motif"
    description: str
    start: int
    end: int


class ProteinFeature(BaseModel):
    """A protein feature annotation from UniProt."""

    type: str  # e.g. "Active site", "Binding site", "Disulfide bond"
    description: str
    position: int | None = None
    start: int | None = None
    end: int | None = None


class UniProtData(BaseModel):
    """UniProt protein data for Nightingale rendering."""

    accession: str
    gene_symbol: str
    sequence_length: int
    domains: list[ProteinDomain] = []
    features: list[ProteinFeature] = []
    fetched_at: str | None = None
    is_cached: bool = False


class GenePhenotypeRecord(BaseModel):
    """Gene-phenotype association from MONDO/HPO or OMIM."""

    gene_symbol: str
    disease_name: str
    disease_id: str | None = None
    source: str
    hpo_terms: list[str] | None = None
    inheritance: str | None = None
    omim_link: str | None = None


class PubMedArticleResponse(BaseModel):
    """A PubMed article summary."""

    pmid: str
    title: str
    abstract: str
    authors: list[str] = []
    journal: str = ""
    year: int | None = None
    is_stale: bool = False


class GeneVariantSummary(BaseModel):
    """Summary of a variant in this gene from the sample."""

    rsid: str
    chrom: str
    pos: int
    genotype: str | None = None
    consequence: str | None = None
    hgvs_protein: str | None = None
    hgvs_coding: str | None = None
    clinvar_significance: str | None = None
    clinvar_review_stars: int | None = None
    gnomad_af_global: float | None = None
    cadd_phred: float | None = None
    evidence_conflict: bool | None = None
    annotation_coverage: int | None = None


class PopulationAFSummary(BaseModel):
    """Per-population allele frequency summary across gene variants."""

    rsid: str
    hgvs_protein: str | None = None
    gnomad_af_global: float | None = None
    gnomad_af_afr: float | None = None
    gnomad_af_amr: float | None = None
    gnomad_af_eas: float | None = None
    gnomad_af_eur: float | None = None
    gnomad_af_fin: float | None = None
    gnomad_af_sas: float | None = None


class GeneDetailResponse(BaseModel):
    """Full gene detail for the gene detail page."""

    gene_symbol: str
    uniprot: UniProtData | None = None
    uniprot_error: str | None = None
    phenotypes: list[GenePhenotypeRecord] = []
    literature: list[PubMedArticleResponse] = []
    literature_errors: list[str] = []
    variants: list[GeneVariantSummary] = []
    population_af: list[PopulationAFSummary] = []


class GeneVariantsResponse(BaseModel):
    """Variants in a gene for a specific sample."""

    gene_symbol: str
    variants: list[GeneVariantSummary] = []
    total: int = 0


# ── Helpers ──────────────────────────────────────────────────────────


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve sample_id to a per-sample DB engine."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")

    sample_db_path = registry.settings.data_dir / row.db_path
    if not sample_db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sample database file not found for sample {sample_id}.",
        )
    return registry.get_sample_engine(sample_db_path)


def _parse_uniprot_cache_row(row: sa.Row, gene_symbol: str) -> UniProtData:
    """Parse a uniprot_cache DB row into a UniProtData model."""
    domains: list[ProteinDomain] = []
    if row.domains:
        try:
            for d in json.loads(row.domains):
                domains.append(ProteinDomain(**d))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning(
                "uniprot_cache_domains_parse_error",
                gene=gene_symbol,
                error=str(exc),
            )

    features: list[ProteinFeature] = []
    if row.features:
        try:
            for f in json.loads(row.features):
                features.append(ProteinFeature(**f))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning(
                "uniprot_cache_features_parse_error",
                gene=gene_symbol,
                error=str(exc),
            )

    fetched_at = row.fetched_at
    return UniProtData(
        accession=row.accession,
        gene_symbol=gene_symbol,
        sequence_length=row.sequence_length or 0,
        domains=domains,
        features=features,
        fetched_at=str(fetched_at) if fetched_at else None,
        is_cached=True,
    )


def _fetch_uniprot_from_cache(gene_symbol: str) -> UniProtData | None:
    """Check the UniProt cache for a fresh entry."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(uniprot_cache).where(uniprot_cache.c.gene_symbol == gene_symbol)
        ).fetchone()

    if row is None:
        return None

    # Check TTL
    fetched_at = row.fetched_at
    ttl = row.ttl_days or _UNIPROT_TTL_DAYS
    if fetched_at is not None:
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        cutoff = datetime.now(UTC) - timedelta(days=ttl)
        if fetched_at < cutoff:
            return None  # Stale — will be refreshed

    return _parse_uniprot_cache_row(row, gene_symbol)


def _fetch_uniprot_from_api(gene_symbol: str) -> UniProtData | None:
    """Fetch protein data from UniProt REST API and cache it.

    Returns None on network failure (graceful offline fallback).
    """
    import httpx

    try:
        # Search UniProt for the gene symbol (human, reviewed/Swiss-Prot).
        # URL-encode the user-supplied symbol so it cannot inject extra query
        # params or otherwise alter the request (the host is a fixed constant,
        # so this addresses the CodeQL py/partial-ssrf finding).
        encoded_symbol = quote(gene_symbol, safe="")
        search_url = (
            f"{_UNIPROT_API_BASE}/search"
            f"?query=gene_exact:{encoded_symbol}+AND+organism_id:9606+AND+reviewed:true"
            f"&format=json&size=1"
            f"&fields=accession,gene_names,sequence,features"
        )
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(search_url)
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if not results:
            logger.info("uniprot_no_results", gene=gene_symbol)
            return None

        entry = results[0]
        accession = entry.get("primaryAccession", "")
        seq_length = entry.get("sequence", {}).get("length", 0)

        # Extract domains and features
        domains: list[ProteinDomain] = []
        features: list[ProteinFeature] = []

        for feat in entry.get("features", []):
            feat_type = feat.get("type", "")
            desc = feat.get("description", "")
            loc = feat.get("location", {})
            start_val = loc.get("start", {}).get("value")
            end_val = loc.get("end", {}).get("value")

            if feat_type in ("Domain", "Region", "Repeat", "Zinc finger", "Motif"):
                if start_val is not None and end_val is not None:
                    domains.append(
                        ProteinDomain(
                            type=feat_type,
                            description=desc,
                            start=start_val,
                            end=end_val,
                        )
                    )
            elif feat_type in (
                "Active site",
                "Binding site",
                "Site",
                "Disulfide bond",
                "Modified residue",
                "Glycosylation",
                "Lipidation",
            ):
                features.append(
                    ProteinFeature(
                        type=feat_type,
                        description=desc,
                        position=start_val,
                        start=start_val,
                        end=end_val,
                    )
                )

        # Store in cache
        _store_uniprot_cache(
            accession=accession,
            gene_symbol=gene_symbol,
            domains=domains,
            features=features,
            sequence_length=seq_length,
        )

        logger.info(
            "uniprot_fetched",
            gene=gene_symbol,
            accession=accession,
            domains=len(domains),
            features=len(features),
        )

        return UniProtData(
            accession=accession,
            gene_symbol=gene_symbol,
            sequence_length=seq_length,
            domains=domains,
            features=features,
            fetched_at=str(datetime.now(UTC)),
            is_cached=False,
        )

    except Exception:
        logger.exception("uniprot_fetch_failed", gene=gene_symbol)
        return None


def _store_uniprot_cache(
    *,
    accession: str,
    gene_symbol: str,
    domains: list[ProteinDomain],
    features: list[ProteinFeature],
    sequence_length: int,
) -> None:
    """Insert or update the UniProt cache entry."""
    registry = get_registry()
    domains_json = json.dumps([d.model_dump() for d in domains])
    features_json = json.dumps([f.model_dump() for f in features])
    now = datetime.now(UTC)

    with registry.reference_engine.begin() as conn:
        existing = conn.execute(
            sa.select(uniprot_cache.c.accession).where(uniprot_cache.c.gene_symbol == gene_symbol)
        ).fetchone()

        if existing:
            conn.execute(
                uniprot_cache.update()
                .where(uniprot_cache.c.accession == existing.accession)
                .values(
                    accession=accession,
                    gene_symbol=gene_symbol,
                    domains=domains_json,
                    features=features_json,
                    sequence_length=sequence_length,
                    fetched_at=now,
                    ttl_days=_UNIPROT_TTL_DAYS,
                )
            )
        else:
            conn.execute(
                uniprot_cache.insert().values(
                    accession=accession,
                    gene_symbol=gene_symbol,
                    domains=domains_json,
                    features=features_json,
                    sequence_length=sequence_length,
                    fetched_at=now,
                    ttl_days=_UNIPROT_TTL_DAYS,
                )
            )


def _get_stale_uniprot(gene_symbol: str) -> UniProtData | None:
    """Return stale cache entry for offline fallback."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(uniprot_cache).where(uniprot_cache.c.gene_symbol == gene_symbol)
        ).fetchone()

    if row is None:
        return None

    return _parse_uniprot_cache_row(row, gene_symbol)


def _fetch_gene_phenotypes(gene_symbol: str) -> list[GenePhenotypeRecord]:
    """Fetch gene-phenotype associations from reference.db."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(gene_phenotype).where(gene_phenotype.c.gene_symbol == gene_symbol)
        ).fetchall()

    results = []
    for row in rows:
        hpo_list: list[str] | None = None
        if row.hpo_terms:
            try:
                hpo_list = json.loads(row.hpo_terms)
            except (json.JSONDecodeError, TypeError):
                hpo_list = None

        omim_link: str | None = None
        if row.disease_id and row.disease_id.startswith("OMIM:"):
            omim_id = row.disease_id.replace("OMIM:", "")
            omim_link = f"https://omim.org/entry/{omim_id}"

        results.append(
            GenePhenotypeRecord(
                gene_symbol=row.gene_symbol,
                disease_name=row.disease_name,
                disease_id=row.disease_id,
                source=row.source,
                hpo_terms=hpo_list,
                inheritance=row.inheritance,
                omim_link=omim_link,
            )
        )
    return results


def _fetch_gene_literature(gene_symbol: str) -> tuple[list[PubMedArticleResponse], list[str]]:
    """Fetch PubMed literature for a gene, cache-first."""
    settings = get_settings()
    registry = get_registry()

    fetcher = PubMedFetcher(
        registry.reference_engine,
        email=settings.pubmed_email or "",
        api_key=settings.pubmed_api_key if settings.pubmed_email else "",
    )
    result = fetcher.search_by_gene(gene_symbol, max_results=10)

    articles = [
        PubMedArticleResponse(
            pmid=a.pmid,
            title=a.title,
            abstract=a.abstract,
            authors=a.authors,
            journal=a.journal,
            year=a.year,
            is_stale=a.is_stale,
        )
        for a in result.articles
    ]
    return articles, result.errors


def _fetch_gene_variants(gene_symbol: str, sample_engine: sa.Engine) -> list[GeneVariantSummary]:
    """Fetch all annotated variants for a gene from the sample DB."""
    av = annotated_variants
    stmt = (
        sa.select(
            av.c.rsid,
            av.c.chrom,
            av.c.pos,
            av.c.genotype,
            av.c.consequence,
            av.c.hgvs_protein,
            av.c.hgvs_coding,
            av.c.clinvar_significance,
            av.c.clinvar_review_stars,
            av.c.gnomad_af_global,
            av.c.cadd_phred,
            av.c.evidence_conflict,
            av.c.annotation_coverage,
        )
        .where(av.c.gene_symbol == gene_symbol)
        .order_by(av.c.pos)
    )

    with sample_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    return [
        GeneVariantSummary(
            rsid=row.rsid,
            chrom=row.chrom,
            pos=row.pos,
            genotype=row.genotype,
            consequence=row.consequence,
            hgvs_protein=row.hgvs_protein,
            hgvs_coding=row.hgvs_coding,
            clinvar_significance=row.clinvar_significance,
            clinvar_review_stars=row.clinvar_review_stars,
            gnomad_af_global=row.gnomad_af_global,
            cadd_phred=row.cadd_phred,
            evidence_conflict=(
                bool(row.evidence_conflict) if row.evidence_conflict is not None else None
            ),
            annotation_coverage=row.annotation_coverage,
        )
        for row in rows
    ]


def _fetch_population_af(gene_symbol: str, sample_engine: sa.Engine) -> list[PopulationAFSummary]:
    """Fetch per-population AF for gene variants (for the AF bar chart)."""
    av = annotated_variants
    stmt = (
        sa.select(
            av.c.rsid,
            av.c.hgvs_protein,
            av.c.gnomad_af_global,
            av.c.gnomad_af_afr,
            av.c.gnomad_af_amr,
            av.c.gnomad_af_eas,
            av.c.gnomad_af_eur,
            av.c.gnomad_af_fin,
            av.c.gnomad_af_sas,
        )
        .where(
            sa.and_(
                av.c.gene_symbol == gene_symbol,
                av.c.gnomad_af_global.isnot(None),
            )
        )
        .order_by(av.c.pos)
    )

    with sample_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    return [
        PopulationAFSummary(
            rsid=row.rsid,
            hgvs_protein=row.hgvs_protein,
            gnomad_af_global=row.gnomad_af_global,
            gnomad_af_afr=row.gnomad_af_afr,
            gnomad_af_amr=row.gnomad_af_amr,
            gnomad_af_eas=row.gnomad_af_eas,
            gnomad_af_eur=row.gnomad_af_eur,
            gnomad_af_fin=row.gnomad_af_fin,
            gnomad_af_sas=row.gnomad_af_sas,
        )
        for row in rows
    ]


# ── Endpoints ────────────────────────────────────────────────────────


@router.get(
    "/{symbol}",
    response_model=GeneDetailResponse,
    dependencies=[Depends(require_fresh_sample)],
)
def get_gene_detail(
    symbol: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> GeneDetailResponse:
    """Return full gene detail for the gene detail page.

    Includes UniProt protein domain data (Nightingale), gene-phenotype
    records, PubMed literature, variants in this gene, and per-population
    allele frequency data.

    Example: ``GET /api/genes/BRCA1?sample_id=1``
    """
    gene_symbol = symbol.upper()
    sample_engine = _get_sample_engine(sample_id)

    # 1. UniProt protein data (cache-first, 30-day TTL)
    uniprot_data: UniProtData | None = None
    uniprot_error: str | None = None

    # Try fresh cache first
    uniprot_data = _fetch_uniprot_from_cache(gene_symbol)
    if uniprot_data is None:
        # Try live API fetch
        uniprot_data = _fetch_uniprot_from_api(gene_symbol)
        if uniprot_data is None:
            # Offline fallback: return stale cache if available
            uniprot_data = _get_stale_uniprot(gene_symbol)
            if uniprot_data is None:
                uniprot_error = "Protein data unavailable offline."
            else:
                uniprot_error = "Protein data may be outdated (offline fallback)."

    # 2. Gene-phenotype records
    phenotypes = _fetch_gene_phenotypes(gene_symbol)

    # 3. PubMed literature
    literature, lit_errors = _fetch_gene_literature(gene_symbol)

    # 4. Variants in this gene
    variants = _fetch_gene_variants(gene_symbol, sample_engine)

    # 5. Population AF data for chart
    population_af = _fetch_population_af(gene_symbol, sample_engine)

    return GeneDetailResponse(
        gene_symbol=gene_symbol,
        uniprot=uniprot_data,
        uniprot_error=uniprot_error,
        phenotypes=phenotypes,
        literature=literature,
        literature_errors=lit_errors,
        variants=variants,
        population_af=population_af,
    )


@router.get(
    "/{symbol}/variants",
    response_model=GeneVariantsResponse,
    dependencies=[Depends(require_fresh_sample)],
)
def get_gene_variants(
    symbol: str,
    sample_id: int = Query(..., description="Sample ID"),
) -> GeneVariantsResponse:
    """Return variants in a gene for a specific sample.

    Lighter-weight endpoint for fetching just the variant list
    without UniProt/literature data.

    Example: ``GET /api/genes/BRCA1/variants?sample_id=1``
    """
    gene_symbol = symbol.upper()
    sample_engine = _get_sample_engine(sample_id)
    variants = _fetch_gene_variants(gene_symbol, sample_engine)

    return GeneVariantsResponse(
        gene_symbol=gene_symbol,
        variants=variants,
        total=len(variants),
    )


# ── UniProt cache management endpoints (P4-12c) ─────────────────────


class UniProtRefreshResponse(BaseModel):
    """Response from a forced UniProt cache refresh."""

    gene_symbol: str
    success: bool
    accession: str | None = None
    domains_count: int = 0
    features_count: int = 0
    message: str = ""


class UniProtCacheStatsResponse(BaseModel):
    """UniProt cache statistics for admin panel."""

    total_entries: int = 0
    fresh_entries: int = 0
    stale_entries: int = 0
    oldest_entry: str | None = None
    newest_entry: str | None = None
    genes_cached: list[str] = []


def _get_fetcher() -> UniProtCacheFetcher:
    """Create a UniProtCacheFetcher using the reference engine."""
    registry = get_registry()
    return UniProtCacheFetcher(registry.reference_engine, ttl_days=_UNIPROT_TTL_DAYS)


# Per-gene cooldown to prevent excessive API calls (gene_symbol → last refresh time)
_refresh_cooldowns: dict[str, datetime] = {}
_REFRESH_COOLDOWN_SECONDS = 60


@router.post("/{symbol}/refresh-uniprot", response_model=UniProtRefreshResponse)
def refresh_uniprot_cache(symbol: str) -> UniProtRefreshResponse:
    """Force refresh UniProt protein data for a gene.

    Bypasses the cache and fetches directly from the UniProt REST API.
    Useful when the user wants to ensure the latest protein data is
    available without waiting for TTL expiry. Subject to a 60-second
    per-gene cooldown to prevent excessive API calls.

    Example: ``POST /api/genes/BRCA1/refresh-uniprot``
    """
    gene_symbol = symbol.upper()

    # Cooldown check
    last_refresh = _refresh_cooldowns.get(gene_symbol)
    if last_refresh is not None:
        elapsed = (datetime.now(UTC) - last_refresh).total_seconds()
        if elapsed < _REFRESH_COOLDOWN_SECONDS:
            remaining = int(_REFRESH_COOLDOWN_SECONDS - elapsed)
            return UniProtRefreshResponse(
                gene_symbol=gene_symbol,
                success=False,
                message=f"Cooldown active. Try again in {remaining}s.",
            )

    fetcher = _get_fetcher()
    result = fetcher.refresh(gene_symbol)

    if result is None:
        return UniProtRefreshResponse(
            gene_symbol=gene_symbol,
            success=False,
            message="Failed to fetch from UniProt API. Check network connectivity.",
        )

    # Record cooldown timestamp
    _refresh_cooldowns[gene_symbol] = datetime.now(UTC)

    return UniProtRefreshResponse(
        gene_symbol=gene_symbol,
        success=True,
        accession=result.accession,
        domains_count=len(result.domains),
        features_count=len(result.features),
        message=f"Refreshed {gene_symbol} ({result.accession}): "
        f"{len(result.domains)} domains, {len(result.features)} features.",
    )


# Separate router for cache stats (no gene symbol prefix)
cache_router = APIRouter(prefix="/uniprot-cache", tags=["gene-detail"])


@cache_router.get("/stats", response_model=UniProtCacheStatsResponse)
def get_uniprot_cache_stats() -> UniProtCacheStatsResponse:
    """Return UniProt cache statistics.

    Used by the admin panel (Settings > System Health) to display
    cache usage and freshness.

    Example: ``GET /api/uniprot-cache/stats``
    """
    fetcher = _get_fetcher()
    stats = fetcher.get_cache_stats()

    return UniProtCacheStatsResponse(
        total_entries=stats.total_entries,
        fresh_entries=stats.fresh_entries,
        stale_entries=stats.stale_entries,
        oldest_entry=stats.oldest_entry,
        newest_entry=stats.newest_entry,
        genes_cached=stats.genes_cached,
    )
