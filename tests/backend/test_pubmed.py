"""Tests for PubMed abstract fetcher (P3-10).

Covers:
  T3-08: PubMed fetcher returns abstracts, stores in literature_cache,
         returns cached version on second call.
  T3-09: PubMed fetcher degrades gracefully when offline (returns cached
         with staleness indicator, no crash).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from backend.db.tables import literature_cache, reference_metadata
from backend.utils.pubmed import (
    DEFAULT_TTL_DAYS,
    PubMedArticle,
    PubMedFetcher,
    _parse_entrez_record,
    _row_to_article,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory SQLite engine with reference tables."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    return engine


@pytest.fixture
def fetcher(reference_engine: sa.Engine) -> PubMedFetcher:
    """PubMedFetcher with test email."""
    return PubMedFetcher(
        reference_engine=reference_engine,
        email="test@example.com",
        api_key="",
        ttl_days=DEFAULT_TTL_DAYS,
    )


@pytest.fixture
def fetcher_no_email(reference_engine: sa.Engine) -> PubMedFetcher:
    """PubMedFetcher with no email configured."""
    return PubMedFetcher(
        reference_engine=reference_engine,
        email="",
        api_key="",
    )


def _seed_cache(engine: sa.Engine, entries: list[dict]) -> None:
    """Insert entries into literature_cache."""
    with engine.begin() as conn:
        for entry in entries:
            conn.execute(literature_cache.insert().values(**entry))


def _make_entrez_record(
    pmid: str = "12345678",
    title: str = "Test Article",
    abstract: str = "This is a test abstract.",
    authors: list[dict] | None = None,
    journal: str = "Test Journal",
    year: str = "2024",
) -> dict:
    """Build a mock PubmedArticle record matching Entrez.read() output."""
    if authors is None:
        authors = [
            {"LastName": "Smith", "Initials": "J"},
            {"LastName": "Doe", "Initials": "A"},
        ]
    return {
        "MedlineCitation": {
            "PMID": pmid,
            "Article": {
                "ArticleTitle": title,
                "Abstract": {"AbstractText": [abstract]},
                "AuthorList": authors,
                "Journal": {
                    "Title": journal,
                    "JournalIssue": {
                        "PubDate": {"Year": year},
                    },
                },
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# T3-08: Fetch, cache, and return cached on second call
# ═══════════════════════════════════════════════════════════════════════


class TestFetchAndCache:
    """T3-08: PubMed fetcher stores in cache, returns cached on repeat."""

    def test_fetch_stores_in_cache(
        self, fetcher: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """First call fetches from Entrez, stores in literature_cache."""
        mock_records = {
            "PubmedArticle": [
                _make_entrez_record(
                    pmid="11111111",
                    title="BRCA1 mutations",
                    abstract="Study of BRCA1.",
                    journal="Nature Genetics",
                    year="2023",
                ),
            ]
        }

        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_handle = MagicMock()
            mock_entrez.efetch.return_value = mock_handle
            mock_entrez.read.return_value = mock_records

            result = fetcher.fetch_by_pmids(["11111111"], gene="BRCA1")

        mock_entrez.read.assert_called_once_with(mock_handle, escape=True)
        assert len(result.articles) == 1
        assert result.from_network == 1
        assert result.articles[0].pmid == "11111111"
        assert result.articles[0].title == "BRCA1 mutations"
        assert result.articles[0].journal == "Nature Genetics"
        assert result.articles[0].year == 2023
        assert "Smith J" in result.articles[0].authors

        # Verify stored in DB
        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(literature_cache).where(literature_cache.c.pmid == "11111111")
            ).fetchall()
        assert len(rows) == 1
        assert rows[0].gene == "BRCA1"
        assert rows[0].title == "BRCA1 mutations"

    def test_second_call_returns_cached(
        self, fetcher: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """Second call returns from cache without hitting Entrez."""
        # Pre-seed the cache with a fresh entry
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "22222222",
                    "gene": "MTHFR",
                    "title": "MTHFR and folate",
                    "abstract": "Study of MTHFR.",
                    "authors": json.dumps(["Author A"]),
                    "journal": "J Nutrition",
                    "year": 2024,
                    "fetched_at": datetime.now(UTC),
                }
            ],
        )

        # This should NOT call Entrez at all
        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            result = fetcher.fetch_by_pmids(["22222222"])

        mock_entrez.efetch.assert_not_called()
        assert len(result.articles) == 1
        assert result.from_cache == 1
        assert result.from_network == 0
        assert result.articles[0].pmid == "22222222"
        assert result.articles[0].title == "MTHFR and folate"

    def test_mixed_cached_and_new(
        self, fetcher: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """Mix of cached and new PMIDs: only new ones fetched."""
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "33333333",
                    "gene": None,
                    "title": "Cached article",
                    "abstract": "Already cached.",
                    "authors": json.dumps(["Author B"]),
                    "journal": "J Cached",
                    "year": 2023,
                    "fetched_at": datetime.now(UTC),
                }
            ],
        )

        mock_records = {
            "PubmedArticle": [
                _make_entrez_record(pmid="44444444", title="New article"),
            ]
        }

        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_handle = MagicMock()
            mock_entrez.efetch.return_value = mock_handle
            mock_entrez.read.return_value = mock_records

            result = fetcher.fetch_by_pmids(["33333333", "44444444"])

        assert result.from_cache == 1
        assert result.from_network == 1
        assert len(result.articles) == 2
        pmids = {a.pmid for a in result.articles}
        assert pmids == {"33333333", "44444444"}

    def test_empty_pmid_list(self, fetcher: PubMedFetcher) -> None:
        """Empty PMID list returns empty result."""
        result = fetcher.fetch_by_pmids([])
        assert len(result.articles) == 0
        assert result.from_cache == 0
        assert result.from_network == 0

    def test_duplicate_pmids_deduplicated(self, fetcher: PubMedFetcher) -> None:
        """Duplicate PMIDs are deduplicated before fetching."""
        mock_records = {
            "PubmedArticle": [
                _make_entrez_record(pmid="55555555"),
            ]
        }

        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_handle = MagicMock()
            mock_entrez.efetch.return_value = mock_handle
            mock_entrez.read.return_value = mock_records

            result = fetcher.fetch_by_pmids(["55555555", "55555555", "55555555"])

        # Should only fetch once, not three times
        assert result.from_network == 1
        assert len(result.articles) == 1

    def test_stale_cache_triggers_refetch(
        self, fetcher: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """Stale cached entries trigger a re-fetch from Entrez."""
        stale_time = datetime.now(UTC) - timedelta(days=DEFAULT_TTL_DAYS + 1)
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "66666666",
                    "gene": None,
                    "title": "Old title",
                    "abstract": "Old abstract.",
                    "authors": json.dumps(["Old Author"]),
                    "journal": "Old Journal",
                    "year": 2020,
                    "fetched_at": stale_time,
                }
            ],
        )

        mock_records = {
            "PubmedArticle": [
                _make_entrez_record(
                    pmid="66666666",
                    title="Updated title",
                    abstract="Updated abstract.",
                ),
            ]
        }

        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_handle = MagicMock()
            mock_entrez.efetch.return_value = mock_handle
            mock_entrez.read.return_value = mock_records

            result = fetcher.fetch_by_pmids(["66666666"])

        assert result.from_network == 1
        assert result.articles[0].title == "Updated title"


# ═══════════════════════════════════════════════════════════════════════
# T3-09: Graceful offline fallback
# ═══════════════════════════════════════════════════════════════════════


class TestOfflineFallback:
    """T3-09: Graceful degradation when offline."""

    def test_network_failure_returns_stale_cache(
        self, fetcher: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """Network failure returns stale cache with is_stale=True."""
        stale_time = datetime.now(UTC) - timedelta(days=DEFAULT_TTL_DAYS + 1)
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "77777777",
                    "gene": "TP53",
                    "title": "TP53 study",
                    "abstract": "Stale abstract.",
                    "authors": json.dumps(["Stale Author"]),
                    "journal": "Stale Journal",
                    "year": 2019,
                    "fetched_at": stale_time,
                }
            ],
        )

        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_entrez.efetch.side_effect = Exception("Network error")

            result = fetcher.fetch_by_pmids(["77777777"], gene="TP53")

        assert len(result.articles) == 1
        assert result.articles[0].pmid == "77777777"
        assert result.articles[0].is_stale is True
        assert len(result.errors) > 0

    def test_network_failure_no_cache_returns_empty(self, fetcher: PubMedFetcher) -> None:
        """Network failure with no cache returns empty with error."""
        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_entrez.efetch.side_effect = Exception("Network error")

            result = fetcher.fetch_by_pmids(["99999999"])

        assert len(result.articles) == 0
        assert len(result.errors) > 0

    def test_no_email_returns_cached_only(
        self, fetcher_no_email: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """No email configured returns cached data with warning."""
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "88888888",
                    "gene": None,
                    "title": "Cached only",
                    "abstract": "Abstract.",
                    "authors": json.dumps([]),
                    "journal": "J Test",
                    "year": 2024,
                    "fetched_at": datetime.now(UTC) - timedelta(days=DEFAULT_TTL_DAYS + 1),
                }
            ],
        )

        result = fetcher_no_email.fetch_by_pmids(["88888888"])
        assert len(result.errors) > 0
        assert "email not configured" in result.errors[0].lower()
        # Should still return the stale cached entry
        assert len(result.articles) == 1
        assert result.articles[0].is_stale is True

    def test_no_crash_on_complete_failure(self, fetcher: PubMedFetcher) -> None:
        """Complete failure (no cache, no network) does not crash."""
        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_entrez.efetch.side_effect = ConnectionError("DNS failure")

            result = fetcher.fetch_by_pmids(["00000001"])

        assert isinstance(result.articles, list)
        assert isinstance(result.errors, list)


# ═══════════════════════════════════════════════════════════════════════
# search_by_gene tests
# ═══════════════════════════════════════════════════════════════════════


class TestSearchByGene:
    """Tests for gene-based PubMed search."""

    def test_search_by_gene_success(
        self, fetcher: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """Successful gene search returns articles."""
        mock_search_result = {"IdList": ["11111111", "22222222"]}
        mock_fetch_records = {
            "PubmedArticle": [
                _make_entrez_record(pmid="11111111", title="BRCA1 paper 1"),
                _make_entrez_record(pmid="22222222", title="BRCA1 paper 2"),
            ]
        }

        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_search_handle = MagicMock()
            mock_fetch_handle = MagicMock()

            # esearch returns search results, efetch returns articles
            mock_entrez.esearch.return_value = mock_search_handle
            mock_entrez.efetch.return_value = mock_fetch_handle

            # read() is called for both esearch and efetch results
            mock_entrez.read.side_effect = [mock_search_result, mock_fetch_records]

            result = fetcher.search_by_gene("BRCA1", max_results=5)

        assert len(result.articles) == 2
        assert result.from_network == 2

    def test_search_by_gene_offline_fallback(
        self, fetcher: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """Gene search offline fallback returns cached gene entries."""
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "99999999",
                    "gene": "APOE",
                    "title": "APOE cached",
                    "abstract": "Cached.",
                    "authors": json.dumps([]),
                    "journal": "J Cache",
                    "year": 2022,
                    "fetched_at": datetime.now(UTC),
                }
            ],
        )

        with patch("backend.utils.pubmed.Entrez", autospec=True) as mock_entrez:
            mock_entrez.esearch.side_effect = Exception("Network error")

            result = fetcher.search_by_gene("APOE")

        assert len(result.articles) == 1
        assert result.articles[0].gene == "APOE"
        assert result.articles[0].is_stale is True

    def test_search_no_email(
        self, fetcher_no_email: PubMedFetcher, reference_engine: sa.Engine
    ) -> None:
        """Gene search with no email returns cached only."""
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "12121212",
                    "gene": "MTHFR",
                    "title": "MTHFR cached",
                    "abstract": "Cached.",
                    "authors": json.dumps([]),
                    "journal": "J Cache",
                    "year": 2023,
                    "fetched_at": datetime.now(UTC),
                }
            ],
        )

        result = fetcher_no_email.search_by_gene("MTHFR")
        assert len(result.articles) == 1
        assert len(result.errors) > 0


# ═══════════════════════════════════════════════════════════════════════
# Parser / helper tests
# ═══════════════════════════════════════════════════════════════════════


class TestParseEntrezRecord:
    """Unit tests for _parse_entrez_record helper."""

    def test_parse_full_record(self) -> None:
        """Parse a complete Entrez record."""
        record = _make_entrez_record(
            pmid="12345678",
            title="Test Title",
            abstract="Test abstract text.",
            authors=[
                {"LastName": "Smith", "Initials": "AB"},
                {"LastName": "Jones", "Initials": "C"},
            ],
            journal="Nature",
            year="2024",
        )
        article = _parse_entrez_record(record, gene="BRCA1")
        assert article is not None
        assert article.pmid == "12345678"
        assert article.title == "Test Title"
        assert article.abstract == "Test abstract text."
        assert article.authors == ["Smith AB", "Jones C"]
        assert article.journal == "Nature"
        assert article.year == 2024
        assert article.gene == "BRCA1"

    def test_parse_missing_pmid(self) -> None:
        """Record without PMID returns None."""
        record = {"MedlineCitation": {"PMID": "", "Article": {}}}
        assert _parse_entrez_record(record) is None

    def test_parse_missing_abstract(self) -> None:
        """Record without abstract returns empty string."""
        record = _make_entrez_record(pmid="11111111")
        record["MedlineCitation"]["Article"].pop("Abstract", None)
        article = _parse_entrez_record(record)
        assert article is not None
        assert article.abstract == ""

    def test_parse_missing_year(self) -> None:
        """Record without year returns None for year."""
        record = _make_entrez_record(pmid="22222222")
        record["MedlineCitation"]["Article"]["Journal"]["JournalIssue"]["PubDate"] = {}
        article = _parse_entrez_record(record)
        assert article is not None
        assert article.year is None

    def test_parse_multi_part_abstract(self) -> None:
        """Multi-part abstract is joined with spaces."""
        record = _make_entrez_record(pmid="33333333")
        record["MedlineCitation"]["Article"]["Abstract"]["AbstractText"] = [
            "BACKGROUND: Part 1.",
            "METHODS: Part 2.",
            "RESULTS: Part 3.",
        ]
        article = _parse_entrez_record(record)
        assert article is not None
        assert "Part 1." in article.abstract
        assert "Part 2." in article.abstract
        assert "Part 3." in article.abstract

    def test_parse_strips_pubmed_markup_from_title_and_abstract(self) -> None:
        """Third-party PubMed markup is reduced to safe readable text."""
        record = _make_entrez_record(
            pmid="36766853",
            title="The Role of <i>TP53</i> in Adaptation &amp; Evolution.",
            abstract="The <i>TP53</i> gene has <sup>many</sup> functions.",
        )

        article = _parse_entrez_record(record)

        assert article is not None
        assert article.title == "The Role of TP53 in Adaptation & Evolution."
        assert article.abstract == "The TP53 gene has many functions."
        assert "<" not in article.title
        assert "<" not in article.abstract

    def test_parse_preserves_escaped_literal_comparisons(self) -> None:
        """Entrez-escaped scientific comparisons survive markup removal."""
        record = _make_entrez_record(
            title="Expression x&lt;y with <i>TP53</i>",
            abstract="Dose &lt;5 mg and x&lt;y while <sup>2</sup> remains formatted.",
        )

        article = _parse_entrez_record(record)

        assert article is not None
        assert article.title == "Expression x<y with TP53"
        assert article.abstract == "Dose <5 mg and x<y while 2 remains formatted."


class TestRowToArticle:
    """Unit tests for _row_to_article helper."""

    def test_row_conversion(self, reference_engine: sa.Engine) -> None:
        """Convert a DB row to PubMedArticle."""
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "44444444",
                    "gene": "TP53",
                    "query": "TP53 cancer",
                    "title": "TP53 study",
                    "abstract": "Abstract text.",
                    "authors": json.dumps(["Smith J", "Doe A"]),
                    "journal": "Science",
                    "year": 2023,
                    "fetched_at": datetime(2024, 1, 1, tzinfo=UTC),
                }
            ],
        )

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(literature_cache).where(literature_cache.c.pmid == "44444444")
            ).first()

        article = _row_to_article(row)
        assert article.pmid == "44444444"
        assert article.gene == "TP53"
        assert article.title == "TP53 study"
        assert article.authors == ["Smith J", "Doe A"]
        assert article.journal == "Science"
        assert article.year == 2023

    def test_row_conversion_strips_markup_from_legacy_cache(
        self, reference_engine: sa.Engine
    ) -> None:
        """Existing cached PubMed markup is cleaned before it reaches consumers."""
        _seed_cache(
            reference_engine,
            [
                {
                    "pmid": "36766853",
                    "gene": "TP53",
                    "title": "The Role of <i>TP53</i> in Adaptation &amp; Evolution.",
                    "abstract": (
                        "The <i>TP53</i> gene has <sub>important</sub> functions when x<y."
                    ),
                    "authors": json.dumps(["Author A"]),
                    "journal": "J Test",
                    "year": 2023,
                    "fetched_at": datetime.now(UTC),
                }
            ],
        )

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(literature_cache).where(literature_cache.c.pmid == "36766853")
            ).first()

        article = _row_to_article(row)
        assert article.title == "The Role of TP53 in Adaptation & Evolution."
        assert article.abstract == "The TP53 gene has important functions when x<y."
        assert "<" not in article.title
        assert "<i>" not in article.abstract
        assert "<sub>" not in article.abstract


class TestArticleSerialization:
    """Tests for PubMedArticle.to_dict()."""

    def test_to_dict(self) -> None:
        """Serialize article to dictionary."""
        article = PubMedArticle(
            pmid="55555555",
            title="Test",
            abstract="Abstract.",
            authors=["Author A"],
            journal="J Test",
            year=2024,
            gene="BRCA1",
            is_stale=True,
        )
        d = article.to_dict()
        assert d["pmid"] == "55555555"
        assert d["is_stale"] is True
        assert d["gene"] == "BRCA1"
        assert d["authors"] == ["Author A"]
