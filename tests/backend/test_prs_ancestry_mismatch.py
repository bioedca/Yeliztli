"""Tests for PRS ancestry mismatch warning integration (P3-16).

Covers:
  - T3-15: PRS ancestry mismatch warning fires when inferred ancestry
    ≠ weight set source population.
  - T3-16: PRS ancestry mismatch warning does NOT fire when ancestries match.
  - get_inferred_ancestry() lookups from sample DB findings.
  - End-to-end cancer /run endpoint with ancestry mismatch propagation.
  - Amber per-score flag (not static fine print) stored in detail_json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.ancestry import get_inferred_ancestry
from backend.analysis.cancer_prs import (
    load_cancer_prs_weights,
    store_cancer_prs_findings,
)
from backend.analysis.cancer_prs import (
    run_cancer_prs as _run_cancer_prs,
)
from backend.analysis.prs import (
    PRSResult,
    check_ancestry_mismatch,
)
from backend.db.tables import annotated_variants, findings

WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_prs_weights.json"
)


def run_cancer_prs(*args, **kwargs):
    """Test helper: keep ancestry-mismatch coverage in an explicit XY context."""
    kwargs.setdefault("inferred_sex", "XY")
    return _run_cancer_prs(*args, **kwargs)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def sample_with_ancestry_eur(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with an ancestry finding indicating EUR."""
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "ancestry",
                    "category": "population",
                    "evidence_level": 2,
                    "finding_text": "Inferred top ancestry: EUR",
                    "detail_json": json.dumps(
                        {
                            "top_population": "EUR",
                            "admixture_fractions": {
                                "EUR": 0.92,
                                "EAS": 0.03,
                                "AFR": 0.05,
                            },
                        }
                    ),
                }
            ],
        )
    return sample_engine


@pytest.fixture()
def sample_with_ancestry_afr(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with an ancestry finding indicating AFR."""
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "ancestry",
                    "category": "population",
                    "evidence_level": 2,
                    "finding_text": "Inferred top ancestry: AFR",
                    "detail_json": json.dumps(
                        {
                            "top_population": "AFR",
                            "admixture_fractions": {
                                "AFR": 0.88,
                                "EUR": 0.07,
                                "AMR": 0.05,
                            },
                        }
                    ),
                }
            ],
        )
    return sample_engine


@pytest.fixture()
def sample_no_ancestry(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with no ancestry findings (inference not yet run)."""
    return sample_engine


def _build_prs_variants() -> list[dict]:
    """Generate variant rows for all cancer PRS SNPs with rotating alleles."""
    weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
    all_rsids: set[str] = set()
    for ws in weight_sets:
        all_rsids.update(ws.rsid_set())

    variants = []
    for i, rsid in enumerate(sorted(all_rsids)):
        alleles = ["A", "C", "G", "T"]
        a1 = alleles[i % 4]
        a2 = alleles[(i + 1) % 4]
        variants.append(
            {
                "rsid": rsid,
                "chrom": str((i % 22) + 1),
                "pos": 100000 + i * 1000,
                "genotype": f"{a1}{a2}",
                "annotation_coverage": 0,
            }
        )
    return variants


@pytest.fixture()
def sample_with_prs_snps_and_ancestry_eur(
    sample_with_ancestry_eur: sa.Engine,
) -> sa.Engine:
    """Sample with both ancestry=EUR and PRS SNPs for all 4 cancer traits."""
    with sample_with_ancestry_eur.begin() as conn:
        conn.execute(sa.insert(annotated_variants), _build_prs_variants())
    return sample_with_ancestry_eur


@pytest.fixture()
def sample_with_prs_snps_and_ancestry_afr(
    sample_with_ancestry_afr: sa.Engine,
) -> sa.Engine:
    """Sample with ancestry=AFR and PRS SNPs for all 4 cancer traits."""
    with sample_with_ancestry_afr.begin() as conn:
        conn.execute(sa.insert(annotated_variants), _build_prs_variants())
    return sample_with_ancestry_afr


@pytest.fixture()
def sample_with_prs_snps_no_ancestry(sample_no_ancestry: sa.Engine) -> sa.Engine:
    """Sample with PRS SNPs but no ancestry inference run."""
    with sample_no_ancestry.begin() as conn:
        conn.execute(sa.insert(annotated_variants), _build_prs_variants())
    return sample_no_ancestry


# ── get_inferred_ancestry tests ──────────────────────────────────────────


class TestGetInferredAncestry:
    """Test retrieval of inferred ancestry from sample DB."""

    def test_returns_eur_when_present(self, sample_with_ancestry_eur: sa.Engine) -> None:
        result = get_inferred_ancestry(sample_with_ancestry_eur)
        assert result == "EUR"

    def test_returns_afr_when_present(self, sample_with_ancestry_afr: sa.Engine) -> None:
        result = get_inferred_ancestry(sample_with_ancestry_afr)
        assert result == "AFR"

    def test_returns_none_when_no_ancestry(self, sample_no_ancestry: sa.Engine) -> None:
        result = get_inferred_ancestry(sample_no_ancestry)
        assert result is None

    def test_returns_none_for_empty_detail_json(self, sample_engine: sa.Engine) -> None:
        """If detail_json is empty or missing, should return None."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "ancestry",
                        "category": "population",
                        "evidence_level": 2,
                        "finding_text": "Ancestry unknown",
                        "detail_json": None,
                    }
                ],
            )
        result = get_inferred_ancestry(sample_engine)
        assert result is None

    def test_returns_none_for_malformed_json(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "ancestry",
                        "category": "population",
                        "evidence_level": 2,
                        "finding_text": "Ancestry",
                        "detail_json": "not valid json{{{",
                    }
                ],
            )
        result = get_inferred_ancestry(sample_engine)
        assert result is None

    def test_uses_most_recent_finding(self, sample_engine: sa.Engine) -> None:
        """If multiple ancestry findings exist, use the most recent (highest id)."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "ancestry",
                        "category": "population",
                        "evidence_level": 2,
                        "finding_text": "First run",
                        "detail_json": json.dumps({"top_population": "EUR"}),
                    },
                    {
                        "module": "ancestry",
                        "category": "population",
                        "evidence_level": 2,
                        "finding_text": "Re-run",
                        "detail_json": json.dumps({"top_population": "SAS"}),
                    },
                ],
            )
        result = get_inferred_ancestry(sample_engine)
        assert result == "SAS"

    def test_accepts_inferred_ancestry_key(self, sample_engine: sa.Engine) -> None:
        """Also supports 'inferred_ancestry' key in detail_json."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "ancestry",
                        "category": "population",
                        "evidence_level": 2,
                        "finding_text": "Ancestry",
                        "detail_json": json.dumps({"inferred_ancestry": "AMR"}),
                    }
                ],
            )
        result = get_inferred_ancestry(sample_engine)
        assert result == "AMR"


# ── T3-15: Mismatch fires when different ─────────────────────────────────


class TestAncestryMismatchFires:
    """T3-15: PRS ancestry mismatch warning fires when inferred
    ancestry ≠ weight set source population."""

    def test_mismatch_per_score_flag(self) -> None:
        """Each PRS result gets its own ancestry_mismatch flag (not global)."""
        result = PRSResult(
            weight_set_name="Breast cancer (BCAC)",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="Mavaddat et al. 2019",
            source_pmid="30554720",
            sample_size=228951,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry="AFR")
        assert result.ancestry_mismatch is True
        assert result.ancestry_warning_text is not None
        assert "EUR" in result.ancestry_warning_text
        assert "AFR" in result.ancestry_warning_text

    def test_cancer_prs_all_scores_flagged_when_mismatch(
        self, sample_with_prs_snps_and_ancestry_afr: sa.Engine
    ) -> None:
        """All 4 cancer PRS scores get individual amber warnings for AFR user."""
        weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
        inferred = get_inferred_ancestry(sample_with_prs_snps_and_ancestry_afr)
        assert inferred == "AFR"

        result = run_cancer_prs(
            weight_sets,
            sample_with_prs_snps_and_ancestry_afr,
            inferred_ancestry=inferred,
            n_bootstrap=100,
            rng_seed=42,
        )

        for r in result.results:
            assert r.ancestry_mismatch is True, f"{r.trait} should have mismatch"
            assert r.ancestry_warning_text is not None
            assert "AFR" in r.ancestry_warning_text
            assert r.source_ancestry in r.ancestry_warning_text

    def test_mismatch_stored_in_findings_detail_json(
        self, sample_with_prs_snps_and_ancestry_afr: sa.Engine
    ) -> None:
        """ancestry_mismatch flag persists in detail_json — not just in memory."""
        weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
        inferred = get_inferred_ancestry(sample_with_prs_snps_and_ancestry_afr)
        prs_result = run_cancer_prs(
            weight_sets,
            sample_with_prs_snps_and_ancestry_afr,
            inferred_ancestry=inferred,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps_and_ancestry_afr)

        with sample_with_prs_snps_and_ancestry_afr.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        assert len(rows) > 0
        for row in rows:
            detail = json.loads(row.detail_json)
            assert detail["ancestry_mismatch"] is True
            assert detail["ancestry_warning_text"] is not None
            assert "AFR" in detail["ancestry_warning_text"]

    def test_mismatch_is_active_per_score_not_static(self) -> None:
        """Per PRD: 'not static fine print, but an active per-score flag.'
        Verify each score carries its own warning tied to the weight set."""
        results = []
        for trait, ancestry in [
            ("breast_cancer", "EUR"),
            ("prostate_cancer", "EUR"),
        ]:
            r = PRSResult(
                weight_set_name=f"{trait} PRS",
                trait=trait,
                module="cancer",
                source_ancestry=ancestry,
                source_study="Test",
                source_pmid="123",
                sample_size=1000,
                raw_score=0.5,
            )
            r = check_ancestry_mismatch(r, inferred_ancestry="EAS")
            results.append(r)

        # Each result has its own flag and text
        for r in results:
            assert r.ancestry_mismatch is True
            assert r.ancestry_warning_text is not None
            assert r.source_ancestry in r.ancestry_warning_text


# ── T3-16: No mismatch when matching ─────────────────────────────────────


class TestAncestryMismatchNoFire:
    """T3-16: PRS ancestry mismatch warning does NOT fire when
    ancestries match."""

    def test_no_mismatch_when_eur_matches_eur(self) -> None:
        result = PRSResult(
            weight_set_name="Breast cancer (BCAC)",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="Mavaddat et al. 2019",
            source_pmid="30554720",
            sample_size=228951,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry="EUR")
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_cancer_prs_no_flags_when_matching(
        self, sample_with_prs_snps_and_ancestry_eur: sa.Engine
    ) -> None:
        """All cancer weight sets are EUR — no mismatch for EUR user."""
        weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
        inferred = get_inferred_ancestry(sample_with_prs_snps_and_ancestry_eur)
        assert inferred == "EUR"

        result = run_cancer_prs(
            weight_sets,
            sample_with_prs_snps_and_ancestry_eur,
            inferred_ancestry=inferred,
            n_bootstrap=100,
            rng_seed=42,
        )

        for r in result.results:
            assert r.ancestry_mismatch is False, f"{r.trait} should not have mismatch"
            assert r.ancestry_warning_text is None

    def test_no_mismatch_stored_in_findings(
        self, sample_with_prs_snps_and_ancestry_eur: sa.Engine
    ) -> None:
        """Matching ancestry: detail_json shows ancestry_mismatch=false."""
        weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
        inferred = get_inferred_ancestry(sample_with_prs_snps_and_ancestry_eur)
        prs_result = run_cancer_prs(
            weight_sets,
            sample_with_prs_snps_and_ancestry_eur,
            inferred_ancestry=inferred,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps_and_ancestry_eur)

        with sample_with_prs_snps_and_ancestry_eur.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        assert len(rows) > 0
        for row in rows:
            detail = json.loads(row.detail_json)
            assert detail["ancestry_mismatch"] is False
            assert detail["ancestry_warning_text"] is None


# ── Informational warning when ancestry not run ──────────────────────────


class TestAncestryNotRun:
    """When ancestry inference hasn't been run, PRS should include
    an informational warning (not an error, not a mismatch)."""

    def test_informational_warning_per_score(
        self, sample_with_prs_snps_no_ancestry: sa.Engine
    ) -> None:
        weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
        inferred = get_inferred_ancestry(sample_with_prs_snps_no_ancestry)
        assert inferred is None

        result = run_cancer_prs(
            weight_sets,
            sample_with_prs_snps_no_ancestry,
            inferred_ancestry=inferred,
            n_bootstrap=100,
            rng_seed=42,
        )

        for r in result.results:
            assert r.ancestry_mismatch is False  # Not a mismatch, just informational
            assert r.ancestry_warning_text is not None
            assert "not been run" in r.ancestry_warning_text

    def test_informational_warning_stored_in_findings(
        self, sample_with_prs_snps_no_ancestry: sa.Engine
    ) -> None:
        weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
        inferred = get_inferred_ancestry(sample_with_prs_snps_no_ancestry)
        prs_result = run_cancer_prs(
            weight_sets,
            sample_with_prs_snps_no_ancestry,
            inferred_ancestry=inferred,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps_no_ancestry)

        with sample_with_prs_snps_no_ancestry.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        assert len(rows) > 0
        for row in rows:
            detail = json.loads(row.detail_json)
            assert detail["ancestry_mismatch"] is False
            assert "not been run" in detail["ancestry_warning_text"]


# ── Case-insensitive matching ─────────────────────────────────────────────


class TestAncestryMatchCaseInsensitive:
    """Ancestry comparison must be case-insensitive."""

    def test_lowercase_eur_matches(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry="eur")
        assert result.ancestry_mismatch is False

    def test_mixed_case_matches(self) -> None:
        result = PRSResult(
            weight_set_name="Test",
            trait="test",
            module="test",
            source_ancestry="EUR",
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )
        result = check_ancestry_mismatch(result, inferred_ancestry="Eur")
        assert result.ancestry_mismatch is False


# ── T-PRS-01 to T-PRS-04: AMv2 Step 8 tests ─────────────────────────────


class TestAdmixtureAwareThreshold:
    """AMv2 Step 8.3: Admixture-aware PRS accuracy warning."""

    def _make_result(self, source_ancestry: str = "EUR") -> PRSResult:
        return PRSResult(
            weight_set_name="Test PRS",
            trait="test",
            module="test",
            source_ancestry=source_ancestry,
            source_study="Test",
            source_pmid="123",
            sample_size=1000,
            raw_score=0.5,
        )

    def test_admixture_warning_fires_when_top_below_70(self) -> None:
        """T-PRS-03: Admixture-aware warning fires when top ancestry < 70%."""
        result = self._make_result()
        result = check_ancestry_mismatch(
            result, inferred_ancestry="EUR", top_ancestry_fraction=0.55
        )
        assert result.ancestry_mismatch is True
        assert result.ancestry_warning_text is not None
        assert "admixed" in result.ancestry_warning_text.lower()

    def test_no_admixture_warning_when_top_above_70(self) -> None:
        """No admixture warning when top ancestry >= 70% and populations match."""
        result = self._make_result()
        result = check_ancestry_mismatch(
            result, inferred_ancestry="EUR", top_ancestry_fraction=0.85
        )
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_admixture_warning_combined_with_mismatch(self) -> None:
        """Both mismatch and admixture warnings when both conditions hold."""
        result = self._make_result(source_ancestry="EUR")
        result = check_ancestry_mismatch(
            result, inferred_ancestry="AFR", top_ancestry_fraction=0.55
        )
        assert result.ancestry_mismatch is True
        assert "AFR" in result.ancestry_warning_text
        assert "admixed" in result.ancestry_warning_text.lower()

    def test_no_admixture_warning_when_fraction_none(self) -> None:
        """No admixture warning when top fraction is not available."""
        result = self._make_result()
        result = check_ancestry_mismatch(
            result, inferred_ancestry="EUR", top_ancestry_fraction=None
        )
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_admixture_warning_at_boundary_70(self) -> None:
        """Exactly 70% should NOT trigger warning (threshold is < 70%)."""
        result = self._make_result()
        result = check_ancestry_mismatch(
            result, inferred_ancestry="EUR", top_ancestry_fraction=0.70
        )
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_admixture_warning_at_69(self) -> None:
        """69% should trigger the admixture warning."""
        result = self._make_result()
        result = check_ancestry_mismatch(
            result, inferred_ancestry="EUR", top_ancestry_fraction=0.69
        )
        assert result.ancestry_mismatch is True
        assert "admixed" in result.ancestry_warning_text.lower()


class TestLAIPreferredOverTier1:
    """T-PRS-04: LAI-derived ancestry preferred over Tier 1 when available."""

    def test_local_ancestry_preferred(self, sample_engine: sa.Engine) -> None:
        """get_inferred_ancestry prefers local_ancestry over nnls_admixture."""
        with sample_engine.begin() as conn:
            # Insert nnls_admixture first
            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 2,
                    "finding_text": "NNLS: EUR",
                    "detail_json": json.dumps({"top_population": "EUR"}),
                },
            )
            # Insert local_ancestry second
            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": "local_ancestry",
                    "evidence_level": 2,
                    "finding_text": "LAI: AFR",
                    "detail_json": json.dumps({"top_population": "AFR"}),
                },
            )

        result = get_inferred_ancestry(sample_engine)
        assert result == "AFR"

    def test_falls_back_to_nnls_without_lai(self, sample_engine: sa.Engine) -> None:
        """Without local_ancestry, falls back to nnls_admixture."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 2,
                    "finding_text": "NNLS: EUR",
                    "detail_json": json.dumps({"top_population": "EUR"}),
                },
            )

        result = get_inferred_ancestry(sample_engine)
        assert result == "EUR"


class TestGetTopAncestryFraction:
    """Test get_top_ancestry_fraction() helper."""

    def test_returns_fraction_from_nnls(self, sample_engine: sa.Engine) -> None:
        from backend.analysis.ancestry import get_top_ancestry_fraction

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 2,
                    "finding_text": "NNLS",
                    "detail_json": json.dumps(
                        {
                            "top_population": "EUR",
                            "admixture_fractions": {"EUR": 0.82, "AFR": 0.10, "EAS": 0.08},
                        }
                    ),
                },
            )

        result = get_top_ancestry_fraction(sample_engine)
        assert result == pytest.approx(0.82)

    def test_returns_none_when_no_findings(self, sample_engine: sa.Engine) -> None:
        from backend.analysis.ancestry import get_top_ancestry_fraction

        result = get_top_ancestry_fraction(sample_engine)
        assert result is None

    def test_returns_none_when_no_fractions(self, sample_engine: sa.Engine) -> None:
        from backend.analysis.ancestry import get_top_ancestry_fraction

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 2,
                    "finding_text": "NNLS",
                    "detail_json": json.dumps({"top_population": "EUR"}),
                },
            )

        result = get_top_ancestry_fraction(sample_engine)
        assert result is None

    def test_prefers_local_ancestry(self, sample_engine: sa.Engine) -> None:
        from backend.analysis.ancestry import get_top_ancestry_fraction

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 2,
                    "finding_text": "NNLS",
                    "detail_json": json.dumps(
                        {
                            "top_population": "EUR",
                            "admixture_fractions": {"EUR": 0.82, "AFR": 0.10, "EAS": 0.08},
                        }
                    ),
                },
            )
            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": "local_ancestry",
                    "evidence_level": 2,
                    "finding_text": "LAI",
                    "detail_json": json.dumps(
                        {
                            "top_population": "AFR",
                            "admixture_fractions": {"AFR": 0.65, "EUR": 0.20, "AMR": 0.15},
                        }
                    ),
                },
            )

        result = get_top_ancestry_fraction(sample_engine)
        assert result == pytest.approx(0.65)


class TestMultiAncestryProvenanceWarning:
    """Multi-ancestry score provenance wording (issue #239).

    For a multi-ancestry score whose development set does not include the
    user's inferred ancestry (e.g. the MID / OCE buckets), the warning must
    describe it as a multi-ancestry score listing its development ancestries,
    not assert a single 'AFR population study'.
    """

    def _multi_result(self, source_ancestry: str = "AFR") -> PRSResult:
        return PRSResult(
            weight_set_name="BMI (multi-ancestry)",
            trait="body_mass_index",
            module="traits",
            source_ancestry=source_ancestry,
            source_study="Multi 2023",
            source_pmid="1",
            sample_size=100000,
            raw_score=0.5,
            multi_ancestry=True,
            development_ancestries=["AFR", "AMR", "EAS", "EUR", "SAS"],
        )

    def test_multi_ancestry_mid_not_single_ancestry(self) -> None:
        result = check_ancestry_mismatch(self._multi_result(), inferred_ancestry="MID")
        assert result.ancestry_mismatch is True  # uncovered bucket still flags
        text = result.ancestry_warning_text
        assert text is not None
        # Must describe the score as multi-ancestry and list the dev ancestries…
        assert "multiple ancestries" in text
        assert "AFR, AMR, EAS, EUR, SAS" in text
        assert "MID" in text
        # …and must NOT misrepresent it as a single-ancestry "population study".
        assert "population study" not in text

    def test_multi_ancestry_oce_not_single_ancestry(self) -> None:
        result = check_ancestry_mismatch(self._multi_result(), inferred_ancestry="OCE")
        text = result.ancestry_warning_text
        assert text is not None
        assert "multiple ancestries" in text and "population study" not in text
        assert "OCE" in text

    def test_multi_ancestry_uninferred_lists_dev_ancestries(self) -> None:
        result = check_ancestry_mismatch(self._multi_result(), inferred_ancestry=None)
        text = result.ancestry_warning_text
        assert text is not None
        assert "development ancestries" in text
        assert "AFR, AMR, EAS, EUR, SAS" in text
        assert "population study" not in text

    def test_multi_ancestry_covered_ancestry_no_false_warning(self) -> None:
        # A covered ancestry literally present in the development set must NOT be
        # flagged even when its label differs from ancestries[0] (the resolved
        # source label) — guards CodeRabbit's direct-construction case (#239).
        result = check_ancestry_mismatch(self._multi_result(), inferred_ancestry="EAS")
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_multi_ancestry_csa_sas_alias_no_false_warning(self) -> None:
        # CSA (app label for Central/South Asian) is covered by a SAS-labelled
        # development set via the CSA→SAS alias. _resolve_source_ancestry returns
        # "CSA" (covered), so source==inferred and no warning must fire — even
        # though "CSA" is not literally in development_ancestries (#239 review).
        covered = PRSResult(
            weight_set_name="BMI (multi-ancestry)",
            trait="body_mass_index",
            module="traits",
            source_ancestry="CSA",  # resolved label for a covered CSA sample
            source_study="Multi 2023",
            source_pmid="1",
            sample_size=100000,
            raw_score=0.5,
            multi_ancestry=True,
            development_ancestries=["AFR", "AMR", "EAS", "EUR", "SAS"],
        )
        result = check_ancestry_mismatch(covered, inferred_ancestry="CSA")
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_single_ancestry_wording_is_grammatical(self) -> None:
        # A genuine single-ancestry score keeps the "single-ancestry (...) population
        # study" wording (no fragile "a/an {CODE}" article logic).
        single = PRSResult(
            weight_set_name="T2D (EAS)",
            trait="t2d",
            module="metabolic",
            source_ancestry="EAS",
            source_study="x",
            source_pmid="1",
            sample_size=100,
            raw_score=0.0,
        )
        result = check_ancestry_mismatch(single, inferred_ancestry="EUR")
        text = result.ancestry_warning_text
        assert text is not None
        assert "single-ancestry (EAS) population study" in text
        assert "multiple ancestries" not in text
        assert "a EAS" not in text and "a AFR" not in text  # no ungrammatical article
