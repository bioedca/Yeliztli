"""Tests for IGV.js track data endpoints (P2-17).

Validates:
- ClinVar VCF region + header endpoints
- User sample VCF region + header endpoints
- gnomAD AF JSON features endpoint
- ENCODE cCREs JSON features endpoint
- Chromosome normalization (chr prefix handling)
- Error handling (missing samples, unavailable DBs)
"""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.annotation.encode_ccres import CCREResult
from backend.annotation.gnomad import _create_gnomad_table
from backend.api.routes import igv_tracks as igv_tracks_route
from backend.db.connection import DBRegistry, get_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    clinvar_variants,
    raw_variants,
    samples,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def _seed_clinvar(test_client: TestClient) -> None:
    """Insert test ClinVar variants into reference.db via the active registry."""
    registry = get_registry()
    with registry.reference_engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert(),
            [
                {
                    "rsid": "rs123",
                    "chrom": "17",
                    "pos": 41245466,
                    "ref": "A",
                    "alt": "G",
                    "significance": "Pathogenic",
                    "review_stars": 3,
                    "accession": "VCV000012345",
                    "conditions": "Breast cancer",
                    "gene_symbol": "BRCA1",
                    "variation_id": 12345,
                },
                {
                    "rsid": "rs456",
                    "chrom": "17",
                    "pos": 41245500,
                    "ref": "C",
                    "alt": "T",
                    "significance": "Benign",
                    "review_stars": 2,
                    "accession": "VCV000067890",
                    "conditions": None,
                    "gene_symbol": "BRCA1",
                    "variation_id": 67890,
                },
                {
                    "rsid": "rs789",
                    "chrom": "1",
                    "pos": 100000,
                    "ref": "G",
                    "alt": "A",
                    "significance": "Uncertain_significance",
                    "review_stars": 1,
                    "accession": "VCV000011111",
                    "conditions": "Unknown condition",
                    "gene_symbol": "GENE1",
                    "variation_id": 11111,
                },
            ],
        )


@pytest.fixture()
def _seed_gnomad(db_registry: DBRegistry) -> DBRegistry:
    """Insert test gnomAD variants into gnomad_af.db via the active registry."""
    engine = db_registry.gnomad_engine
    _create_gnomad_table(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO gnomad_af "
                "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_eas, af_eur) "
                "VALUES (:rsid, :chrom, :pos, :ref, :alt, :af_global, :af_afr, "
                ":af_amr, :af_eas, :af_eur)"
            ),
            [
                {
                    "rsid": "rsGnomad1",
                    "chrom": "17",
                    "pos": 41245466,
                    "ref": "A",
                    "alt": "G",
                    "af_global": 0.0123,
                    "af_afr": 0.001,
                    "af_amr": 0.002,
                    "af_eas": None,
                    "af_eur": 0.003,
                }
            ],
        )
    return db_registry


@pytest.fixture()
def sample_with_variants(test_client: TestClient) -> int:
    """Create a sample with raw variants and return its ID."""
    registry = get_registry()

    # Register sample in reference.db
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="Test Sample",
                db_path="samples/test_igv_sample.db",
                file_format="23andme",
            )
        )
        sample_id = result.lastrowid

    # Create per-sample DB
    sample_db_path = registry.settings.data_dir / "samples" / "test_igv_sample.db"
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    sample_engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(sample_engine)

    # Insert raw variants
    with sample_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": "rs100", "chrom": "17", "pos": 41245466, "genotype": "AG"},
                {"rsid": "rs101", "chrom": "17", "pos": 41245500, "genotype": "CC"},
                {"rsid": "rs102", "chrom": "17", "pos": 41246000, "genotype": "A"},
                {"rsid": "rs103", "chrom": "1", "pos": 50000, "genotype": "--"},
            ],
        )

    return sample_id


@pytest.fixture()
def sample_with_annotations(test_client: TestClient) -> int:
    """Create a sample whose variants are reference-resolved in annotated_variants.

    Each raw variant has a matching ``annotated_variants`` row carrying the true
    reference-aligned ``ref``/``alt`` and a resolved ``zygosity`` (as the real
    annotation engine writes via ``classify_zygosity``). This exercises the
    reference-aligned VCF path — the core fix for #471, where a homozygous-ALT
    array call (``CC`` vs reference ``T``) must show ``GT=1/1``, not a false
    ``0/0``.
    """
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="Annotated Sample",
                db_path="samples/test_igv_annotated.db",
                file_format="23andme",
            )
        )
        sample_id = result.lastrowid

    sample_db_path = registry.settings.data_dir / "samples" / "test_igv_annotated.db"
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    sample_engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(sample_engine)

    # rsid, chrom, pos, genotype, ref, alt, zygosity
    variants = [
        # Homozygous ALT vs reference T — the headline #471 case: observed CC
        # must NOT be encoded as reference-genome 0/0.
        ("rs200", "17", 41245466, "CC", "T", "C", "hom_alt"),
        # Heterozygous — REF/ALT follow biology (ref A, alt G), not string order.
        ("rs201", "17", 41245500, "AG", "A", "G", "het"),
        # True homozygous reference — correctly shown as 0/0.
        ("rs202", "17", 41246000, "GG", "G", "A", "hom_ref"),
        # Haploid homozygous ALT (e.g. male non-PAR X / Y / MT).
        ("rs203", "X", 2700000, "T", "A", "T", "hom_alt"),
    ]
    with sample_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": r, "chrom": c, "pos": p, "genotype": g}
                for (r, c, p, g, _ref, _alt, _zyg) in variants
            ],
        )
        conn.execute(
            annotated_variants.insert(),
            [
                {
                    "rsid": r,
                    "chrom": c,
                    "pos": p,
                    "genotype": g,
                    "ref": ref,
                    "alt": alt,
                    "zygosity": zyg,
                    "annotation_coverage": 0,
                }
                for (r, c, p, g, ref, alt, zyg) in variants
            ],
        )

    return sample_id


@pytest.mark.parametrize(
    ("path_template", "params"),
    [
        ("/api/igv-tracks/clinvar", {"chr": "chr17", "start": "inf", "end": "100"}),
        (
            "/api/igv-tracks/sample/{sample_id}/variants",
            {"chr": "chr17", "start": "0", "end": "nan"},
        ),
        ("/api/igv-tracks/gnomad", {"chr": "chr17", "start": "inf", "end": "100"}),
        ("/api/igv-tracks/encode-ccres", {"chr": "chr1", "start": "0", "end": "nan"}),
    ],
)
def test_track_regions_reject_non_finite_bounds(
    test_client: TestClient,
    sample_with_variants: int,
    path_template: str,
    params: dict[str, str],
) -> None:
    """Non-finite IGV bounds should return validation-style 422 responses."""
    path = path_template.format(sample_id=sample_with_variants)

    resp = test_client.get(path, params=params)

    assert resp.status_code == 422
    payload = resp.json()
    assert "detail" in payload
    assert payload["detail"]


# ── ClinVar VCF Track Tests ─────────────────────────────────────────


class TestClinVarTrack:
    """Tests for ClinVar VCF region and header endpoints."""

    def test_clinvar_header_returns_vcf(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/igv-tracks/clinvar/header")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        text = resp.text
        assert "##fileformat=VCFv4.2" in text
        assert "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO" in text

    @pytest.mark.usefixtures("_seed_clinvar")
    def test_clinvar_region_returns_variants(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/api/igv-tracks/clinvar",
            params={"chr": "chr17", "start": 41245400, "end": 41245600},
        )
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        data_lines = [line for line in lines if not line.startswith("#")]
        assert len(data_lines) == 2
        assert "rs123" in data_lines[0]
        assert "CLNSIG=Pathogenic" in data_lines[0]
        assert "chr17" in data_lines[0]
        assert "rs456" in data_lines[1]
        assert "CLNSIG=Benign" in data_lines[1]

    @pytest.mark.usefixtures("_seed_clinvar")
    def test_clinvar_region_normalizes_chrom(self, test_client: TestClient) -> None:
        """Requesting with or without 'chr' prefix should work."""
        resp = test_client.get(
            "/api/igv-tracks/clinvar",
            params={"chr": "17", "start": 41245400, "end": 41245600},
        )
        assert resp.status_code == 200
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 2

    @pytest.mark.usefixtures("_seed_clinvar")
    def test_clinvar_region_accepts_fractional_igv_bounds(self, test_client: TestClient) -> None:
        """IGV.js can emit floating-point bounds; the API floors/ceils them."""
        resp = test_client.get(
            "/api/igv-tracks/clinvar",
            params={"chr": "chr17", "start": 41245466.35, "end": 41245466.65},
        )
        assert resp.status_code == 200
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        assert "rs123" in data_lines[0]

    @pytest.mark.usefixtures("_seed_clinvar")
    def test_clinvar_region_empty_when_no_overlap(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/api/igv-tracks/clinvar",
            params={"chr": "chr17", "start": 1, "end": 100},
        )
        assert resp.status_code == 200
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 0

    @pytest.mark.usefixtures("_seed_clinvar")
    def test_clinvar_vcf_info_fields(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/api/igv-tracks/clinvar",
            params={"chr": "chr17", "start": 41245460, "end": 41245470},
        )
        text = resp.text
        assert "GENEINFO=BRCA1" in text
        assert "CLNACC=VCV000012345" in text
        assert "CLNDN=Breast cancer" in text
        assert "CLNREVSTAT=3" in text


# ── User Sample VCF Track Tests ──────────────────────────────────────


class TestSampleVariantsTrack:
    """Tests for user sample VCF region and header endpoints."""

    def test_sample_header_returns_vcf(
        self, test_client: TestClient, sample_with_variants: int
    ) -> None:
        resp = test_client.get(f"/api/igv-tracks/sample/{sample_with_variants}/header")
        assert resp.status_code == 200
        assert "##fileformat=VCFv4.2" in resp.text
        assert "FORMAT\tSAMPLE" in resp.text
        # Honesty metadata: reference build + the inferred-allele caveat (#471).
        assert "##reference=GRCh37" in resp.text
        assert "REF is set to N" in resp.text

    def test_sample_header_404_missing(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/igv-tracks/sample/9999/header")
        assert resp.status_code == 404

    def test_sample_region_returns_variants(
        self, test_client: TestClient, sample_with_variants: int
    ) -> None:
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_variants}/variants",
            params={"chr": "chr17", "start": 41245400, "end": 41246100},
        )
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        data_lines = [line for line in lines if not line.startswith("#")]
        assert len(data_lines) == 3  # rs100, rs101, rs102 on chr17

    def test_sample_region_accepts_fractional_igv_bounds(
        self, test_client: TestClient, sample_with_variants: int
    ) -> None:
        """Fractional IGV.js bounds should not 422 the sample VCF track."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_variants}/variants",
            params={"chr": "chr17", "start": 41245466.35, "end": 41245466.65},
        )
        assert resp.status_code == 200
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        assert "rs100" in data_lines[0]

    def test_sample_region_het_unannotated_fallback(
        self, test_client: TestClient, sample_with_variants: int
    ) -> None:
        """Unannotated AG -> honest fallback REF=N, ALT=A,G, GT=1/2 (no arbitrary REF)."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_variants}/variants",
            params={"chr": "chr17", "start": 41245466, "end": 41245467},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[3] == "N"  # REF — reference unknown, not an arbitrary allele
        assert fields[4] == "A,G"  # both observed bases as ALT
        assert fields[9] == "1/2"  # GT — neither observed allele assumed reference

    def test_sample_region_hom_unannotated_fallback(
        self, test_client: TestClient, sample_with_variants: int
    ) -> None:
        """Unannotated CC -> REF=N, ALT=C, GT=1/1 (never a false reference-genome 0/0)."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_variants}/variants",
            params={"chr": "chr17", "start": 41245500, "end": 41245501},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[3] == "N"  # REF — unknown
        assert fields[4] == "C"  # observed homozygous base as ALT
        assert fields[9] == "1/1"  # GT — carriage not hidden as 0/0

    def test_sample_region_haploid_unannotated_fallback(
        self, test_client: TestClient, sample_with_variants: int
    ) -> None:
        """Unannotated single-char call -> haploid ALT against REF=N."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_variants}/variants",
            params={"chr": "chr17", "start": 41246000, "end": 41246001},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[3] == "N"  # REF — unknown
        assert fields[4] == "A"  # observed base as ALT
        assert fields[9] == "1"  # haploid GT

    def test_sample_region_hom_alt_reference_aligned(
        self, test_client: TestClient, sample_with_annotations: int
    ) -> None:
        """#471 core fix: annotated hom-ALT (CC vs ref T) -> REF=T, ALT=C, GT=1/1."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_annotations}/variants",
            params={"chr": "chr17", "start": 41245466, "end": 41245467},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[3] == "T"  # true reference allele
        assert fields[4] == "C"  # alternate allele
        assert fields[9] == "1/1"  # homozygous alternate — NOT a false 0/0
        assert "OBS=CC" in fields[7]  # observed array call preserved as provenance

    def test_sample_region_het_reference_aligned(
        self, test_client: TestClient, sample_with_annotations: int
    ) -> None:
        """Annotated het -> reference-aligned REF/ALT (biology, not string order)."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_annotations}/variants",
            params={"chr": "chr17", "start": 41245500, "end": 41245501},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[3] == "A"  # REF
        assert fields[4] == "G"  # ALT
        assert fields[9] == "0/1"  # GT

    def test_sample_region_hom_ref_reference_aligned(
        self, test_client: TestClient, sample_with_annotations: int
    ) -> None:
        """Annotated true hom-ref -> correctly shown as 0/0 (reference match)."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_annotations}/variants",
            params={"chr": "chr17", "start": 41246000, "end": 41246001},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[3] == "G"  # REF
        assert fields[4] == "A"  # ALT
        assert fields[9] == "0/0"  # GT — genuine homozygous reference

    def test_sample_region_haploid_reference_aligned(
        self, test_client: TestClient, sample_with_annotations: int
    ) -> None:
        """Annotated haploid hom-ALT -> single-allele GT=1 against true REF."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_annotations}/variants",
            params={"chr": "chrX", "start": 2700000, "end": 2700001},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[3] == "A"  # REF
        assert fields[4] == "T"  # ALT
        assert fields[9] == "1"  # haploid alternate

    def test_sample_region_nocall_genotype(
        self, test_client: TestClient, sample_with_variants: int
    ) -> None:
        """'--' genotype -> no-call."""
        resp = test_client.get(
            f"/api/igv-tracks/sample/{sample_with_variants}/variants",
            params={"chr": "chr1", "start": 49999, "end": 50001},
        )
        data_lines = [line for line in resp.text.strip().split("\n") if not line.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields[9] == "./."  # No-call GT

    def test_sample_region_404_missing(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/api/igv-tracks/sample/9999/variants",
            params={"chr": "chr1", "start": 0, "end": 100},
        )
        assert resp.status_code == 404


# ── gnomAD AF Track Tests ────────────────────────────────────────────


class TestGnomadTrack:
    """Tests for gnomAD AF JSON features endpoint."""

    def test_gnomad_returns_empty_when_db_unavailable(self, test_client: TestClient) -> None:
        """When gnomAD DB doesn't exist, return empty array (not error)."""
        resp = test_client.get(
            "/api/igv-tracks/gnomad",
            params={"chr": "chr1", "start": 0, "end": 100000},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_gnomad_normalizes_chrom(self, test_client: TestClient) -> None:
        """Both 'chr1' and '1' should work."""
        resp1 = test_client.get(
            "/api/igv-tracks/gnomad",
            params={"chr": "chr1", "start": 0, "end": 100},
        )
        resp2 = test_client.get(
            "/api/igv-tracks/gnomad",
            params={"chr": "1", "start": 0, "end": 100},
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200

    def test_gnomad_accepts_fractional_igv_bounds(
        self,
        test_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        _seed_gnomad: DBRegistry,
    ) -> None:
        """Fractional IGV.js bounds should not 422 the gnomAD JSON track."""
        monkeypatch.setattr(igv_tracks_route, "get_registry", lambda: _seed_gnomad)

        resp = test_client.get(
            "/api/igv-tracks/gnomad",
            params={"chr": "chr17", "start": 41245464.35, "end": 41245465.35},
        )

        assert resp.status_code == 200
        assert [item["name"] for item in resp.json()] == ["rsGnomad1 AF=0.0123"]

    def test_gnomad_converts_vcf_pos_to_igv_feature_interval(
        self, monkeypatch: pytest.MonkeyPatch, _seed_gnomad: DBRegistry
    ) -> None:
        """VCF POS p should be emitted as the 0-based half-open interval [p-1, p)."""
        pos = 41245466
        monkeypatch.setattr(igv_tracks_route, "get_registry", lambda: _seed_gnomad)

        features = asyncio.run(igv_tracks_route.gnomad_region(chr="chr17", start=pos - 1, end=pos))

        assert [feature.model_dump() for feature in features] == [
            {
                "chr": "chr17",
                "start": pos - 1,
                "end": pos,
                "name": "rsGnomad1 AF=0.0123",
                "score": 0.0123,
                "af_global": 0.0123,
                "af_afr": 0.001,
                "af_amr": 0.002,
                "af_eas": None,
                "af_eur": 0.003,
            }
        ]

        shifted_right = asyncio.run(
            igv_tracks_route.gnomad_region(chr="chr17", start=pos, end=pos + 1)
        )
        assert shifted_right == []


# ── ENCODE cCREs Track Tests ─────────────────────────────────────────


class TestEncodeCcresTrack:
    """Tests for ENCODE cCREs JSON features endpoint."""

    def test_ccres_returns_empty_when_db_unavailable(self, test_client: TestClient) -> None:
        """When ENCODE cCREs DB is not loaded, return empty array."""
        resp = test_client.get(
            "/api/igv-tracks/encode-ccres",
            params={"chr": "chr1", "start": 0, "end": 100000},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ccres_accepts_fractional_igv_bounds(
        self,
        test_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        db_registry: DBRegistry,
    ) -> None:
        """Fractional IGV.js bounds should not 422 the ENCODE cCRE track."""
        observed: dict[str, int | str] = {}

        def fake_query(chrom: str, start: int, end: int, _engine: sa.Engine) -> list[CCREResult]:
            observed.update({"chrom": chrom, "start": start, "end": end})
            return [
                CCREResult(
                    accession="EH38E0000001",
                    chrom=chrom,
                    start_pos=start,
                    end_pos=end,
                    ccre_class="PLS",
                )
            ]

        monkeypatch.setattr(igv_tracks_route, "get_registry", lambda: db_registry)
        monkeypatch.setattr("backend.annotation.encode_ccres.is_loaded", lambda _engine: True)
        monkeypatch.setattr("backend.annotation.encode_ccres.query_ccres_by_region", fake_query)

        resp = test_client.get(
            "/api/igv-tracks/encode-ccres",
            params={"chr": "chr1", "start": 10400.35, "end": 10499.35},
        )

        assert resp.status_code == 200
        assert observed == {"chrom": "1", "start": 10400, "end": 10500}
        assert resp.json() == [
            {
                "chr": "chr1",
                "start": 10400,
                "end": 10500,
                "name": "EH38E0000001 (PLS)",
                "color": "rgb(255,0,0)",
            }
        ]


# ── Genotype conversion unit tests ───────────────────────────────────


class TestResolveVcfFields:
    """Unit tests for the _resolve_vcf_fields helper (#471)."""

    # ── Reference-aligned path (annotation-resolved ref/alt + zygosity) ──

    def test_hom_alt_reference_aligned(self) -> None:
        """Homozygous-ALT call (CC vs ref T) -> 1/1, never a false 0/0."""
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("CC", "T", "C", "hom_alt") == ("T", "C", "1/1")

    def test_het_reference_aligned(self) -> None:
        """Heterozygote uses biological REF/ALT, not raw allele-string order."""
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("AG", "A", "G", "het") == ("A", "G", "0/1")

    def test_hom_ref_reference_aligned(self) -> None:
        """True homozygous reference -> 0/0."""
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("GG", "G", "A", "hom_ref") == ("G", "A", "0/0")

    def test_haploid_hom_alt_reference_aligned(self) -> None:
        """Haploid homozygous-ALT -> single-allele GT=1."""
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("T", "A", "T", "hom_alt") == ("A", "T", "1")

    def test_haploid_hom_ref_reference_aligned(self) -> None:
        """Haploid homozygous-ref -> single-allele GT=0."""
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("A", "A", "T", "hom_ref") == ("A", "T", "0")

    # ── Honest fallback (reference allele unresolved) ──

    def test_hom_unannotated_fallback(self) -> None:
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("CC", None, None, None) == ("N", "C", "1/1")

    def test_het_unannotated_fallback(self) -> None:
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("AG", None, None, None) == ("N", "A,G", "1/2")

    def test_haploid_unannotated_fallback(self) -> None:
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("A", None, None, None) == ("N", "A", "1")

    def test_indeterminate_zygosity_falls_back(self) -> None:
        """ref/alt present but zygosity NULL (strand-ambiguous) -> honest fallback."""
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("CC", "T", "C", None) == ("N", "C", "1/1")

    def test_nocall(self) -> None:
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("--", None, None, None) == ("N", ".", "./.")

    def test_empty(self) -> None:
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("", None, None, None) == ("N", ".", "./.")

    def test_none(self) -> None:
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields(None, None, None, None) == ("N", ".", "./.")

    def test_non_nucleotide_call_is_unscoreable(self) -> None:
        """A stray single indel code (not a no-call sentinel) -> no-call output."""
        from backend.api.routes.igv_tracks import _resolve_vcf_fields

        assert _resolve_vcf_fields("I", None, None, None) == ("N", ".", "./.")


# ── Chromosome normalization tests ───────────────────────────────────


class TestChromNormalization:
    """Unit tests for _normalize_chrom helper."""

    def test_strips_chr_prefix(self) -> None:
        from backend.api.routes.igv_tracks import _normalize_chrom

        assert _normalize_chrom("chr17") == "17"
        assert _normalize_chrom("chrX") == "X"
        assert _normalize_chrom("chrMT") == "MT"

    def test_no_prefix_passthrough(self) -> None:
        from backend.api.routes.igv_tracks import _normalize_chrom

        assert _normalize_chrom("17") == "17"
        assert _normalize_chrom("X") == "X"
