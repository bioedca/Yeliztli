"""Route-level tests for the MTHFR & Methylation API (#1946).

Guards the ``POST /api/analysis/methylation/run`` response contract and the
``run`` → stored finding → ``GET /pathways`` compound-het handoff.

``test_methylation.py`` owns the science: it calls ``score_methylation_pathways``
and ``store_methylation_findings`` directly and never crosses the API boundary,
so a route that drops or mis-shapes the live handoff stays green there. These
tests exercise the boundary only — they seed genotypes whose compound-het
meaning is established by ``test_methylation.py::TestCompoundHeterozygosity``
and assert what the routes surface, rather than re-deriving the allele call.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, raw_variants, reference_metadata, samples

# (rsid, chrom, pos) for the two MTHFR variants the compound-het call rests on,
# matching the coordinates test_methylation.py seeds.
C677T = ("rs1801133", "1", 11856378)
A1298C = ("rs1801131", "1", 11854476)

# C677T het + A1298C het = compound heterozygote; both wild type = not.
COMPOUND_HET_GENOTYPES = {C677T: "GA", A1298C: "AC"}
WILD_TYPE_GENOTYPES = {C677T: "GG", A1298C: "AA"}

RUN_URL = "/api/analysis/methylation/run?sample_id=1"
PATHWAYS_URL = "/api/analysis/methylation/pathways?sample_id=1"


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[sa.Engine, None, None]:
    """Temporary data dir with one registered sample, wired into the registry."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()

    ref_db = data_dir / "reference.db"
    ref_engine = sa.create_engine(f"sqlite:///{ref_db}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                }
            ],
        )

    sample_db = data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
    create_sample_tables(sample_engine)

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)

    with ExitStack() as stack:
        stack.enter_context(
            patch("backend.api.routes.methylation.get_registry", return_value=registry)
        )
        stack.enter_context(patch("backend.api.dependencies.get_registry", return_value=registry))
        stack.enter_context(
            patch("backend.services.staleness.get_registry", return_value=registry)
        )
        yield sample_engine

    registry.dispose_all()
    reset_registry()


@pytest.fixture()
def client(_env: sa.Engine) -> TestClient:
    from backend.api.routes.methylation import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _seed_mthfr(engine: sa.Engine, genotypes: dict[tuple[str, str, int], str]) -> None:
    """Insert raw_variants rows for the given {(rsid, chrom, pos): genotype}."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}
                for (rsid, chrom, pos), genotype in genotypes.items()
            ],
        )


class TestRunRoute:
    def test_run_response_contract(self, client: TestClient, _env: sa.Engine) -> None:
        """POST /run returns exactly findings_count + pathways_scored."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)

        resp = client.post(RUN_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"findings_count", "pathways_scored"}
        assert body["pathways_scored"] == 5
        assert body["findings_count"] > 0

    def test_run_is_idempotent(self, client: TestClient, _env: sa.Engine) -> None:
        """Re-running must not accumulate duplicate findings."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)

        first = client.post(RUN_URL).json()
        second = client.post(RUN_URL).json()

        assert first == second

    def test_run_unknown_sample_is_404(self, client: TestClient) -> None:
        assert client.post("/api/analysis/methylation/run?sample_id=999").status_code == 404


class TestCompoundHetHandoff:
    def test_run_then_pathways_surfaces_compound_het(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        """The stored compound-het finding is recovered through GET /pathways."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)
        assert client.post(RUN_URL).status_code == 200

        resp = client.get(PATHWAYS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["items"]) == 5

        compound_het = body["compound_het"]
        assert compound_het is not None
        assert compound_het["is_compound_het"] is True
        assert compound_het["is_double_homozygous"] is False
        assert compound_het["c677t_genotype"] == "GA"
        assert compound_het["a1298c_genotype"] == "AC"
        assert "compound" in compound_het["finding_text"].lower()

    def test_pathways_reports_no_compound_het_for_wild_type(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        """Wild-type C677T/A1298C must not surface a compound-het call."""
        _seed_mthfr(_env, WILD_TYPE_GENOTYPES)
        assert client.post(RUN_URL).status_code == 200

        resp = client.get(PATHWAYS_URL)

        assert resp.status_code == 200
        compound_het = resp.json()["compound_het"]
        assert compound_het is None or compound_het["is_compound_het"] is False

    def test_pathways_without_run_has_no_findings(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        """GET /pathways before any run returns an empty, well-formed body."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)

        resp = client.get(PATHWAYS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["compound_het"] is None


class TestPathwayDetailStrand:
    """The detail route says which genome strand each genotype was read on (#2103).

    Variant names follow the gene's transcript, so the Advanced View needs the
    strand to explain why a genotype's letters can be the name's complements.
    Arrays report some probes on their design strand, so it is told from every
    allele the VEP bundle records at the locus plus the sample's own annotated
    pair, never assumed, and withheld when the call fits both strands.
    """

    DETAIL_URL = "/api/analysis/methylation/pathway/folate_mthfr?sample_id=1"

    # (rsid, chrom, pos) of the sample's raw row -> (genotype, bundle rows as
    # (chrom, pos, ref, alt), sample annotated pair)
    CASES: dict[
        tuple[str, str, int],
        tuple[str, list[tuple[str, int, str, str]], tuple[str, str] | None],
    ] = {
        A1298C: ("GT", [("1", 11854476, "T", "G")], ("T", "G")),  # plus
        ("rs202676", "11", 49227620): ("TC", [("11", 49227620, "A", "G")], ("A", "G")),  # minus
        # G>A,C: GG fits both strands.
        C677T: ("GG", [("1", 11856378, "G", "A"), ("1", 11856378, "G", "C")], ("G", "A")),
        ("rs1801198", "22", 31012379): (
            "CC",
            [("22", 31012379, "G", "A"), ("22", 31012379, "G", "C")],
            ("G", "C"),
        ),
        # The bundle records T>C only; the sample's annotation adds G, so GG
        # fits both strands. Without the sample pair it would read as minus.
        ("rs1051266", "21", 46957794): ("GG", [("21", 46957794, "T", "C")], ("T", "G")),
        # The bundle's record for this rsid is at another coordinate, so it does
        # not describe the sample's row; coordinate-blind it would read as plus.
        ("rs1677693", "5", 79950000): ("GT", [("5", 79950999, "G", "T")], None),
        ("rs3758149", "10", 97000000): ("CC", [("10", 97000000, "C", "T")], None),  # plus
    }
    # An annotated row at another coordinate does not describe the sample's
    # row either; counted, its G would make rs3758149 CC fit both strands.
    STRAY_ANNOTATION = {
        "rsid": "rs3758149",
        "chrom": "10",
        "pos": 97000001,
        "ref": "C",
        "alt": "G",
    }

    def _write_bundle(self, engine: sa.Engine) -> Path:
        bundle = Path(str(engine.url.database)).parent.parent / "vep_bundle.db"
        with sa.create_engine(f"sqlite:///{bundle}").begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE vep_annotations "
                "(rsid TEXT, chrom TEXT, pos INTEGER, ref TEXT, alt TEXT)"
            )
            for (rsid, _c, _p), (_gt, vep_rows, _pair) in self.CASES.items():
                for chrom, pos, ref, alt in vep_rows:
                    conn.exec_driver_sql(
                        "INSERT INTO vep_annotations VALUES (?, ?, ?, ?, ?)",
                        (rsid, chrom, pos, ref, alt),
                    )
        return bundle

    def _snps(self, client: TestClient, engine: sa.Engine) -> dict[str, dict]:
        _seed_mthfr(engine, {key: gt for key, (gt, _v, _p) in self.CASES.items()})
        with engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {"rsid": rsid, "chrom": chrom, "pos": pos, "ref": pair[0], "alt": pair[1]}
                    for (rsid, chrom, pos), (_gt, _v, pair) in self.CASES.items()
                    if pair is not None
                ]
                + [self.STRAY_ANNOTATION],
            )
        assert client.post(RUN_URL).status_code == 200
        resp = client.get(self.DETAIL_URL)
        assert resp.status_code == 200
        return {s["rsid"]: s for s in resp.json()["snp_details"]}

    def test_each_call_reports_the_strand_only_it_fits(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        self._write_bundle(_env)
        snps = self._snps(client, _env)
        assert snps["rs1801131"]["genotype"] == "GT"
        assert snps["rs1801131"]["genotype_strand"] == "plus"
        assert snps["rs202676"]["genotype"] == "TC"
        assert snps["rs202676"]["genotype_strand"] == "minus"
        assert snps["rs3758149"]["genotype"] == "CC"
        assert snps["rs3758149"]["genotype_strand"] == "plus"

    def test_no_strand_is_claimed_when_the_call_fits_both_or_neither(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        self._write_bundle(_env)
        snps = self._snps(client, _env)
        assert snps["rs1801133"]["genotype_strand"] is None  # multi-allelic G>A,C
        assert snps["rs1801198"]["genotype_strand"] is None  # C/G palindrome
        assert snps["rs1051266"]["genotype_strand"] is None  # widened by the sample pair
        assert snps["rs1677693"]["genotype"] == "GT"
        assert snps["rs1677693"]["genotype_strand"] is None  # bundle record elsewhere

    def test_without_a_bundle_nothing_is_labelled_and_none_is_created(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        bundle = Path(str(_env.url.database)).parent.parent / "vep_bundle.db"
        assert not bundle.exists()
        snps = self._snps(client, _env)
        assert {s["genotype_strand"] for s in snps.values()} == {None}
        assert not bundle.exists()
