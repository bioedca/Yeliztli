"""Rebuild/check the bounded DPYD investigation from retained public evidence.

Run ``python data/science-evidence/2026-11-08-dpyd-coverage-2455/audit.py --check``.
This validates artifact consistency; it does not validate clinical effects or
authorize installing an allele. No network or individual-level data is used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

PACKET = Path(__file__).resolve().parent
REPO = PACKET.parents[2]
SELECTED = {"c.868A>G", "c.1314T>G", "c.2279C>T"}
ALIASES = {"*2A": "c.1905+1G>A (*2A)", "*13": "c.1679T>G (*13)"}


def read(relative: str):
    return json.loads((PACKET / relative).read_text())


def build() -> list[dict]:
    alleles = read("raw/cpic-dpyd-alleles.json")
    definitions = {d["name"]: d for d in read("raw/cpic-dpyd-definitions.json")}
    locations = {v["id"]: v for v in read("raw/cpic-dpyd-locations.json")}
    diplotypes = read("raw/cpic-dpyd-diplotypes.json")
    assert len(alleles) == len(definitions) == 84
    assert len(diplotypes) == len({d["diplotype"] for d in diplotypes}) == 3570
    for q in read("cpic-queries.json"):
        rows = read(q["saved"])
        assert len(rows) == q["rows"] == sum(page["rows"] for page in q["pages"])
        assert all(int(page["content_range"].split("/")[1]) == q["rows"] for page in q["pages"])
    with (REPO / "backend/data/cpic/cpic_alleles.csv").open() as handle:
        bundled = {
            ALIASES.get(r["allele_name"], r["allele_name"]): r
            for r in csv.DictReader(handle)
            if r["gene"] == "DPYD" and r["allele_name"] != "*1"
        }
    assert len(bundled) == 5
    with (REPO / "backend/data/cpic/cpic_guidelines.csv").open() as handle:
        guidelines = {
            (r["drug"], r["phenotype"], r["activity_score"])
            for r in csv.DictReader(handle)
            if r["gene"] == "DPYD"
        }
    coverage = read("raw/gsa-probe-coverage.json")
    assert coverage["assay_rows"] == 654027
    frequency_queries = {r["rsid_label"]: r for r in read("gnomad-queries.json")}
    nonnormal = [
        a
        for a in alleles
        if a["clinicalfunctionalstatus"] in {"No function", "Decreased function"}
    ]
    assert len(nonnormal) == 28 and set(bundled) <= {a["name"] for a in nonnormal}
    result = []
    for allele in nonnormal:
        name = allele["name"]
        if name in bundled:
            continue
        definition = definitions[name]
        variants = []
        for value in definition["allele_location_value"]:
            if not value["variantallele"]:
                continue
            location = locations[value["locationid"]]
            rsid = location["dbsnpid"]
            ensembl_path = f"raw/ensembl37-{rsid}.json"
            vv_path = f"raw/variantvalidator37-{rsid}.json"
            mappings = [
                m
                for m in read(ensembl_path)["mappings"]
                if m["assembly_name"] == "GRCh37"
                and m["seq_region_name"] == "1"
                and m["strand"] == 1
            ]
            assert len(mappings) == 1
            mapping = mappings[0]
            projections = [
                v
                for v in read(vv_path).values()
                if isinstance(v, dict) and "grch37" in v.get("primary_assembly_loci", {})
            ]
            assert len(projections) == 1 and not projections[0]["validation_warnings"]
            projection = projections[0]
            vcf = projection["primary_assembly_loci"]["grch37"]["vcf"]
            snv = len(vcf["ref"]) == len(vcf["alt"]) == 1
            if snv:
                assert int(vcf["pos"]) == mapping["start"] == mapping["end"]
                assert vcf["ref"] == mapping["allele_string"].split("/")[0]
                assert vcf["alt"] in mapping["allele_string"].split("/")[1:]
                assert vcf["alt"] == value["variantallele"]
            hits = coverage["candidates"][rsid]
            for hit in hits:
                assert hit["GenomeBuild"] == "37" and (
                    hit["matches_rsid"] or hit["matches_coordinate"]
                )
                if hit["matches_coordinate"]:
                    assert hit["Chr"] == "1" and int(hit["MapInfo"]) == mapping["start"]
            query = frequency_queries.get(rsid)
            population = {
                "status": "not_retrieved",
                "dataset": "gnomad_r2_1",
                "interpretation": (
                    "No independent population conclusion established; "
                    "not a population-based priority ranking."
                ),
            }
            if query:
                population["query_status"] = query["ok"]
                population["source"] = query["saved"]
                if query["ok"]:
                    observed = read(query["saved"])["data"]["variant"]
                    assert observed and observed["variant_id"] == query["variant_id"]
                    assert (
                        observed["chrom"],
                        observed["pos"],
                        observed["ref"],
                        observed["alt"],
                    ) == (vcf["chr"], int(vcf["pos"]), vcf["ref"], vcf["alt"])
                    population["status"] = "aggregate_counts_available"
                    population["observations"] = {k: observed[k] for k in ("exome", "genome")}
                else:
                    population["status"] = (
                        "not_found"
                        if "Variant not found" in str(query["error"])
                        else "service_unavailable"
                    )
                    population["error"] = query["error"]
            variants.append(
                {
                    "rsid": rsid,
                    "coding_hgvs": projection["hgvs_transcript_variant"],
                    "cpic_location_name": location["name"],
                    "cpic_location_version": location["version"],
                    "cpic_variant_allele": value["variantallele"],
                    "grch37_vcf": vcf,
                    "ensembl_grch37": mapping,
                    "coordinate_status": "two_source_snv_agreement"
                    if snv
                    else "indel_normalization_and_vendor_polarity_require_release_validation",
                    "coordinate_sources": [ensembl_path, vv_path],
                    "gsa_probe_presence": bool(hits),
                    "gsa_hits": hits,
                    "vendor_custom_array_coverage": (
                        "unknown; public GSA backbone membership is not vendor-specific coverage"
                    ),
                    "population_evidence": population,
                }
            )
        assert variants
        rows = [d for d in diplotypes if name in d["diplotype"].split("/")]
        assert len(rows) == 84
        # Preserve CPIC rows; never infer a phenotype by summing allele values.
        required_partners = set(bundled) | {"Reference", name}
        selected_rows = [d for d in rows if set(d["diplotype"].split("/")) <= required_partners]
        assert len(selected_rows) == 7
        assert any(d["diplotype"].split("/").count(name) == 2 for d in selected_rows)
        assert any("Reference" in d["diplotype"].split("/") for d in selected_rows)
        assert all(
            (drug, d["generesult"], d["totalactivityscore"]) in guidelines
            for d in selected_rows
            for drug in ("fluorouracil", "capecitabine")
        )
        if len(variants) > 1:
            disposition = (
                "distinct_combination_overlapping_bundled_marker; "
                "do_not_duplicate_or_add_proxy_only_definition"
            )
        elif not all(v["gsa_probe_presence"] for v in variants):
            disposition = "defer; no_probe_found_in_audited_GSA_manifest; other_arrays_unknown"
        elif any(v["coordinate_status"] != "two_source_snv_agreement" for v in variants):
            disposition = "defer; indel_normalization_and_vendor_ID_polarity_not_release_validated"
        elif name in SELECTED:
            disposition = (
                "selected_small_followup_group; "
                "exact_vendor_coverage_and_2453_2454_release_gates_required"
            )
        else:
            disposition = (
                "defer_no_function_expansion; resolve_2453_and_2454_then_verify_vendor_coverage"
            )
        result.append(
            {
                "name": name,
                "function": allele["clinicalfunctionalstatus"],
                "activity_value": allele["activityvalue"],
                "cpic_definition_id": definition["id"],
                "cpic_cited_pmids": allele["citations"],
                "variants": variants,
                "cpic_diplotype_rows_available": len(rows),
                "diplotypes_with_reference_self_and_bundled_partners": selected_rows,
                "existing_fluorouracil_capecitabine_score_rows_cover_selected_diplotypes": True,
                "disposition": disposition,
                "release_ready": False,
            }
        )
    assert len(result) == 23
    assert sum(r["function"] == "No function" for r in result) == 19
    assert {r["name"] for r in result if r["name"] in SELECTED} == SELECTED
    return result


def main() -> None:
    if not __debug__:
        raise RuntimeError("Run without Python optimization")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    destination = PACKET / "dispositions.json"
    if args.check:
        assert destination.read_text() == rendered, "Disposition artifact drift"
        hashes = read("raw-hashes.json")
        assert hashes and set(hashes) == {
            str(p.relative_to(PACKET)) for p in (PACKET / "raw").glob("*") if p.is_file()
        }
        for path, digest in hashes.items():
            assert hashlib.sha256((PACKET / path).read_bytes()).hexdigest() == digest, path
        print(
            "Validated 23 dispositions, exact CPIC pagination, source mappings, "
            "probe membership, diplotype/guideline coverage and all retained raw hashes."
        )
    else:
        destination.write_text(rendered)


if __name__ == "__main__":
    main()
