"""API tests for the G6PD deficiency context endpoint (SW-E6).

GET /api/analysis/g6pd?sample_id=N
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.g6pd import (
    G6PD_A_MINUS_RSID,
    G6PD_DEFICIENCY_VARIANTS,
    _is_palindromic,
)
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata, samples

# (rsid, reference base) for every G6PD locus the array types and whose strand is
# resolvable — the denominator behind an adequately covered negative result (#2172).
_COVERED_G6PD_REFERENCE: list[tuple[str, str]] = [
    (rsid, ref)
    for _name, rsid, _cdna, ref, deficiency_allele in G6PD_DEFICIENCY_VARIANTS
    if not _is_palindromic(ref, deficiency_allele)
]

# Evaluable sex-chromosome filler so ``infer_biological_sex`` clears the
# minimum-evidence floors (issue #363) and resolves XX for the female samples
# below. Sex inference decides X dosage on the non-PAR chrX heterozygosity *rate*
# (issue #519), so a real female needs a *diploid-X* het rate, not a lone het:
# 60 het + 60 hom = 0.50 (well above the 0.15 diploid cutoff). Positions clear
# PAR1/PAR2 and the G6PD locus at 153,764,217; the chrY denominator is all
# no-call (rate 0.0).
_EVAL_SEX_FILLER: list[dict] = (
    [
        {"rsid": f"rs_g6pd_xhet_{i}", "chrom": "X", "pos": 50_000_000 + i, "genotype": "AG"}
        for i in range(60)
    ]
    + [
        {"rsid": f"rs_g6pd_xhom_{i}", "chrom": "X", "pos": 60_000_000 + i, "genotype": "GG"}
        for i in range(60)
    ]
    + [
        {"rsid": f"rs_g6pd_yfill_{i}", "chrom": "Y", "pos": 2_800_000 + i, "genotype": "--"}
        for i in range(60)
    ]
)


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def g6pd_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # A second sample (non-carrier female negative control): the diploid-X het
    # filler with no chrY evidence makes her XX, with a reference G6PD allele → "normal".
    noncarrier_db_path = tmp_data_dir / "samples" / "sample_2.db"
    noncarrier_engine = sa.create_engine(f"sqlite:///{noncarrier_db_path}")
    create_sample_tables(noncarrier_engine)

    # A third sample: the issue's own repro -- a single reference call and no
    # data for the rest of the panel. Sample 1 is sparse but POSITIVE and sample
    # 2 carries the whole panel, so without this the sparse-NEGATIVE branch that
    # #2172 is about was never exercised through the API (#2205 review).
    sparse_db_path = tmp_data_dir / "samples" / "sample_3.db"
    sparse_engine = sa.create_engine(f"sqlite:///{sparse_db_path}")
    create_sample_tables(sparse_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )
        conn.execute(
            samples.insert().values(
                id=2,
                name="Non-carrier",
                db_path="samples/sample_2.db",
                file_format="v5",
                file_hash="def456",
            )
        )
        conn.execute(
            samples.insert().values(
                id=3,
                name="Sparse negative",
                db_path="samples/sample_3.db",
                file_format="v5",
                file_hash="ghi789",
            )
        )
    # The diploid-X het filler with no chrY evidence makes her XX; a het A- allele
    # then yields the safety-critical "variable" phenotype (X-inactivation).
    with sample_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": G6PD_A_MINUS_RSID, "chrom": "X", "pos": 153764217, "genotype": "CT"},
                *_EVAL_SEX_FILLER,
            ],
        )
    with noncarrier_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                # Diploid-X het filler (below) with no chrY signal → XX.
                {"rsid": "rs_x_het", "chrom": "X", "pos": 150000000, "genotype": "AG"},
                # Every array-typeable, strand-resolvable G6PD locus at its reference
                # base. A lone reference A- call is no longer a negative panel (#2172)
                # — it is insufficient coverage — so this negative control has to
                # actually cover the panel before it can assert "normal".
                *(
                    {
                        "rsid": rsid,
                        "chrom": "X",
                        "pos": 153764217 + offset,
                        "genotype": ref * 2,
                    }
                    for offset, (rsid, ref) in enumerate(_COVERED_G6PD_REFERENCE)
                ),
                *_EVAL_SEX_FILLER,
            ],
        )

    with sparse_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": "rs_x_het", "chrom": "X", "pos": 150000000, "genotype": "AG"},
                # One reference A- call and nothing else -- the #2172 repro.
                {
                    "rsid": G6PD_A_MINUS_RSID,
                    "chrom": "X",
                    "pos": 153764217,
                    "genotype": "CC",
                },
                *_EVAL_SEX_FILLER,
            ],
        )

    ref_engine.dispose()
    sample_engine.dispose()
    noncarrier_engine.dispose()
    sparse_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
        reset_registry()


class TestG6pdEndpoint:
    def test_heterozygous_female_is_variable(self, g6pd_client: TestClient) -> None:
        data = g6pd_client.get("/api/analysis/g6pd?sample_id=1").json()
        assert data["inferred_sex"] == "XX"
        assert data["phenotype"] == "variable"
        assert data["at_risk"] is True
        assert data["high_risk_drugs"]

    def test_context_only_disclosure(self, g6pd_client: TestClient) -> None:
        data = g6pd_client.get("/api/analysis/g6pd?sample_id=1").json()
        assert data["context_only"] is True
        assert data["any_called"] is True
        assert data["note"]
        assert data["pmid_citations"]

    def test_strand_ambiguous_fields_serialized(self, g6pd_client: TestClient) -> None:
        # The palindromic-locus transparency fields (#321) reach the response model.
        # Sample 1 carries only the non-palindromic A− het, so nothing is withheld here;
        # the strand-ambiguity behaviour itself is unit-tested in test_g6pd.py.
        data = g6pd_client.get("/api/analysis/g6pd?sample_id=1").json()
        assert data["strand_ambiguous_loci"] == []
        assert data["variants"], "expected at least one variant in response"
        assert all("strand_ambiguous" in v for v in data["variants"])

    def test_noncarrier_is_normal(self, g6pd_client: TestClient) -> None:
        # Negative control: non-carrier female with the panel actually covered →
        # normal, no risk surfaced.
        data = g6pd_client.get("/api/analysis/g6pd?sample_id=2").json()
        assert data["inferred_sex"] == "XX"
        assert data["phenotype"] == "normal"
        assert data["at_risk"] is False
        assert data["high_risk_drugs"] == []
        assert data["medication_risk"] == "no_tested_allele_detected"
        assert data["coverage_sufficient"] is True

    def test_sparse_negative_does_not_clear_risk(self, g6pd_client: TestClient) -> None:
        """#2172 through the production route: a single reference call is not a
        negative panel, and must not clear the oxidative-drug warning.

        Sample 1 is sparse but positive and sample 2 carries the whole panel, so
        this branch had no API coverage.
        """
        data = g6pd_client.get("/api/analysis/g6pd?sample_id=3").json()
        assert data["phenotype"] == "indeterminate"
        assert data["coverage_sufficient"] is False
        assert data["called_resolvable_records"] < data["resolvable_records"]
        assert data["medication_risk"] == "undetermined"
        # The legacy fields existing clients read must not read as cleared.
        assert data["at_risk"] is True
        assert data["high_risk_drugs"]

    def test_coverage_fields_are_serialized(self, g6pd_client: TestClient) -> None:
        """#2172 response lock: the coverage/medication-risk fields must reach the
        client.

        `G6pdResponse` is an explicit Pydantic model, so a field present in
        `assess_g6pd`'s dict but absent from the model is silently dropped — the
        consumer would keep seeing only the two-state `at_risk`, which is the exact
        conflation this issue is about.
        """
        data = g6pd_client.get("/api/analysis/g6pd?sample_id=2").json()
        for field in (
            "medication_risk",
            "panel_records",
            "resolvable_records",
            "called_resolvable_records",
            "coverage_sufficient",
        ):
            assert field in data, f"{field} dropped by G6pdResponse"
        # Non-vacuous: the denominator is the real curated panel, not zero.
        assert data["panel_records"] >= data["resolvable_records"] > 0
        assert data["called_resolvable_records"] == data["resolvable_records"]

    def test_invalid_sample_returns_404(self, g6pd_client: TestClient) -> None:
        assert g6pd_client.get("/api/analysis/g6pd?sample_id=999").status_code == 404
