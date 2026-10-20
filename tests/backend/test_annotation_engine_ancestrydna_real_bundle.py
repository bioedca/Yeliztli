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

- **VEP bundle hit-rate ≥ 95%** of the rows where an allele-specific answer
  is possible. Those rows must resolve via the bundle's rsid index or the
  ``(chrom, pos)`` coordinate-fallback path. Since #1458 the lookup is
  allele-specific, so a call that cannot be tied to one of a multi-ALT rsID's
  alleles is withheld on purpose; a hom-ref call carries no ALT at all. The
  engine reports those rows as ``vep_bundle_allele_ambiguous``. They are left
  out of the denominator, and checked independently against the bundle so a
  regression cannot hide among them (#2494). On v4.0.0 with this fixture,
  1,148 rows were withheld this way, 3,832 hit by rsID and 9 by coordinate,
  and 11 missed. The coordinate path could not match at all until the
  fixture moved from GRCh38 to GRCh37 (#2493).
- **ClinVar P/LP hit-rate ≥ 85%.** Of the fixture's calls that sit at a
  ClinVar Pathogenic or Likely_pathogenic rsID, or that carry a P/LP record's
  ALT at its position, in the local reference DB, at least 85% must receive a
  ``clinvar_significance`` value in ``annotated_variants``. Skipped when the
  reference DB has no ClinVar rows (the nightly workflow may not always
  rehydrate the full 250 MB ClinVar pipeline DB), or when no fixture call
  qualifies, which on this fixture is always (#2493).
- **ClinVar carrier control ≥ 85% per lookup route.** Synthetic heterozygous
  carriers at single-base P/LP records that stand alone at their site, drawn
  from the local ClinVar at run time. Half keep the record's rsID and half take
  a vendor-style probe ID, so that only the ``(chrom, pos)`` fallback can
  resolve them. Skipped when the reference DB has no ClinVar rows.

Both thresholds are bio-validator calibration targets — on the first
successful nightly run against the release ``bundles/manifest.json`` pins, the validator
inspects the observed rates and either accepts them or re-tunes the
thresholds in this file. Failures auto-file a GitHub Issue labeled
``slow-tier-regression`` (workflow side); the test itself never falls back
to a softer assertion.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.analysis.clinvar_significance import is_pathogenic_primary
from backend.annotation.engine import run_annotation
from backend.config import get_settings
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    clinvar_variants,
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


def _primed_sample_engine(rows: list[dict], *, name: str, file_format: str) -> sa.Engine:
    """In-memory per-sample DB holding ``rows`` as raw variants."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=name,
                file_format=file_format,
                file_hash=f"{name}-real-bundle-fixture",
            )
        )
        conn.execute(raw_variants.insert(), rows)
    return engine


@pytest.fixture(scope="module")
def synthetic_sample_engine(synthetic_parsed) -> sa.Engine:
    """In-memory per-sample DB primed with parsed synthetic AncestryDNA variants.

    Named distinctly from :func:`tests.backend.conftest.sample_engine` so the
    function-scoped conftest fixture isn't accidentally shadowed.
    """
    rows = [
        {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
        for v in synthetic_parsed.variants
    ]
    return _primed_sample_engine(
        rows,
        name="synthetic-eur-ancestrydna",
        file_format=f"{synthetic_parsed.vendor.value}_{synthetic_parsed.version}",
    )


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
    # No ``database_versions`` stamp: the engine then resolves the version from
    # the staged bundle's own metadata, so the telemetry check below compares a
    # real value. A hard-coded stamp was what it used to read back (#2552).
    reference_metadata.create_all(engine)

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
def registry(
    real_vep_bundle_path: Path,
    real_vep_engine: sa.Engine,
    reference_engine_with_clinvar: sa.Engine,
) -> MagicMock:
    """Mock :class:`DBRegistry` exposing real VEP + (optional) ClinVar references.

    gnomAD and dbNSFP intentionally stay unavailable: the slow-tier
    thresholds gate only the VEP bundle (Plan §5.6) and ClinVar P/LP
    coverage. Asking the engine to run those sources would force every
    nightly run to also rehydrate ~3.5 GB of pipeline DBs that the workflow
    does not provision.
    """
    reg = MagicMock()
    reg.reference_engine = reference_engine_with_clinvar
    # The real path, so version telemetry reads the bundle actually queried. A bare
    # MagicMock path always claimed to be a file and never reached the bundle.
    reg.settings = SimpleNamespace(vep_bundle_db_path=real_vep_bundle_path)
    type(reg).vep_engine = property(lambda self: real_vep_engine)

    def _unavailable(self):
        raise RuntimeError("source unavailable for ADNA-09a real-bundle slow-tier test")

    type(reg).gnomad_engine = property(_unavailable)
    type(reg).dbnsfp_engine = property(_unavailable)
    return reg


# ── Helpers ──────────────────────────────────────────────────────────────


def _manifest_vep_bundle_version() -> str:
    """The VEP bundle release ``bundles/manifest.json`` pins."""
    manifest = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["bundles"]["vep_bundle"]["version"]


def _plp_keys_in_clinvar(
    reference_engine: sa.Engine,
) -> tuple[set[str], dict[tuple[str, int], set[str]]]:
    """Return the P/LP rsIDs, and the P/LP ALTs at each ``(chrom, pos)``, in scope."""
    plp_rsids: set[str] = set()
    plp_alts_by_coord: dict[tuple[str, int], set[str]] = {}
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                clinvar_variants.c.rsid,
                clinvar_variants.c.chrom,
                clinvar_variants.c.pos,
                clinvar_variants.c.alt,
                clinvar_variants.c.significance,
            )
        ).fetchall()
    for row in rows:
        if not is_pathogenic_primary(row.significance):
            continue
        if row.rsid:
            plp_rsids.add(row.rsid)
        if row.chrom and row.pos is not None:
            plp_alts_by_coord.setdefault((row.chrom, int(row.pos)), set()).add(
                (row.alt or "").upper()
            )
    return plp_rsids, plp_alts_by_coord


# Carrier calls per lookup route in the ClinVar control below.
CLINVAR_CONTROL_PER_ROUTE = 20


def _clinvar_carrier_controls(reference_engine: sa.Engine) -> list[dict]:
    """Synthetic heterozygous carrier calls at P/LP records drawn from the local ClinVar.

    Taken from the provisioned reference DB at run time, so no clinical claim
    is encoded here. Only single-base P/LP records that stand alone at their
    site are used, spread evenly in ``(chrom, pos)`` order: at such a site a
    carrier must be annotated with that record. Alternate controls keep the
    record's rsID, and the rest take a vendor-style probe ID, so that only the
    ``(chrom, pos)`` fallback can resolve them (#2493).
    """
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                clinvar_variants.c.rsid,
                clinvar_variants.c.chrom,
                clinvar_variants.c.pos,
                clinvar_variants.c.ref,
                clinvar_variants.c.alt,
                clinvar_variants.c.significance,
            ).where(clinvar_variants.c.pos.is_not(None))
        ).fetchall()
    by_site: dict[tuple[str, int], list] = {}
    for row in rows:
        by_site.setdefault((str(row.chrom), int(row.pos)), []).append(row)
    candidates = sorted(
        (
            site_rows[0]
            for site_rows in by_site.values()
            if len(site_rows) == 1
            and site_rows[0].rsid
            and is_pathogenic_primary(site_rows[0].significance)
            and (site_rows[0].ref or "") in {"A", "C", "G", "T"}
            and (site_rows[0].alt or "") in {"A", "C", "G", "T"}
        ),
        key=lambda row: (str(row.chrom), int(row.pos)),
    )
    wanted = 2 * CLINVAR_CONTROL_PER_ROUTE
    picked = candidates[:: max(1, len(candidates) // wanted)][:wanted]
    return [
        {
            "rsid": row.rsid if i % 2 == 0 else f"i_clinvar_control_{i}",
            "chrom": str(row.chrom),
            "pos": int(row.pos),
            "genotype": f"{row.ref}{row.alt}",
            "route": "rsid" if i % 2 == 0 else "coordinate",
        }
        for i, row in enumerate(picked)
    ]


def test_plp_keys_follow_shared_pathogenicity_boundary() -> None:
    """Coverage keys include compound P/LP and exclude lower-penetrance calls."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert(),
            [
                {
                    "rsid": "rs-compound",
                    "chrom": "1",
                    "pos": 101,
                    "ref": "A",
                    "alt": "G",
                    "significance": "Pathogenic|drug response",
                },
                {
                    "rsid": "rs-primary",
                    "chrom": "2",
                    "pos": 202,
                    "ref": "C",
                    "alt": "T",
                    "significance": "Likely pathogenic",
                },
                {
                    "rsid": "rs-low-penetrance",
                    "chrom": "3",
                    "pos": 303,
                    "ref": "G",
                    "alt": "A",
                    "significance": "Pathogenic, low penetrance",
                },
                {
                    "rsid": "rs-benign",
                    "chrom": "4",
                    "pos": 404,
                    "ref": "T",
                    "alt": "C",
                    "significance": "Benign",
                },
            ],
        )

    rsids, coords = _plp_keys_in_clinvar(engine)

    assert rsids == {"rs-compound", "rs-primary"}
    assert coords == {("1", 101): {"G"}, ("2", 202): {"T"}}


# ── Tests ────────────────────────────────────────────────────────────────


def _bundle_rows_for(vep_engine: sa.Engine, variants) -> list[tuple[str, str, int, str, str]]:
    """Every bundle ``(rsid, chrom, pos, ref, alt)`` row at one of the fixture's rsIDs."""
    rsids = sorted({v.rsid for v in variants})
    rows: list[tuple[str, str, int, str, str]] = []
    with vep_engine.connect() as conn:
        for i in range(0, len(rsids), 500):
            chunk = rsids[i : i + 500]
            params = {f"r{j}": rsid for j, rsid in enumerate(chunk)}
            placeholders = ", ".join(f":r{j}" for j in range(len(chunk)))
            for rsid, chrom, pos, ref, alt in conn.execute(
                sa.text(
                    "SELECT rsid, chrom, pos, ref, alt FROM vep_annotations "  # noqa: S608
                    f"WHERE rsid IN ({placeholders})"
                ),
                params,
            ):
                rows.append((rsid, str(chrom), int(pos), ref or "", alt or ""))
    return rows


def _rows_that_could_be_allele_ambiguous(vep_engine: sa.Engine, variants) -> int:
    """Count fixture rows at a multi-ALT rsID whose call carries no unique listed ALT.

    Read straight from the bundle, independent of the engine's allele logic:
    an rsID listed under more than one ref/alt pair, where the genotype contains
    zero or several of the listed ALT alleles. This is an upper bound on what
    the lookup may withhold as allele-ambiguous (#2494).
    """
    pairs: dict[str, set[tuple[str, str]]] = {}
    for rsid, _chrom, _pos, ref, alt in _bundle_rows_for(vep_engine, variants):
        pairs.setdefault(rsid, set()).add((ref.upper(), alt.upper()))
    count = 0
    for variant in variants:
        listed = pairs.get(variant.rsid, set())
        if len(listed) <= 1:
            continue
        genotype = (variant.genotype or "").upper()
        carried_alts = {alt for _ref, alt in listed if alt and alt in genotype}
        if len(carried_alts) != 1:
            count += 1
    return count


@pytest.mark.slow
@pytest.mark.requires_real_bundle
class TestAncestryDNARealBundle:
    """Step 42 / Plan §13.1 ADNA-09a: nightly slow-tier accuracy guards."""

    def test_vep_bundle_hit_rate_at_least_95_percent(
        self,
        real_vep_bundle_path: Path,  # noqa: ARG002 — gate first
        synthetic_parsed,
        synthetic_sample_engine: sa.Engine,
        real_vep_engine: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Rsid + coord-fallback hits are ≥ 95% of the allele-answerable rows."""
        result = run_annotation(synthetic_sample_engine, registry)
        stats = result.coverage_stats

        total = stats["total_variants"]
        rsid_hits = stats["vep_bundle_rsid_hits"]
        coord_hits = stats["vep_bundle_coord_fallback_hits"]
        ambiguous = stats["vep_bundle_allele_ambiguous"]
        misses = stats["vep_misses"]

        assert total == len(synthetic_parsed.variants)
        assert total > 0
        assert rsid_hits + coord_hits + ambiguous + misses == total

        # The excluded rows must really be allele-ambiguous, judged from the
        # bundle itself rather than the engine: each sits at an rsID the bundle
        # lists under several ref/alt pairs, and its call does not carry
        # exactly one of the listed ALTs. A lookup regression that withheld
        # anything else would otherwise leave the denominator and hide.
        could_be_ambiguous = _rows_that_could_be_allele_ambiguous(
            real_vep_engine, synthetic_parsed.variants
        )
        assert ambiguous <= could_be_ambiguous, (
            f"{ambiguous} rows withheld as allele-ambiguous, but only "
            f"{could_be_ambiguous} fixture rows sit at a multi-ALT rsID without "
            "carrying exactly one listed ALT"
        )

        answerable = total - ambiguous
        hit_rate = (rsid_hits + coord_hits) / answerable
        assert hit_rate >= VEP_HIT_RATE_FLOOR, (
            f"VEP hit-rate {hit_rate:.4f} below floor {VEP_HIT_RATE_FLOOR:.2f} over the "
            f"{answerable} allele-answerable rows ({rsid_hits} rsid + {coord_hits} coord; "
            f"{misses} misses; {ambiguous} withheld as allele-ambiguous, of {total}). "
            "Bio-validator: check for bundle drift (misses are rsIDs or alleles the "
            "bundle does not list) or a lookup regression before re-tuning "
            "VEP_HIT_RATE_FLOOR in this file."
        )

        # Plan §5.6 telemetry contract: the reported version is the staged
        # bundle's own, and it must be the release the manifest pins, which the
        # nightly downloads and sha256-checks. This used to compare a v2.0.0 the
        # fixture stamped itself, so a stale bundle could never fail it (#2552).
        assert stats["bundle_version"] == _manifest_vep_bundle_version()

    def test_clinvar_carrier_controls_are_annotated_pathogenic(
        self,
        real_vep_bundle_path: Path,  # noqa: ARG002 — gate first
        synthetic_parsed,
        reference_engine_with_clinvar: sa.Engine,
        registry: MagicMock,
    ) -> None:
        """Carriers at P/LP records resolve by rsID and by coordinate, each ≥ 85% (#2493).

        The fixture's own calls no longer overlap a P/LP record they carry, so
        the hit-rate test above can skip even with ClinVar provisioned. This
        control keeps the annotation path measured on both lookup routes.
        """
        controls = _clinvar_carrier_controls(reference_engine_with_clinvar)
        if not controls:
            pytest.skip(
                "Reference DB has no ClinVar P/LP rows; ClinVar pipeline DB "
                "is not provisioned in the nightly workflow yet."
            )
        assert len(controls) == 2 * CLINVAR_CONTROL_PER_ROUTE, (
            f"only {len(controls)} single-record P/LP SNV sites in the local ClinVar"
        )
        engine = _primed_sample_engine(
            [{k: c[k] for k in ("rsid", "chrom", "pos", "genotype")} for c in controls],
            name="clinvar-carrier-control",
            file_format=f"{synthetic_parsed.vendor.value}_{synthetic_parsed.version}",
        )
        run_annotation(engine, registry)
        with engine.connect() as conn:
            significance = {
                row.rsid: row.clinvar_significance
                for row in conn.execute(
                    sa.select(annotated_variants.c.rsid, annotated_variants.c.clinvar_significance)
                )
            }
        for route in ("rsid", "coordinate"):
            ids = [c["rsid"] for c in controls if c["route"] == route]
            hits = [i for i in ids if is_pathogenic_primary(significance.get(i))]
            hit_rate = len(hits) / len(ids)
            assert hit_rate >= CLINVAR_PLP_HIT_RATE_FLOOR, (
                f"ClinVar P/LP {route} control hit-rate {hit_rate:.4f} below floor "
                f"{CLINVAR_PLP_HIT_RATE_FLOOR:.2f} ({len(hits)}/{len(ids)}); missed "
                f"{sorted(set(ids) - set(hits))[:5]}"
            )

    def test_fixture_sits_at_the_bundles_grch37_coordinates(
        self,
        real_vep_bundle_path: Path,  # noqa: ARG002 — gate first
        synthetic_parsed,
        real_vep_engine: sa.Engine,
    ) -> None:
        """Every fixture rsID the GRCh37 bundle carries sits at its locus and alleles (#2493).

        The fixture had carried GRCh38 positions, and GRCh38-strand calls, under
        a GRCh37 vendor format, so the coordinate fallback above could never
        match. This checks the fixture against the join target itself rather
        than against a copy of its own coordinates.
        """
        loci: dict[str, set[tuple[str, int]]] = {}
        alleles: dict[str, set[str]] = {}
        for rsid, chrom, pos, ref, alt in _bundle_rows_for(
            real_vep_engine, synthetic_parsed.variants
        ):
            loci.setdefault(rsid, set()).add((chrom, pos))
            alleles.setdefault(rsid, set()).update({ref.upper(), alt.upper()})
        covered = [v for v in synthetic_parsed.variants if v.rsid in loci]
        assert len(covered) >= 4_900, f"only {len(covered)} fixture rsIDs are in the bundle"
        misplaced = [
            (v.rsid, f"{v.chrom}:{v.pos}", sorted(loci[v.rsid]))
            for v in covered
            if (str(v.chrom), int(v.pos)) not in loci[v.rsid]
        ]
        assert not misplaced, (
            f"{len(misplaced)} of {len(covered)} fixture rows are off the bundle's "
            f"GRCh37 locus, e.g. {misplaced[:3]}"
        )
        off_strand = [
            (v.rsid, v.genotype, sorted(alleles[v.rsid]))
            for v in covered
            if not set((v.genotype or "").upper()) <= alleles[v.rsid]
        ]
        assert not off_strand, (
            f"{len(off_strand)} fixture calls draw on alleles the bundle does not list "
            f"on the GRCh37 plus strand, e.g. {off_strand[:3]}"
        )

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
        # An rsID match names the variant itself. A coordinate match says only
        # that some P/LP record sits at that base, and which record the engine
        # attaches depends on the allele the call carries (_pick_clinvar_row):
        # at a multi-record site a non-carrier gets the site's top-starred
        # record instead. So a coordinate overlap counts only when the call
        # carries that record's ALT. While the fixture carried GRCh38 positions
        # its one overlap was a cross-build coincidence (#2493).
        expected_keys: set[str] = set()
        for v in synthetic_parsed.variants:
            if v.rsid in plp_rsids:
                expected_keys.add(v.rsid)
                continue
            genotype = (v.genotype or "").upper()
            plp_alts = plp_coords.get((v.chrom, v.pos), set())
            if any(len(alt) == 1 and alt in genotype for alt in plp_alts):
                expected_keys.add(v.rsid)
        if not expected_keys:
            pytest.skip(
                "Synthetic AncestryDNA fixture has no call at a ClinVar P/LP rsID, "
                "and none carrying a P/LP ALT at a P/LP position, in the local "
                "reference DB; nothing to measure."
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
            if row.rsid in expected_keys and is_pathogenic_primary(row.clinvar_significance)
        }

        hit_rate = len(annotated_plp_rsids) / len(expected_keys)
        assert hit_rate >= CLINVAR_PLP_HIT_RATE_FLOOR, (
            f"ClinVar P/LP hit-rate {hit_rate:.4f} below floor "
            f"{CLINVAR_PLP_HIT_RATE_FLOOR:.2f} "
            f"({len(annotated_plp_rsids)}/{len(expected_keys)} P/LP overlapping "
            "rsIDs annotated). Bio-validator: investigate ClinVar pipeline "
            "drift or re-tune CLINVAR_PLP_HIT_RATE_FLOOR in this file."
        )
