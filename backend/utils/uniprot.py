"""Cache-first UniProt protein domain fetcher (P4-12c).

Implements:
  - 30-day TTL cache in reference.db ``uniprot_cache`` table
  - On-demand fetch from UniProt REST API for cache misses
  - Graceful offline fallback (returns stale cache, never crashes)
  - Background pre-fetch for cancer/cardio gene panels
  - Batch pre-fetch for arbitrary gene lists

Usage::

    from backend.utils.uniprot import UniProtCacheFetcher

    fetcher = UniProtCacheFetcher(reference_engine)
    data = fetcher.get("BRCA1")          # cache-first, auto-refresh
    fetcher.refresh("BRCA1")             # force refresh from API
    stats = fetcher.get_cache_stats()    # cache statistics
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import sqlalchemy as sa
import structlog

from backend.db.tables import uniprot_cache

logger = structlog.get_logger(__name__)

# Type alias for progress callback: (done, total) -> None
_ProgressCallback = Callable[[int, int], None]

# Default TTL for UniProt cache entries
DEFAULT_TTL_DAYS = 30

# UniProt REST API base URL
_UNIPROT_API_BASE = "https://rest.uniprot.org/uniprotkb"

# Rate limit: delay between consecutive API requests (seconds)
_API_DELAY_SECONDS = 0.5

# Domain feature types extracted from UniProt
_DOMAIN_TYPES = frozenset({"Domain", "Region", "Repeat", "Zinc finger", "Motif"})

# Non-domain feature types extracted from UniProt
_FEATURE_TYPES = frozenset(
    {
        "Active site",
        "Binding site",
        "Site",
        "Disulfide bond",
        "Modified residue",
        "Glycosylation",
        "Lipidation",
    }
)


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class ProteinDomainData:
    """A single protein domain from UniProt."""

    type: str
    description: str
    start: int
    end: int


@dataclass
class ProteinFeatureData:
    """A protein feature annotation from UniProt."""

    type: str
    description: str
    position: int | None = None
    start: int | None = None
    end: int | None = None


@dataclass
class UniProtResult:
    """Result of a UniProt lookup."""

    accession: str
    gene_symbol: str
    sequence_length: int
    domains: list[ProteinDomainData] = field(default_factory=list)
    features: list[ProteinFeatureData] = field(default_factory=list)
    fetched_at: str | None = None
    is_cached: bool = False
    is_stale: bool = False


@dataclass
class CacheStats:
    """UniProt cache statistics."""

    total_entries: int = 0
    fresh_entries: int = 0
    stale_entries: int = 0
    oldest_entry: str | None = None
    newest_entry: str | None = None
    genes_cached: list[str] = field(default_factory=list)


@dataclass
class PrefetchResult:
    """Result of a batch pre-fetch operation."""

    total_genes: int = 0
    fetched: int = 0
    cached_already: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


# ── Gene panel constants ─────────────────────────────────────────────

# Cancer panel genes (from cancer_panel.json)
CANCER_PANEL_GENES: list[str] = [
    "BRCA1",
    "BRCA2",
    "TP53",
    "PALB2",
    "ATM",
    "CHEK2",
    "RAD51C",
    "RAD51D",
    "MLH1",
    "MSH2",
    "MSH6",
    "PMS2",
    "APC",
    "MUTYH",
    "VHL",
    "RET",
    "PTEN",
    "STK11",
    "CDH1",
    "NF1",
    "NF2",
    "MEN1",
    "SDHA",
    "SDHB",
    "SDHC",
    "SDHD",
    "BAP1",
    "CDKN2A",
]

# Cardiovascular panel genes (from cardiovascular_panel.json)
CARDIO_PANEL_GENES: list[str] = [
    "LDLR",
    "PCSK9",
    "APOB",
    "LPA",
    "ABCG5",
    "ABCG8",
    "KCNQ1",
    "SCN5A",
    "MYBPC3",
    "MYH7",
    "TNNT2",
    "LMNA",
    "DSP",
    "PKP2",
    "KCNH2",
    "RYR2",
]

# Priority genes pre-fetched at setup time
PRIORITY_GENES: list[str] = CANCER_PANEL_GENES + CARDIO_PANEL_GENES


# ── UniProt fetcher ──────────────────────────────────────────────────


class UniProtCacheFetcher:
    """Cache-first UniProt protein domain fetcher.

    Manages a local SQLite cache of UniProt protein data with
    configurable TTL. Fetches from the UniProt REST API on cache
    miss, with graceful offline fallback.

    Args:
        reference_engine: SQLAlchemy engine for reference.db.
        ttl_days: Cache time-to-live in days (default: 30).
    """

    def __init__(
        self,
        reference_engine: sa.Engine,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> None:
        self._engine = reference_engine
        self._ttl_days = ttl_days

    # ── Public API ───────────────────────────────────────────────────

    def get(self, gene_symbol: str) -> UniProtResult | None:
        """Get protein data for a gene (cache-first).

        1. Check cache for a fresh entry (within TTL).
        2. If stale/missing: fetch from UniProt REST API.
        3. If API fails: return stale cache (offline fallback).
        4. If no cache at all: return None.

        Returns:
            UniProtResult or None if unavailable.
        """
        # Try fresh cache
        result = self._get_from_cache(gene_symbol)
        if result is not None:
            return result

        # Cache miss or stale — fetch from API
        result = self._fetch_from_api(gene_symbol)
        if result is not None:
            return result

        # API failed — try stale fallback
        return self._get_stale_fallback(gene_symbol)

    def refresh(self, gene_symbol: str) -> UniProtResult | None:
        """Force refresh from UniProt API, ignoring cache.

        Returns:
            UniProtResult or None if API fetch failed.
        """
        return self._fetch_from_api(gene_symbol)

    def get_cache_stats(self) -> CacheStats:
        """Return statistics about the UniProt cache."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=self._ttl_days)

        with self._engine.connect() as conn:
            # Total entries
            total = (
                conn.execute(sa.select(sa.func.count()).select_from(uniprot_cache)).scalar() or 0
            )

            # Fresh entries (within TTL)
            fresh = (
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(uniprot_cache)
                    .where(uniprot_cache.c.fetched_at >= cutoff)
                ).scalar()
                or 0
            )

            # Oldest and newest
            oldest = conn.execute(sa.select(sa.func.min(uniprot_cache.c.fetched_at))).scalar()
            newest = conn.execute(sa.select(sa.func.max(uniprot_cache.c.fetched_at))).scalar()

            # All gene symbols
            rows = conn.execute(
                sa.select(uniprot_cache.c.gene_symbol).order_by(uniprot_cache.c.gene_symbol)
            ).fetchall()
            genes = [r.gene_symbol for r in rows if r.gene_symbol]

        return CacheStats(
            total_entries=total,
            fresh_entries=fresh,
            stale_entries=total - fresh,
            oldest_entry=str(oldest) if oldest else None,
            newest_entry=str(newest) if newest else None,
            genes_cached=genes,
        )

    def prefetch_genes(
        self,
        gene_symbols: list[str],
        *,
        skip_fresh: bool = True,
        delay_seconds: float = _API_DELAY_SECONDS,
        progress_callback: _ProgressCallback | None = None,
    ) -> PrefetchResult:
        """Pre-fetch UniProt data for a list of genes.

        Args:
            gene_symbols: Gene symbols to pre-fetch.
            skip_fresh: Skip genes already in fresh cache.
            delay_seconds: Delay between API calls for rate limiting.
            progress_callback: Optional ``(done, total) -> None`` called
                after each gene is processed.

        Returns:
            PrefetchResult with counts and any errors.
        """
        result = PrefetchResult(total_genes=len(gene_symbols))
        total = len(gene_symbols)

        for idx, gene in enumerate(gene_symbols, 1):
            # Check if already cached and fresh
            if skip_fresh:
                cached = self._get_from_cache(gene)
                if cached is not None:
                    result.cached_already += 1
                    if progress_callback is not None:
                        progress_callback(idx, total)
                    continue

            # Fetch from API
            fetched = self._fetch_from_api(gene)
            if fetched is not None:
                result.fetched += 1
            else:
                result.failed += 1
                result.errors.append(f"Failed to fetch {gene}")

            if progress_callback is not None:
                progress_callback(idx, total)

            # Rate limiting
            if delay_seconds > 0:
                time.sleep(delay_seconds)

        logger.info(
            "uniprot_prefetch_complete",
            total=result.total_genes,
            fetched=result.fetched,
            cached=result.cached_already,
            failed=result.failed,
        )
        return result

    # ── Internal methods ─────────────────────────────────────────────

    def _get_from_cache(self, gene_symbol: str) -> UniProtResult | None:
        """Get a fresh (non-expired) cache entry."""
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(uniprot_cache).where(uniprot_cache.c.gene_symbol == gene_symbol)
            ).fetchone()

        if row is None:
            return None

        # Check TTL
        fetched_at = row.fetched_at
        ttl = row.ttl_days or self._ttl_days
        if fetched_at is not None:
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            cutoff = datetime.now(UTC) - timedelta(days=ttl)
            if fetched_at < cutoff:
                return None  # Stale

        return self._parse_row(row, gene_symbol, is_cached=True)

    def _get_stale_fallback(self, gene_symbol: str) -> UniProtResult | None:
        """Return a stale cache entry for offline fallback."""
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(uniprot_cache).where(uniprot_cache.c.gene_symbol == gene_symbol)
            ).fetchone()

        if row is None:
            return None

        result = self._parse_row(row, gene_symbol, is_cached=True)
        result.is_stale = True
        return result

    def _fetch_from_api(self, gene_symbol: str) -> UniProtResult | None:
        """Fetch from UniProt REST API and store in cache."""
        import httpx

        try:
            # URL-encode the user-supplied symbol so it cannot inject extra
            # query params or alter the request (host is a fixed constant —
            # addresses the CodeQL py/partial-ssrf finding).
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

            domains, features = self._extract_features(entry)

            # Store in cache
            self._store_cache(
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

            return UniProtResult(
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

    def _extract_features(
        self, entry: dict
    ) -> tuple[list[ProteinDomainData], list[ProteinFeatureData]]:
        """Extract domain and feature annotations from a UniProt API entry."""
        domains: list[ProteinDomainData] = []
        features: list[ProteinFeatureData] = []

        for feat in entry.get("features", []):
            feat_type = feat.get("type", "")
            desc = feat.get("description", "")
            loc = feat.get("location", {})
            start_val = loc.get("start", {}).get("value")
            end_val = loc.get("end", {}).get("value")

            if feat_type in _DOMAIN_TYPES:
                if start_val is not None and end_val is not None:
                    domains.append(
                        ProteinDomainData(
                            type=feat_type,
                            description=desc,
                            start=start_val,
                            end=end_val,
                        )
                    )
            elif feat_type in _FEATURE_TYPES:
                features.append(
                    ProteinFeatureData(
                        type=feat_type,
                        description=desc,
                        position=start_val,
                        start=start_val,
                        end=end_val,
                    )
                )

        return domains, features

    def _store_cache(
        self,
        *,
        accession: str,
        gene_symbol: str,
        domains: list[ProteinDomainData],
        features: list[ProteinFeatureData],
        sequence_length: int,
    ) -> None:
        """Insert or update a UniProt cache entry."""
        domains_json = json.dumps(
            [
                {"type": d.type, "description": d.description, "start": d.start, "end": d.end}
                for d in domains
            ]
        )
        features_json = json.dumps(
            [
                {
                    "type": f.type,
                    "description": f.description,
                    "position": f.position,
                    "start": f.start,
                    "end": f.end,
                }
                for f in features
            ]
        )
        now = datetime.now(UTC)

        with self._engine.begin() as conn:
            existing = conn.execute(
                sa.select(uniprot_cache.c.accession).where(
                    uniprot_cache.c.gene_symbol == gene_symbol
                )
            ).fetchone()

            if existing:
                conn.execute(
                    uniprot_cache.update()
                    .where(uniprot_cache.c.gene_symbol == gene_symbol)
                    .values(
                        accession=accession,
                        gene_symbol=gene_symbol,
                        domains=domains_json,
                        features=features_json,
                        sequence_length=sequence_length,
                        fetched_at=now,
                        ttl_days=self._ttl_days,
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
                        ttl_days=self._ttl_days,
                    )
                )

    def _parse_row(
        self, row: sa.Row, gene_symbol: str, *, is_cached: bool = True
    ) -> UniProtResult:
        """Parse a DB row into a UniProtResult."""
        domains: list[ProteinDomainData] = []
        if row.domains:
            try:
                for d in json.loads(row.domains):
                    domains.append(ProteinDomainData(**d))
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning(
                    "uniprot_cache_domains_parse_error",
                    gene=gene_symbol,
                    error=str(exc),
                )

        features: list[ProteinFeatureData] = []
        if row.features:
            try:
                for f in json.loads(row.features):
                    features.append(ProteinFeatureData(**f))
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning(
                    "uniprot_cache_features_parse_error",
                    gene=gene_symbol,
                    error=str(exc),
                )

        fetched_at = row.fetched_at
        return UniProtResult(
            accession=row.accession,
            gene_symbol=gene_symbol,
            sequence_length=row.sequence_length or 0,
            domains=domains,
            features=features,
            fetched_at=str(fetched_at) if fetched_at else None,
            is_cached=is_cached,
        )
