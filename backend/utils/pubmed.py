"""Cache-first PubMed abstract fetcher using Biopython Entrez.

Implements P3-10: PubMed abstract fetcher with:
  - Biopython Entrez for NCBI PubMed API access
  - Cache-first architecture using the ``literature_cache`` table
  - 7-day staleness window (configurable)
  - Graceful offline fallback (returns stale cache, never crashes)

Usage::

    from backend.utils.pubmed import PubMedFetcher

    fetcher = PubMedFetcher(reference_engine, email="user@example.com")
    articles = fetcher.fetch_by_pmids(["12345678", "87654321"])
    articles = fetcher.search_by_gene("BRCA1", max_results=5)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import unescape

import sqlalchemy as sa
import structlog
from Bio import Entrez

from backend.db.tables import literature_cache

logger = structlog.get_logger(__name__)

# Default cache staleness window (days)
DEFAULT_TTL_DAYS = 7

# Maximum PMIDs per efetch request (NCBI recommendation)
_EFETCH_BATCH_SIZE = 200

_PUBMED_FORMATTING_TAG_RE = re.compile(
    r"</?(?:b|i|u|sup|sub|DispFormula|mml:[A-Za-z][\w.-]*)"
    r"(?:\s[^<>]*?)?\s*/?>",
    re.IGNORECASE,
)
_PUBMED_MATHML_RE = re.compile(
    r"<(?:mml:)?math\b[^<>]*(?:/>|>.*?</(?:mml:)?math\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_XML_TAG_RE = re.compile(
    r"</?[A-Za-z][\w:.-]*(?:\s[^<>]*?)?\s*/?>",
    re.IGNORECASE,
)


def _plain_pubmed_text(value: object) -> str:
    """Remove PubMed formatting tags while preserving literal comparisons."""
    without_mathml = _PUBMED_MATHML_RE.sub(
        lambda match: _XML_TAG_RE.sub("", match.group(0)),
        str(value),
    )
    without_tags = _PUBMED_FORMATTING_TAG_RE.sub("", without_mathml)
    return unescape(without_tags)


@dataclass
class PubMedArticle:
    """A single PubMed article with metadata."""

    pmid: str
    title: str
    abstract: str
    authors: list[str]
    journal: str
    year: int | None
    gene: str | None = None
    query: str | None = None
    fetched_at: datetime | None = None
    is_stale: bool = False

    def to_dict(self) -> dict:
        """Serialize to dictionary for API responses."""
        return {
            "pmid": self.pmid,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "journal": self.journal,
            "year": self.year,
            "gene": self.gene,
            "query": self.query,
            "is_stale": self.is_stale,
        }


@dataclass
class FetchResult:
    """Result of a PubMed fetch operation."""

    articles: list[PubMedArticle] = field(default_factory=list)
    from_cache: int = 0
    from_network: int = 0
    errors: list[str] = field(default_factory=list)


class PubMedFetcher:
    """Cache-first PubMed abstract fetcher.

    Checks the ``literature_cache`` table first; only calls NCBI Entrez
    for PMIDs not in cache or whose cache entries are older than
    ``ttl_days``.  If the network call fails, stale cached entries are
    returned with ``is_stale=True``.

    Args:
        reference_engine: SQLAlchemy engine for reference.db.
        email: Email address for NCBI Entrez (required by TOS).
        api_key: Optional NCBI API key for higher rate limits.
        ttl_days: Cache staleness window in days (default 7).
    """

    def __init__(
        self,
        reference_engine: sa.Engine,
        email: str,
        api_key: str = "",
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> None:
        self._engine = reference_engine
        self._email = email
        self._api_key = api_key
        self._ttl_days = ttl_days

    # ── Public API ───────────────────────────────────────────────────

    def fetch_by_pmids(
        self,
        pmids: list[str],
        gene: str | None = None,
    ) -> FetchResult:
        """Fetch PubMed articles by PMID list, cache-first.

        Args:
            pmids: List of PubMed IDs to fetch.
            gene: Optional gene symbol to associate with cached entries.

        Returns:
            FetchResult with articles, cache/network counts, and errors.
        """
        if not pmids:
            return FetchResult()

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_pmids: list[str] = []
        for pmid in pmids:
            pmid_str = str(pmid).strip()
            if pmid_str and pmid_str not in seen:
                seen.add(pmid_str)
                unique_pmids.append(pmid_str)

        if not unique_pmids:
            return FetchResult()

        result = FetchResult()
        cutoff = datetime.now(UTC) - timedelta(days=self._ttl_days)

        # Step 1: Check cache
        cached, missing_pmids, stale_pmids = self._check_cache(unique_pmids, cutoff)
        result.from_cache = len(cached)

        # If everything is fresh in cache, return immediately
        needs_fetch = missing_pmids + stale_pmids
        if not needs_fetch:
            result.articles = cached
            return result

        # Step 2: Fetch missing/stale from NCBI
        if not self._email:
            # No email configured — return cached (possibly stale) with warning
            logger.warning("pubmed_email_not_configured")
            result.errors.append("PubMed email not configured. Using cached data only.")
            # Mark stale entries
            stale_cached = self._get_stale_cached(stale_pmids)
            for article in stale_cached:
                article.is_stale = True
            result.articles = cached + stale_cached
            return result

        fetched = self._fetch_from_entrez(needs_fetch, gene)
        if fetched is not None:
            # Network success — cache new entries
            self._store_in_cache(fetched, gene)
            result.from_network = len(fetched)
            result.articles = cached + fetched
        else:
            # Network failure — fall back to stale cache
            logger.warning(
                "pubmed_network_fallback",
                stale_count=len(stale_pmids),
                missing_count=len(missing_pmids),
            )
            result.errors.append("PubMed network request failed. Showing cached data.")
            stale_cached = self._get_stale_cached(stale_pmids)
            for article in stale_cached:
                article.is_stale = True
            result.articles = cached + stale_cached

        return result

    def search_by_gene(
        self,
        gene_symbol: str,
        max_results: int = 5,
    ) -> FetchResult:
        """Search PubMed for articles about a gene and fetch abstracts.

        Uses Entrez esearch to find PMIDs, then fetches via
        :meth:`fetch_by_pmids`.

        Args:
            gene_symbol: Gene symbol to search for (e.g. "BRCA1").
            max_results: Maximum number of results to return.

        Returns:
            FetchResult with articles found.
        """
        if not self._email:
            logger.warning("pubmed_email_not_configured")
            # Try to return cached results for this gene
            cached_for_gene = self._get_cached_by_gene(gene_symbol)
            result = FetchResult(
                articles=cached_for_gene,
                from_cache=len(cached_for_gene),
                errors=["PubMed email not configured. Using cached data only."],
            )
            return result

        pmids = self._esearch_gene(gene_symbol, max_results)
        if pmids is None:
            # Network failure — return cached for this gene
            cached_for_gene = self._get_cached_by_gene(gene_symbol)
            for article in cached_for_gene:
                article.is_stale = True
            return FetchResult(
                articles=cached_for_gene,
                from_cache=len(cached_for_gene),
                errors=["PubMed search failed. Showing cached data."],
            )

        if not pmids:
            return FetchResult()

        return self.fetch_by_pmids(pmids, gene=gene_symbol)

    # ── Cache operations ─────────────────────────────────────────────

    def _check_cache(
        self,
        pmids: list[str],
        cutoff: datetime,
    ) -> tuple[list[PubMedArticle], list[str], list[str]]:
        """Check cache for PMIDs and partition into fresh/stale/missing.

        Returns:
            Tuple of (fresh_articles, missing_pmids, stale_pmids).
        """
        fresh_articles: list[PubMedArticle] = []
        stale_pmids: list[str] = []
        found_pmids: set[str] = set()

        with self._engine.connect() as conn:
            stmt = sa.select(literature_cache).where(literature_cache.c.pmid.in_(pmids))
            for row in conn.execute(stmt):
                found_pmids.add(row.pmid)
                fetched_at = row.fetched_at
                # Handle naive datetimes from SQLite
                if fetched_at is not None and fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=UTC)
                if fetched_at is not None and fetched_at >= cutoff:
                    fresh_articles.append(_row_to_article(row))
                else:
                    stale_pmids.append(row.pmid)

        missing_pmids = [p for p in pmids if p not in found_pmids]
        return fresh_articles, missing_pmids, stale_pmids

    def _get_stale_cached(
        self,
        pmids: list[str],
    ) -> list[PubMedArticle]:
        """Retrieve stale cached entries (for offline fallback)."""
        if not pmids:
            return []

        with self._engine.connect() as conn:
            stmt = sa.select(literature_cache).where(literature_cache.c.pmid.in_(pmids))
            return [_row_to_article(row) for row in conn.execute(stmt)]

    def _get_cached_by_gene(self, gene_symbol: str) -> list[PubMedArticle]:
        """Retrieve all cached entries for a gene symbol."""
        with self._engine.connect() as conn:
            stmt = sa.select(literature_cache).where(literature_cache.c.gene == gene_symbol)
            return [_row_to_article(row) for row in conn.execute(stmt)]

    def _store_in_cache(
        self,
        articles: list[PubMedArticle],
        gene: str | None = None,
    ) -> None:
        """Insert or update articles in the literature_cache table."""
        if not articles:
            return

        now = datetime.now(UTC)
        with self._engine.begin() as conn:
            for article in articles:
                article_gene = article.gene or gene
                # Try to update existing entry first
                existing = conn.execute(
                    sa.select(literature_cache.c.id).where(
                        sa.and_(
                            literature_cache.c.pmid == article.pmid,
                            literature_cache.c.gene == article_gene,
                        )
                    )
                ).first()

                if existing:
                    conn.execute(
                        literature_cache.update()
                        .where(literature_cache.c.id == existing.id)
                        .values(
                            title=article.title,
                            abstract=article.abstract,
                            authors=json.dumps(article.authors),
                            journal=article.journal,
                            year=article.year,
                            fetched_at=now,
                        )
                    )
                else:
                    conn.execute(
                        literature_cache.insert().values(
                            pmid=article.pmid,
                            gene=article_gene,
                            query=article.query,
                            title=article.title,
                            abstract=article.abstract,
                            authors=json.dumps(article.authors),
                            journal=article.journal,
                            year=article.year,
                            fetched_at=now,
                        )
                    )

        logger.info(
            "pubmed_cache_updated",
            articles_stored=len(articles),
            gene=gene,
        )

    # ── NCBI Entrez calls ────────────────────────────────────────────

    def _fetch_from_entrez(
        self,
        pmids: list[str],
        gene: str | None = None,
    ) -> list[PubMedArticle] | None:
        """Fetch articles from NCBI Entrez efetch.

        Returns:
            List of PubMedArticle on success, None on network failure.
        """
        try:
            Entrez.email = self._email
            if self._api_key:
                Entrez.api_key = self._api_key

            articles: list[PubMedArticle] = []

            # Batch fetches to respect NCBI limits
            for i in range(0, len(pmids), _EFETCH_BATCH_SIZE):
                batch = pmids[i : i + _EFETCH_BATCH_SIZE]
                handle = Entrez.efetch(
                    db="pubmed",
                    id=",".join(batch),
                    rettype="xml",
                    retmode="xml",
                )
                try:
                    records = Entrez.read(handle, escape=True)
                finally:
                    handle.close()

                for record in records.get("PubmedArticle", []):
                    article = _parse_entrez_record(record, gene)
                    if article:
                        articles.append(article)

            logger.info(
                "pubmed_entrez_fetch_success",
                requested=len(pmids),
                fetched=len(articles),
            )
            return articles

        except Exception:
            logger.exception("pubmed_entrez_fetch_failed")
            return None

    def _esearch_gene(
        self,
        gene_symbol: str,
        max_results: int,
    ) -> list[str] | None:
        """Search PubMed for a gene symbol and return PMIDs.

        Returns:
            List of PMID strings on success, None on network failure.
        """
        try:
            Entrez.email = self._email
            if self._api_key:
                Entrez.api_key = self._api_key

            # Search for gene in PubMed with genetic context
            term = f"{gene_symbol}[Gene Name] AND humans[MeSH Terms]"
            handle = Entrez.esearch(
                db="pubmed",
                term=term,
                retmax=str(max_results),
                sort="relevance",
            )
            try:
                record = Entrez.read(handle)
            finally:
                handle.close()

            pmids = record.get("IdList", [])
            logger.info(
                "pubmed_esearch_success",
                gene=gene_symbol,
                results=len(pmids),
            )
            return [str(p) for p in pmids]

        except Exception:
            logger.exception("pubmed_esearch_failed", gene=gene_symbol)
            return None


# ── Helper functions ─────────────────────────────────────────────────


def _row_to_article(row: sa.Row) -> PubMedArticle:
    """Convert a literature_cache row to a PubMedArticle."""
    authors_raw = row.authors
    if isinstance(authors_raw, str):
        try:
            authors = json.loads(authors_raw)
        except (json.JSONDecodeError, TypeError):
            authors = [authors_raw] if authors_raw else []
    else:
        authors = authors_raw or []

    return PubMedArticle(
        pmid=row.pmid,
        title=_plain_pubmed_text(row.title or ""),
        abstract=_plain_pubmed_text(row.abstract or ""),
        authors=[_plain_pubmed_text(author) for author in authors],
        journal=_plain_pubmed_text(row.journal or ""),
        year=row.year,
        gene=row.gene,
        query=row.query,
        fetched_at=row.fetched_at,
    )


def _parse_entrez_record(
    record: dict,
    gene: str | None = None,
) -> PubMedArticle | None:
    """Parse a single PubmedArticle XML record into a PubMedArticle.

    Returns None if the record lacks a PMID.
    """
    try:
        medline = record.get("MedlineCitation", {})
        pmid = str(medline.get("PMID", ""))
        if not pmid:
            return None

        article_data = medline.get("Article", {})

        # Title
        title = _plain_pubmed_text(article_data.get("ArticleTitle", ""))

        # Abstract
        abstract_parts = article_data.get("Abstract", {}).get("AbstractText", [])
        abstract = " ".join(_plain_pubmed_text(part) for part in abstract_parts)

        # Authors
        authors: list[str] = []
        author_list = article_data.get("AuthorList", [])
        for author in author_list:
            last = _plain_pubmed_text(author.get("LastName", ""))
            initials = _plain_pubmed_text(author.get("Initials", ""))
            if last:
                authors.append(f"{last} {initials}".strip())

        # Journal
        journal_info = article_data.get("Journal", {})
        journal = _plain_pubmed_text(journal_info.get("Title", ""))

        # Year
        year = None
        pub_date = article_data.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
        year_str = pub_date.get("Year", "")
        if year_str:
            try:
                year = int(year_str)
            except (ValueError, TypeError):
                pass

        return PubMedArticle(
            pmid=pmid,
            title=title,
            abstract=abstract,
            authors=authors,
            journal=journal,
            year=year,
            gene=gene,
        )

    except Exception:
        logger.exception("pubmed_record_parse_error")
        return None
