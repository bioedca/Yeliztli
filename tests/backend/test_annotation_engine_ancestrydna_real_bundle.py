"""Nightly slow-tier real-bundle accuracy test for AncestryDNA (Step 42, ADNA-09a).

Dormant on every PR-blocking run: ``requires_real_bundle`` is auto-skipped
by :mod:`tests.conftest` when *neither* the production LAI nor VEP bundle is
present locally, and the :func:`real_vep_bundle_path` gate fixture below
short-circuits with ``pytest.skip()`` when the specific VEP bundle is
missing. The nightly workflow (:file:`.github/workflows/nightly.yml`)
downloads the bundle — cached by the manifest's ``sha256`` per Plan §16.5 —
into ``~/.yeliztli/vep_bundle.db`` before invoking ``pytest -m slow``,
at which point this class executes.

Plan §13.1 ADNA-09a thresholds:

- **VEP bundle hit-rate ≥ 95%.** Of all variants in the synthetic AncestryDNA
  fixture, at least 95% must resolve via the bundle's rsid index or the
  ``(chrom, pos)`` coordinate-fallback path. Combined hit-rate is what the
  downstream analysis modules see; the threshold guards against bundle
  drift that would silently suppress findings.
- **ClinVar P/LP hit-rate ≥ 85%.** Of the *intersection* between the
  fixture's variants and ClinVar positions classified as Pathogenic or
  Likely_pathogenic in the local reference DB, at least 85% must receive a
  ``clinvar_significance`` value in ``annotated_variants``. Skipped when the
  reference DB has no ClinVar rows (the nightly workflow may not always
  rehydrate the full 250 MB ClinVar pipeline DB).

Both thresholds are bio-validator calibration targets — on the first
successful nightly run against ``vep_bundle v2.0.0``, the validator
inspects the observed rates and either accepts them or re-tunes the
thresholds in this file. Failures auto-file a GitHub Issue labeled
``slow-tier-regression`` (workflow side); the test itself never falls back
to a softer assertion.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.engine import run_annotation
from backend.config import get_settings
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    clinvar_variants,
    database_versions,
    raw_variants,
    reference_metadata,
    sample_metadata_table,
)
from backend.ingestion.base import SourceVendor
from backend.ingestion.parser_ancestrydna import parse_ancestrydna

# Plan §13.1 thresholds (bio-validator-calibrated).
VEP_HIT_RATE_FLOOR = 0.95
CLINVAR_PLP_HIT_RATE_FLOOR = 0.85

# Heuristic floor for "is this the real production VEP bundle, not a stub?"
# Production releases are hundreds of MB; anything smaller than 100 MB is
# treated as a development stub and the slow-tier test stays dormant.
_REAL_VEP_BUNDLE_MIN_BYTES = 100_000_000

# ClinVar significance strings that ClinVar's parser writes for P / LP records.
# The parser lowercases the CLNSIG underscores into spaces and keeps native
# casing (see :func:`backend.annotation.clinvar.parse_clinvar_vcf_line`);
# these are the canonical post-parse values.
_PATHOGENIC_TOKENS = (
    "pathogenic",
    "likely pathogenic",
    "pathogenic, low penetrance",
    "likely pathogenic, low penetrance",
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SYNTHETIC_FIXTURE = FIXTURES_DIR / "synthetic_eur_ancestrydna.txt"


# ── Gate ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_vep_bundle_path() -> Path:
    """Resolve the real production VEP bundle path or skip the test module.

    Declared first in every test method so the skip propagates *before* the
    other module-scoped fixtures touch the real reference DB or parse the
    5,000-row synthetic fixture. The conftest gate keeps the marker dormant
    when neither bundle is present locally, but a dev who has only the LAI
    bundle still trips that gate and needs this in-fixture short-circuit.
    """
    path = get_settings().vep_bundle_db_path
    if not path.is_file():
        pytest.skip(f"Real VEP bundle missing at {path}; nightly-only test.")
    if path.stat().st_size < _REAL_VEP_BUNDLE_MIN_BYTES:
        pytest.skip(
            f"Bundle at {path} is only {path.stat().st_size} bytes; "
            "looks like a dev stub, not the real production release asset."
        )
    return path


# ── Fixtures (all module-scoped — read-only once initialized) ────────────


@pytest.fixture(scope="module")
def synthetic_parsed(real_vep_bundle_path: Path):  # noqa: ARG001 — gate dep
    """Parse the synthetic AncestryDNA fixture once per module.

    Calls :func:`parser_ancestrydna.parse_ancestrydna` directly rather than
    routing through :mod:`backend.ingestion.dispatcher`. The step-41
    generator embeds a ``# Derived from … synthetic_eur_23andme.txt``
    provenance comment into the fixture head, and Plan §8.3 gives the
    dispatcher's ``"23andme"`` substring heuristic precedence over the
    AncestryDNA signature — so the dispatcher mis-routes this specific
    fixture to the 23andMe parser. The annotation engine is the subject
    under test here, not the dispatcher; vendor-specific parsing keeps the
    test scoped without papering over the dispatcher contract.
    """
    if not SYNTHETIC_FIXTURE.exists():
        pytest.skip(
            f"Synthetic AncestryDNA fixture missing at {SYNTHETIC_FIXTURE}; "
            "regenerate via `python scripts/regenerate_fixtures.py --vendor=ancestrydna`."
        )
    result = parse_ancestrydna(SYNTHETIC_FIXTURE)
    assert result.vendor is SourceVendor.ANCESTRYDNA
    assert result.version == "v2.0"
    assert result.variants, "synthetic fixture parsed zero variants"
    return result


@pytest.fixture(scope="module")
def synthetic_sample_engine(synthetic_parsed) -> sa.Engine:
    """In-memory per-sample DB primed with parsed synthetic AncestryDNA variants.

    Named distinctly from :func:`tests.backend.conftest.sample_engine` so the
    function-scoped conftest fixture isn't accidentally shadowed.
    """
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_sample_tables(engine)
    file_format = f"{synthetic_parsed.vendor.value}_{synthetic_parsed.version}"
    rows = [
        {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
        for v in synthetic_parsed.variants
    ]
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="synthetic-eur-ancestrydna",
                file_format=file_format,
                file_hash="synthetic-real-bundle-fixture",
            )
        )
        conn.execute(raw_variants.insert(), rows)
    return engine


@pytest.fixture(scope="module")
def real_vep_engine(real_vep_bundle_path: Path) -> sa.Engine:
    """Read-only engine pointing at the real production VEP bundle."""
    engine = sa.create_engine(f"sqlite:///{real_vep_bundle_path}", pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def reference_engine_with_clinvar(real_vep_bundle_path: Path) -> sa.Engine:  # noqa: ARG001
    """In-memory reference DB cloned with whatever ClinVar rows are available locally.

    If the user has run the ClinVar pipeline (``reference.db`` exists with a
    populated ``clinvar_variants`` table), those rows are copied verbatim so
    the slow-tier test can compute a real P/LP intersection. Otherwise the
    table stays empty and the ClinVar assertion individually skips.

    Module-scoped so the (potentially multi-million-row) copy runs once per
    pytest session for this file, not once per test method.
    """
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(database_versions.insert().values(db_name="vep_bundle", version="v2.0.0"))

    settings = get_settings()
    real_reference_path = settings.reference_db_path
    if not real_reference_path.is_file():
        return engine

    src = sa.create_engine(f"sqlite:///{real_reference_path}", pool_pre_ping=True)
    try:
        with src.connect() as src_conn:
            has_table = src_conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='clinvar_variants'"
                )
            ).fetchone()
            if not has_table:
                return engine
            rows = src_conn.execute(
                sa.select(
                    clinvar_variants.c.rsid,
                    clinvar_variants.c.chrom,
                    clinvar_variants.c.pos,
                    clinvar_variants.c.ref,
                    clinvar_variants.c.alt,
                    clinvar_variants.c.significance,
                    clinvar_variants.c.review_stars,
                    clinvar_variants.c.accession,
                    clinvar_variants.c.conditions,
                    clinvar_variants.c.gene_symbol,
                    clinvar_variants.c.variation_id,
                )
            ).fetchall()
    finally:
        src.dispose()

    if rows:
        with engine.begin() as conn:
            conn.execute(clinvar_variants.insert(), [dict(r._mapping) for r in rows])

    return engine


@pytest.fixture(scope="module")
def registry(real_vep_engine: sa.Engine, reference_engine_with_clinvar: sa.Engine) -> MagicMock:
    """Mock :class:`DBRegistry` exposing real VEP + (optional) ClinVar references.

    gnomAD and dbNSFP intentionally stay unavailable: the slow-tier
    thresholds gate only the VEP bundle (Plan §5.6) and ClinVar P/LP
    coverage. Asking the engine to run those sources would force every
    nightly run to also rehydrate ~3.5 GB of pipeline DBs that the workflow
    does not provision.
    """
    reg = MagicMock()
    reg.reference_engine = reference_engine_with_clinvar
    type(reg).vep_engine = property(lambda self: real_vep_engine)

    def _unavailable(self):
        raise RuntimeError("source unavailable for ADNA-09a real-bundle slow-tier test")

    type(reg).gnomad_engine = property(_unavailable)
    type(reg).dbnsfp_engine = property(_unavailable)
    return reg


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_pathogenic(significance: str | None) -> bool:
    """Return True if the ClinVar significance string is P or LP."""
    if not significance:
        return False
    sig_lower = significance.strip().lower()
    return any(sig_lower == token for token in _PATHOGENIC_TOKENS)


def _plp_keys_in_clinvar(reference_engine: sa.Engine) -> tuple[set[str], set[tuple[str, int]]]:
    """Return (rsids, (chrom, pos) pairs) for all ClinVar P/LP rows in scope."""
    plp_rsids: set[str] = set()
    plp_coords: set[tuple[str, int]] = set()
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                clinvar_variants.c.rsid,
                clinvar_variants.c.chrom,
                clinvar_variants.c.pos,
                clinvar_variants.c.significance,
            )
        ).fetchall()
    for row in rows:
        if not _is_pathogenic(row.significance):
            continue
        if row.rsid:
            plp_rsids.add(row.rsid)
        if row.chrom and row.pos is not None:
            plp_coords.add((row.chrom, int(row.pos)))
    return plp_rsids, plp_coords


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.requires_real_bundle
class TestAncestryDNARealBundle:
    """Step 42 / Plan §13.1 ADNA-09a: nightly slow-tier accuracy guards."""

    def test_vep_bundle_hit_rate_at_least_95_percent(
        self,
        real_vep_bundle_path: Path,  # noqa: ARG002 — gate first
        synthetic_parsed,
        synthetic_sample_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Combined rsid + (chrom, pos) coord-fallback coverage ≥ 95%."""
        result = run_annotation(synthetic_sample_engine, registry)
        stats = result.coverage_stats

        total = stats["total_variants"]
        rsid_hits = stats["vep_bundle_rsid_hits"]
        coord_hits = stats["vep_bundle_coord_fallback_hits"]

        assert total == len(synthetic_parsed.variants)
        assert total > 0

        combined_hit_rate = (rsid_hits + coord_hits) / total
        assert combined_hit_rate >= VEP_HIT_RATE_FLOOR, (
            f"VEP combined hit-rate {combined_hit_rate:.4f} below floor "
            f"{VEP_HIT_RATE_FLOOR:.2f} ({rsid_hits} rsid + {coord_hits} coord / "
            f"{total} variants). Bio-validator: investigate bundle drift or "
            "re-tune VEP_HIT_RATE_FLOOR in this file if the bundle was "
            "legitimately re-cut."
        )

        # Plan §5.6 telemetry contract — confirm the run we measured really
        # came from a v2.0.0-tagged bundle, not a stale reference DB row.
        assert stats["bundle_version"] == "v2.0.0"

    def test_clinvar_pathogenic_hit_rate_at_least_85_percent(
        self,
        real_vep_bundle_path: Path,  # noqa: ARG002 — gate first
        synthetic_parsed,
        synthetic_sample_engine: sa.Engine,
        reference_engine_with_clinvar: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Of fixture variants on ClinVar P/LP positions, ≥85% get annotated."""
        plp_rsids, plp_coords = _plp_keys_in_clinvar(reference_engine_with_clinvar)
        if not plp_rsids and not plp_coords:
            pytest.skip(
                "Reference DB has no ClinVar P/LP rows; ClinVar pipeline DB "
                "is not provisioned in the nightly workflow yet."
            )

        # Intersection: fixture variants that *should* receive a ClinVar hit.
        expected_keys: set[str] = set()
        for v in synthetic_parsed.variants:
            if v.rsid in plp_rsids:
                expected_keys.add(v.rsid)
                continue
            if (v.chrom, v.pos) in plp_coords:
                expected_keys.add(v.rsid)
        if not expected_keys:
            pytest.skip(
                "Synthetic AncestryDNA fixture has zero overlap with ClinVar P/LP "
                "positions in the local reference DB; nothing to measure."
            )

        run_annotation(synthetic_sample_engine, registry)

        with synthetic_sample_engine.connect() as conn:
            annotated_rows = conn.execute(
                sa.select(
                    annotated_variants.c.rsid,
                    annotated_variants.c.clinvar_significance,
                )
            ).fetchall()
        annotated_plp_rsids = {
            row.rsid
            for row in annotated_rows
            if row.rsid in expected_keys and _is_pathogenic(row.clinvar_significance)
        }

        hit_rate = len(annotated_plp_rsids) / len(expected_keys)
        assert hit_rate >= CLINVAR_PLP_HIT_RATE_FLOOR, (
            f"ClinVar P/LP hit-rate {hit_rate:.4f} below floor "
            f"{CLINVAR_PLP_HIT_RATE_FLOOR:.2f} "
            f"({len(annotated_plp_rsids)}/{len(expected_keys)} P/LP overlapping "
            "rsIDs annotated). Bio-validator: investigate ClinVar pipeline "
            "drift or re-tune CLINVAR_PLP_HIT_RATE_FLOOR in this file."
        )
