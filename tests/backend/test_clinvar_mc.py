"""Synthetic MC parsing, allele selection and schema regression controls (#2393)."""

import pytest
import sqlalchemy as sa

from backend.annotation.clinvar import (
    iter_clinvar_vcf,
    load_clinvar_from_iter,
    lookup_clinvar_by_positions,
    lookup_clinvar_by_rsids,
    molecular_consequence_terms,
    parse_clinvar_vcf_line,
)
from backend.db.reference_schema import ensure_reference_schema_current
from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
from backend.db.tables import clinvar_variants, reference_metadata, sample_metadata_obj


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (".", None),
        ("", None),
        ("frameshift_variant", None),
        ("SO:0001589|frameshift_variant", "frameshift_variant"),
        (
            "SO:0001589|frameshift_variant,SO:0001627|intron_variant",
            "frameshift_variant&intron_variant",
        ),
        ("SO:0001589|frameshift_variant,SO:0001589|frameshift_variant", "frameshift_variant"),
        ("SO:0001589|frameshift_variant,bad", None),
        ("SO:0001589|frameshift_variant_extra|bad", None),
    ],
)
def test_decode_mc_source_terms(raw, expected):
    assert molecular_consequence_terms(raw) == expected


def test_multiallelic_vcf_does_not_assign_record_mc_to_first_alt():
    record, skipped = parse_clinvar_vcf_line(
        "1\t99001\t900000001\tA\tATG,AT\t.\t.\tRS=900000001;MC=SO:0001589|frameshift_variant"
    )
    assert skipped is None and record is not None
    assert record.molecular_consequence is None


def test_stream_load_retains_raw_mc(tmp_path):
    vcf = tmp_path / "synthetic.vcf"
    vcf.write_text(
        "##fileDate=20261107\n"
        "1\t99001\t900000001\tA\tATG\t.\t.\t"
        "RS=900000001;MC=SO:0001589|frameshift_variant\n"
    )
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    stats = load_clinvar_from_iter(iter_clinvar_vcf(vcf), engine)
    assert stats.variants_loaded == 1
    with engine.connect() as conn:
        row = conn.execute(sa.select(clinvar_variants)).one()
    assert (row.chrom, row.pos, row.ref, row.alt) == ("1", 99001, "A", "ATG")
    assert row.molecular_consequence == "SO:0001589|frameshift_variant"
    engine.dispose()


@pytest.mark.parametrize("by_position", [False, True])
@pytest.mark.parametrize(
    ("genotype", "expected"),
    [
        ("AG", "SO:0001587|stop_gained"),
        ("AC", "SO:0001583|missense_variant"),
        ("AA", None),
        ("GC", None),
        ("--", None),
    ],
)
def test_mc_does_not_cross_same_site_alts(by_position, genotype, expected):
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert(),
            [
                {
                    "rsid": "rs900000002",
                    "chrom": "1",
                    "pos": 99002,
                    "ref": "A",
                    "alt": "G",
                    "review_stars": 1,
                    "molecular_consequence": "SO:0001587|stop_gained",
                },
                {
                    "rsid": "rs900000002",
                    "chrom": "1",
                    "pos": 99002,
                    "ref": "A",
                    "alt": "C",
                    "review_stars": 4,
                    "molecular_consequence": "SO:0001583|missense_variant",
                },
            ],
        )
    if by_position:
        found = lookup_clinvar_by_positions(
            [("1", 99002, "i900000002")],
            engine,
            genotype_by_rsid={"i900000002": genotype},
        )
    else:
        found = lookup_clinvar_by_rsids(
            ["rs900000002"],
            engine,
            genotype_by_rsid={"rs900000002": genotype},
        )
    assert len(found) == 1
    assert next(iter(found.values())).clinvar_molecular_consequence == expected
    engine.dispose()


def test_reference_migration_preserves_unknown_mc_and_release():
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert().values(
                rsid="rs900000001",
                chrom="1",
                pos=99001,
                ref="A",
                alt="ATG",
            )
        )
        conn.exec_driver_sql("ALTER TABLE clinvar_variants DROP COLUMN molecular_consequence")
        conn.exec_driver_sql(
            "INSERT INTO database_versions(db_name,version) VALUES ('clinvar','20261107')"
        )
    assert ensure_reference_schema_current(engine)
    assert not ensure_reference_schema_current(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.select(clinvar_variants.c.molecular_consequence)).one() == (None,)
        assert (
            conn.exec_driver_sql("SELECT version FROM database_versions").scalar_one()
            == "20261107"
        )
    engine.dispose()


def test_sample_migration_does_not_invent_consequence_provenance():
    engine = sa.create_engine("sqlite://")
    sample_metadata_obj.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE annotated_variants DROP COLUMN consequence_source")
        conn.exec_driver_sql("ALTER TABLE annotated_variants DROP COLUMN vep_allele_identity")
        conn.exec_driver_sql("ALTER TABLE annotated_variants DROP COLUMN clinvar_allele_identity")
        conn.exec_driver_sql(
            "ALTER TABLE annotated_variants DROP COLUMN clinvar_molecular_consequence"
        )
        conn.exec_driver_sql(
            "INSERT INTO annotated_variants(rsid,chrom,pos,consequence) "
            "VALUES ('rs900000001','1',99001,'missense_variant')"
        )
        conn.exec_driver_sql("PRAGMA user_version=32")
    assert ensure_sample_schema_current(engine)
    assert not ensure_sample_schema_current(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == SAMPLE_SCHEMA_VERSION
        assert conn.exec_driver_sql(
            "SELECT consequence,consequence_source,clinvar_molecular_consequence,"
            "vep_allele_identity,clinvar_allele_identity "
            "FROM annotated_variants"
        ).one() == ("missense_variant", None, None, None, None)
    engine.dispose()


def test_standalone_refresh_clears_mc_when_gene_allele_no_longer_matches():
    from backend.annotation.clinvar import annotate_sample_clinvar
    from backend.db.sample_schema import create_sample_tables
    from backend.db.tables import annotated_variants, raw_variants

    reference = sa.create_engine("sqlite://")
    sample = sa.create_engine("sqlite://")
    reference_metadata.create_all(reference)
    create_sample_tables(sample)
    with reference.begin() as conn:
        conn.execute(
            clinvar_variants.insert().values(
                rsid="rs900000006",
                chrom="1",
                pos=99006,
                ref="A",
                alt="C",
                molecular_consequence="SO:0001587|stop_gained",
            )
        )
    with sample.begin() as conn:
        conn.execute(
            raw_variants.insert().values(
                rsid="rs900000006",
                chrom="1",
                pos=99006,
                genotype="AC",
            )
        )
        conn.execute(
            annotated_variants.insert().values(
                rsid="rs900000006",
                chrom="1",
                pos=99006,
                ref="A",
                alt="G",
                gene_symbol="PCSK9",
                consequence="frameshift_variant",
                consequence_source="clinvar",
                clinvar_molecular_consequence="SO:0001589|frameshift_variant",
            )
        )
    assert annotate_sample_clinvar(sample, reference).rows_written == 1
    with sample.connect() as conn:
        row = conn.execute(sa.select(annotated_variants)).one()
    assert row.alt == "C"
    assert row.consequence is None
    assert row.consequence_source is None
    sample.dispose()
    reference.dispose()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SO:0001587|nonsense", "stop_gained"),
        ("SO:0001623|5_prime_UTR_variant", "5_prime_UTR_variant"),
        ("SO:0001624|3_prime_UTR_variant", "3_prime_UTR_variant"),
        ("SO:0001619|non-coding_transcript_variant", "non_coding_transcript_variant"),
        ("SO:0001587|nonsense,SO:0001623|5_prime_UTR_variant", "stop_gained&5_prime_UTR_variant"),
    ],
)
def test_source_labels_normalize_without_discarding_valid_utr_terms(raw, expected):
    assert molecular_consequence_terms(raw) == expected
