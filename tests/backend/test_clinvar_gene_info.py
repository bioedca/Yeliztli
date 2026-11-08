"""Synthetic source-format and allele-identity controls for #2464."""

import pytest
import sqlalchemy as sa

from backend.annotation.clinvar import (
    gene_symbol_from_gene_info,
    lookup_clinvar_by_positions,
    lookup_clinvar_by_rsids,
    parse_clinvar_vcf_line,
)
from backend.db.tables import clinvar_variants, reference_metadata


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (".", None),
        ("YELIZTLIA:999001", "YELIZTLIA"),
        ("YELIZTLI-B:999002", "YELIZTLI-B"),
        ("YELIZTLIA:999001|YELIZTLIB:999002", None),
        ("YELIZTLIA:999001|YELIZTLIA:999001", None),
        ("YELIZTLIA", None),
        ("YELIZTLIA:", None),
        ("YELIZTLIA:0", None),
        ("YELIZTLIA:not-an-id", None),
        ("YELIZTLIA:999001:extra", None),
        (" YELIZTLIA:999001", None),
    ],
)
def test_gene_info_requires_one_complete_source_pair(value, expected):
    assert gene_symbol_from_gene_info(value) == expected


def test_parser_retains_multigene_source_without_selecting_first_gene():
    value = "YELIZTLIA:999001|YELIZTLIB:999002"
    record, skipped = parse_clinvar_vcf_line(f"1\t992464\t900002464\tA\tG\t.\t.\tGENEINFO={value}")
    assert skipped is None and record is not None
    assert record.gene_info == value
    assert record.gene_symbol is None


def test_stream_loader_keeps_complete_gene_info_for_later_reannotation(tmp_path):
    from backend.annotation.clinvar import iter_clinvar_vcf, load_clinvar_from_iter

    source = tmp_path / "synthetic.vcf"
    source.write_text(
        "##fileDate=20261101\n"
        "1\t992464\t900002464\tA\tG\t.\t.\tGENEINFO=YELIZTLIA:999001\n"
        "1\t992465\t900002465\tA\tT\t.\t.\tGENEINFO=YELIZTLIA:999001|YELIZTLIB:999002\n"
    )
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    loaded = load_clinvar_from_iter(iter_clinvar_vcf(source), engine)
    assert loaded.variants_loaded == 2
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(clinvar_variants.c.gene_symbol, clinvar_variants.c.gene_info).order_by(
                clinvar_variants.c.pos
            )
        ).all()
    assert rows == [
        ("YELIZTLIA", "YELIZTLIA:999001"),
        (None, "YELIZTLIA:999001|YELIZTLIB:999002"),
    ]
    engine.dispose()


def test_gene_context_cannot_override_explicit_vep_withholding():
    from backend.annotation.engine import _clinvar_gene_fallbacks

    source = {
        "rsYELIZTLI2464": {
            "clinvar_gene_info": "YELIZTLIA:999001",
            "clinvar_allele_identity": '["1",992464,"A","G"]',
        }
    }
    assert _clinvar_gene_fallbacks({}, source, set(), vep_lookup_complete=True) == {
        "rsYELIZTLI2464": {"gene_symbol": "YELIZTLIA", "gene_source": "clinvar"}
    }
    assert _clinvar_gene_fallbacks({}, source, {"rsYELIZTLI2464"}, vep_lookup_complete=True) == {}


@pytest.mark.parametrize("genotype", ["II", "ID", "DD", "--"])
def test_indel_probe_does_not_establish_a_single_gene_allele(genotype):
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert().values(
                rsid="rsYELIZTLI2464",
                chrom="1",
                pos=992464,
                ref="A",
                alt="ATG",
                gene_info="YELIZTLIA:999001",
            )
        )
    found = lookup_clinvar_by_rsids(
        ["rsYELIZTLI2464"], engine, genotype_by_rsid={"rsYELIZTLI2464": genotype}
    )
    assert len(found) == 1
    assert found["rsYELIZTLI2464"].clinvar_gene_info is None
    engine.dispose()


@pytest.mark.parametrize("by_position", [False, True])
@pytest.mark.parametrize(
    ("genotype", "expected"),
    [
        ("AG", "YELIZTLIA:999001"),
        ("GG", "YELIZTLIA:999001"),
        ("AC", "YELIZTLIB:999002"),
        ("AA", None),
        ("GC", None),
        ("AT", None),
        ("--", None),
    ],
)
def test_gene_info_never_follows_another_alt_or_review_star_ranking(
    by_position, genotype, expected
):
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    rsid = "rsYELIZTLI2464"
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert(),
            [
                {
                    "rsid": rsid,
                    "chrom": "1",
                    "pos": 992464,
                    "ref": "A",
                    "alt": alt,
                    "review_stars": stars,
                    "gene_info": value,
                }
                for alt, stars, value in [
                    ("G", 1, "YELIZTLIA:999001"),
                    ("C", 4, "YELIZTLIB:999002"),
                ]
            ],
        )
    if by_position:
        found = lookup_clinvar_by_positions(
            [("1", 992464, rsid)], engine, genotype_by_rsid={rsid: genotype}
        )
    else:
        found = lookup_clinvar_by_rsids([rsid], engine, genotype_by_rsid={rsid: genotype})
    assert len(found) == 1
    assert found[rsid].clinvar_gene_info == expected
    engine.dispose()


def test_reference_upgrade_keeps_legacy_gene_identity_unknown():
    from backend.db.reference_schema import ensure_reference_schema_current

    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert().values(
                chrom="1", pos=992464, ref="A", alt="G", gene_symbol="YELIZTLIA"
            )
        )
        conn.exec_driver_sql("ALTER TABLE clinvar_variants DROP COLUMN gene_info")
    assert ensure_reference_schema_current(engine)
    assert not ensure_reference_schema_current(engine)
    with engine.connect() as conn:
        assert conn.execute(
            sa.select(clinvar_variants.c.gene_symbol, clinvar_variants.c.gene_info)
        ).one() == ("YELIZTLIA", None)
    engine.dispose()


def test_sample_upgrade_preserves_legacy_gene_without_inventing_provenance():
    from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, ensure_sample_schema_current
    from backend.db.tables import sample_metadata_obj

    engine = sa.create_engine("sqlite://")
    sample_metadata_obj.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE annotated_variants DROP COLUMN gene_source")
        conn.exec_driver_sql("ALTER TABLE annotated_variants DROP COLUMN clinvar_gene_info")
        conn.exec_driver_sql(
            "INSERT INTO annotated_variants(rsid,chrom,pos,gene_symbol,inheritance_pattern) "
            "VALUES ('rsYELIZTLI2464','1',992464,'YELIZTLIA','Autosomal recessive')"
        )
        conn.exec_driver_sql("PRAGMA user_version=34")
    assert ensure_sample_schema_current(engine)
    assert not ensure_sample_schema_current(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == SAMPLE_SCHEMA_VERSION
        assert conn.exec_driver_sql(
            "SELECT gene_symbol,inheritance_pattern,gene_source,clinvar_gene_info "
            "FROM annotated_variants"
        ).one() == ("YELIZTLIA", "Autosomal recessive", None, None)
    engine.dispose()


@pytest.mark.parametrize("source", ["clinvar", "vep", None])
@pytest.mark.parametrize(
    "change",
    ["unchanged", "different_gene", "multi_gene", "missing", "removed", "different_allele"],
)
def test_source_refresh_reconciles_only_owned_gene_and_phenotype_context(source, change):
    from backend.annotation.allele_matching import source_allele_identity
    from backend.annotation.clinvar import annotate_sample_clinvar
    from backend.db.sample_schema import create_sample_tables
    from backend.db.tables import annotated_variants, raw_variants

    reference, sample = sa.create_engine("sqlite://"), sa.create_engine("sqlite://")
    reference_metadata.create_all(reference)
    create_sample_tables(sample)
    rsid = "rsYELIZTLI2464"
    original_info = "YELIZTLIA:999001"
    gene_info = {
        "different_gene": "YELIZTLIB:999002",
        "multi_gene": "YELIZTLIA:999001|YELIZTLIB:999002",
        "missing": None,
    }.get(change, original_info)
    with reference.begin() as conn:
        if change != "removed":
            conn.execute(
                clinvar_variants.insert().values(
                    rsid=rsid,
                    chrom="1",
                    pos=992464,
                    ref="A",
                    alt="C" if change == "different_allele" else "G",
                    gene_info=gene_info,
                )
            )
    with sample.begin() as conn:
        conn.execute(
            raw_variants.insert().values(
                rsid=rsid,
                chrom="1",
                pos=992464,
                genotype="AC" if change == "different_allele" else "AG",
            )
        )
        conn.execute(
            annotated_variants.insert().values(
                rsid=rsid,
                chrom="1",
                pos=992464,
                gene_symbol="YELIZTLIA",
                gene_source=source,
                clinvar_gene_info=original_info,
                clinvar_allele_identity=source_allele_identity("1", 992464, "A", "G"),
                disease_name="Synthetic condition",
                disease_id="MONDO:9002464",
                phenotype_source="mondo_hpo",
                hpo_terms='["HP:9002464"]',
                inheritance_pattern="Autosomal recessive",
                annotation_coverage=2 | 4 | 16,
            )
        )
    result = annotate_sample_clinvar(sample, reference)
    assert result.rows_written == int(change != "removed")
    with sample.connect() as conn:
        row = conn.execute(sa.select(annotated_variants)).one()
    retained = source != "clinvar" or change == "unchanged"
    assert row.gene_symbol == ("YELIZTLIA" if retained else None)
    assert row.gene_source == (source if retained else None)
    assert row.inheritance_pattern == ("Autosomal recessive" if retained else None)
    assert row.disease_id == ("MONDO:9002464" if retained else None)
    assert row.disease_name == ("Synthetic condition" if retained else None)
    assert row.hpo_terms == ('["HP:9002464"]' if retained else None)
    assert row.phenotype_source == ("mondo_hpo" if retained else None)
    assert row.clinvar_gene_info == (None if change == "removed" else gene_info)
    assert row.annotation_coverage == 4 | (16 if retained else 0) | (
        0 if change == "removed" else 2
    )
    sample.dispose()
    reference.dispose()
