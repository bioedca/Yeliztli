#!/usr/bin/env python3
"""Build and verify the exact Mavaddat 2015 breast-cancer PRS77 entry.

The committed source snapshots are intentionally small and offline. This
script validates their order, decimal-string projections, allele mapping, and
coordinate joins before replacing the disabled breast entry in
``cancer_prs_weights.json``. The prior 25-marker object remains nested as an
immutable audit record.

Usage::

    python scripts/build_breast_prs77.py --write
    python scripts/build_breast_prs77.py --check
"""

from __future__ import annotations

import argparse
import copy
import csv
import difflib
import hashlib
import io
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_PANEL = _REPO / "backend" / "data" / "panels" / "cancer_prs_weights.json"
_SOURCE_DIR = _REPO / "backend" / "data" / "sources" / "breast_prs77"
_SUPPLEMENT = _SOURCE_DIR / "mavaddat_2015_supplementary_table_4.tsv"
_HARMONIZED = _SOURCE_DIR / "pgs000001_harmonized_grch37.tsv"
_ENSEMBL = _SOURCE_DIR / "ensembl_primary_grch38_2026-07-16.json"

_EXPECTED_VARIANTS = 77
_EXPECTED_MULTIALLELIC = 39
_EXPECTED_GROUP_COUNTS = {
    "biallelic_forward_non_palindromic": 34,
    "biallelic_forward_palindromic": 2,
    "biallelic_reverse_complement_non_palindromic": 2,
    "multiallelic_forward_non_palindromic": 34,
    "multiallelic_forward_palindromic": 5,
}
_EXPECTED_FILE_SHA256 = {
    _SUPPLEMENT.name: "f08dac2d21dbbf14d2fe09f88de8d0b699e70b1a2eb5a9e02d0723c672ed80f8",
    _HARMONIZED.name: "7faab8eac749b1748c243105de6241bfc3c4bc5dc95bce54d1893d2e6cdd0221",
    _ENSEMBL.name: "7e212e701dfbb7d189155326683396785e6cd3018a4d0a1645650d067fda50b5",
}
_EXPECTED_SOURCE_PROJECTION_SHA256 = (
    "db7811c2e526e58a54524f5096c2af4bc92c4afa4cd114a45112c9208075059d"
)
_EXPECTED_GRCH37_PROJECTION_SHA256 = (
    "6e014bab4d8fcb147677492719f7bb8d919cd51efafa85ef66c877369ae55a85"
)
_EXPECTED_FULL_SOURCE_PROJECTION_SHA256 = (
    "d365caed0ebe7a8ae2d496ae5840ac47876f0577c5ebd07ab487684b4111bac9"
)
_EXPECTED_FULL_HARMONIZED_PROJECTION_SHA256 = (
    "9ee7f5435fbcc5a9312c56b08d0160869a3ad9189066785278c2731bd67b31bb"
)
_EXPECTED_ENSEMBL_RAW_SHA256 = "36597c0ab5d02c7ce373f671f87cd6630ae9df865b99c2e056770dd2eb78cbac"
_EXPECTED_LEGACY_HASHES = {
    "legacy_canonical_sha256": (
        "5c1e91302d638fa30ea325d27e561179e0f66f30ae181c65f3b51a9965e912a0"
    ),
    "legacy_weights_sha256": ("8923dc246e4dd702040a891b9b8d9caf1b99c880f39141b3d7730e113ef3ad93"),
    "legacy_ordered_projection_sha256": (
        "58eba9aff9a7bca7a25247d43332507f71d9d7474045312e7da0f82805f4f606"
    ),
}
_EXPECTED_LEGACY_RECORD_SHA256 = "01e47f0ca15eb6cb51774f31719f6bb79a836792744c85925f95f4c33ce29fe4"
_DNA = frozenset("ACGT")
_COMPLEMENT = str.maketrans("ACGT", "TGCA")


def _fail(message: str) -> None:
    raise ValueError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _projection_sha256(rows: list[list[str]]) -> str:
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True) + "\n"
    return _sha256_bytes(payload.encode())


def _canonical_sha256(value: object) -> str:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    return _sha256_bytes(payload.encode())


def _read_tsv(path: Path) -> list[dict[str, str]]:
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    return list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))


def _validate_file_hashes() -> None:
    for filename, expected in _EXPECTED_FILE_SHA256.items():
        observed = _sha256_file(_SOURCE_DIR / filename)
        if observed != expected:
            _fail(f"{filename}: SHA-256 {observed} != pinned {expected}")


def _validate_source_rows(rows: list[dict[str, str]]) -> None:
    if len(rows) != _EXPECTED_VARIANTS:
        _fail(f"supplement: expected 77 rows, found {len(rows)}")
    expected_fields = {
        "source_row",
        "rsid",
        "chromosome",
        "source_effect_allele",
        "source_other_allele",
        "pgs_effect_weight",
        "overall_or",
        "locus_name",
    }
    if set(rows[0]) != expected_fields:
        _fail(f"supplement: unexpected fields {sorted(rows[0])}")
    if [int(row["source_row"]) for row in rows] != list(range(1, 78)):
        _fail("supplement: source_row must be the ordered integers 1..77")
    rsids = [row["rsid"] for row in rows]
    if len(set(rsids)) != len(rsids):
        _fail("supplement: duplicate rsID")

    for row in rows:
        effect = row["source_effect_allele"]
        other = row["source_other_allele"]
        if effect not in _DNA or other not in _DNA or effect == other:
            _fail(f"{row['rsid']}: invalid source allele pair {other}/{effect}")
        published_or = float(row["overall_or"])
        weight = float(row["pgs_effect_weight"])
        if published_or <= 0 or not math.isclose(
            math.log(published_or), weight, rel_tol=0.0, abs_tol=5e-16
        ):
            _fail(f"{row['rsid']}: effect weight is not ln(overall OR)")

    projection = [
        [
            row["rsid"],
            row["source_effect_allele"],
            row["source_other_allele"],
            row["pgs_effect_weight"],
        ]
        for row in rows
    ]
    observed = _projection_sha256(projection)
    if observed != _EXPECTED_SOURCE_PROJECTION_SHA256:
        _fail(f"supplement: ordered projection SHA-256 {observed} is not pinned value")

    full_projection = [
        [
            row["rsid"],
            row["chromosome"],
            row["source_effect_allele"],
            row["source_other_allele"],
            row["pgs_effect_weight"],
            row["locus_name"],
            row["overall_or"],
        ]
        for row in rows
    ]
    observed = _projection_sha256(full_projection)
    if observed != _EXPECTED_FULL_SOURCE_PROJECTION_SHA256:
        _fail(f"supplement: full PGS projection SHA-256 {observed} is not pinned value")


def _validate_harmonized_rows(
    source_rows: list[dict[str, str]], harmonized_rows: list[dict[str, str]]
) -> dict[str, dict[str, str]]:
    if len(harmonized_rows) != _EXPECTED_VARIANTS:
        _fail(f"harmonized snapshot: expected 77 rows, found {len(harmonized_rows)}")
    expected_fields = {"rsid", "hm_source", "hm_rsid", "hm_chr", "hm_pos"}
    if set(harmonized_rows[0]) != expected_fields:
        _fail(f"harmonized snapshot: unexpected fields {sorted(harmonized_rows[0])}")
    if [row["rsid"] for row in harmonized_rows] != [row["rsid"] for row in source_rows]:
        _fail("harmonized snapshot: rsIDs are not in supplement order")

    by_rsid: dict[str, dict[str, str]] = {}
    for source, harmonized in zip(source_rows, harmonized_rows, strict=True):
        rsid = harmonized["rsid"]
        if rsid in by_rsid:
            _fail(f"harmonized snapshot: duplicate {rsid}")
        if harmonized["hm_source"] != "ENSEMBL" or harmonized["hm_rsid"] != rsid:
            _fail(f"{rsid}: unexpected PGS harmonization identity")
        if harmonized["hm_chr"] != source["chromosome"]:
            _fail(f"{rsid}: source and harmonized chromosome disagree")
        if int(harmonized["hm_pos"]) <= 0:
            _fail(f"{rsid}: invalid GRCh37 position")
        by_rsid[rsid] = harmonized

    projection = [
        [
            source["rsid"],
            by_rsid[source["rsid"]]["hm_chr"],
            by_rsid[source["rsid"]]["hm_pos"],
            source["source_effect_allele"],
            source["source_other_allele"],
            source["pgs_effect_weight"],
        ]
        for source in source_rows
    ]
    observed = _projection_sha256(projection)
    if observed != _EXPECTED_GRCH37_PROJECTION_SHA256:
        _fail(f"harmonized snapshot: ordered projection SHA-256 {observed} is not pinned value")

    full_projection = [
        [
            source["rsid"],
            source["chromosome"],
            source["source_effect_allele"],
            source["source_other_allele"],
            source["pgs_effect_weight"],
            source["locus_name"],
            source["overall_or"],
            by_rsid[source["rsid"]]["hm_source"],
            by_rsid[source["rsid"]]["hm_rsid"],
            by_rsid[source["rsid"]]["hm_chr"],
            by_rsid[source["rsid"]]["hm_pos"],
            "",
        ]
        for source in source_rows
    ]
    observed = _projection_sha256(full_projection)
    if observed != _EXPECTED_FULL_HARMONIZED_PROJECTION_SHA256:
        _fail(f"harmonized snapshot: full PGS projection SHA-256 {observed} is not pinned value")
    return by_rsid


def _parse_location(rsid: str, location: object) -> tuple[str, int]:
    if not isinstance(location, str) or ":" not in location:
        _fail(f"{rsid}: invalid Ensembl location")
    chrom, interval = location.split(":", 1)
    start_text, _, end_text = interval.partition("-")
    start = int(start_text)
    end = int(end_text or start_text)
    if start <= 0 or start != end:
        _fail(f"{rsid}: expected one-base Ensembl location, found {location}")
    return chrom, start


def _validate_ensembl_snapshot(
    source_rows: list[dict[str, str]], data: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if data.get("assembly_name") != "GRCh38" or data.get("checked_on") != "2026-07-16":
        _fail("Ensembl snapshot: unexpected assembly or access date")
    if data.get("coord_system") != "chromosome":
        _fail("Ensembl snapshot: mappings are not restricted to chromosomes")
    if data.get("raw_payload_sha256") != _EXPECTED_ENSEMBL_RAW_SHA256:
        _fail("Ensembl snapshot: raw response-set SHA-256 changed")
    records = data.get("records")
    if not isinstance(records, list) or len(records) != _EXPECTED_VARIANTS:
        _fail("Ensembl snapshot: expected 77 records")

    by_rsid: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("rsid"), str):
            _fail("Ensembl snapshot: malformed record")
        rsid = record["rsid"]
        if rsid in by_rsid:
            _fail(f"Ensembl snapshot: duplicate {rsid}")
        alleles = record.get("alleles")
        if (
            not isinstance(alleles, list)
            or len(alleles) < 2
            or len(set(alleles)) != len(alleles)
            or any(allele not in _DNA for allele in alleles)
        ):
            _fail(f"{rsid}: invalid current primary allele set")
        if alleles != sorted(alleles):
            _fail(f"{rsid}: current allele set is not deterministically normalized")
        if record.get("strand") != 1:
            _fail(f"{rsid}: primary Ensembl mapping is not forward-strand")
        _parse_location(rsid, record.get("location"))
        by_rsid[rsid] = record

    source_rsids = {row["rsid"] for row in source_rows}
    if set(by_rsid) != source_rsids:
        _fail("Ensembl snapshot: rsID set differs from supplement")
    if sum(len(record["alleles"]) > 2 for record in records) != _EXPECTED_MULTIALLELIC:
        _fail("Ensembl snapshot: expected exactly 39 multiallelic primary loci")
    return by_rsid


def _is_palindromic(effect: str, other: str) -> bool:
    return {effect, other} == {effect.translate(_COMPLEMENT), other.translate(_COMPLEMENT)}


def _build_weights(
    source_rows: list[dict[str, str]],
    harmonized: dict[str, dict[str, str]],
    ensembl: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    weights: list[dict[str, Any]] = []
    groups: Counter[str] = Counter()
    for source in source_rows:
        rsid = source["rsid"]
        effect = source["source_effect_allele"]
        other = source["source_other_allele"]
        current = ensembl[rsid]
        current_alleles = set(current["alleles"])
        source_pair = {effect, other}
        complement_pair = {
            effect.translate(_COMPLEMENT),
            other.translate(_COMPLEMENT),
        }
        if source_pair <= current_alleles:
            orientation = "forward"
        elif complement_pair <= current_alleles:
            orientation = "reverse_complement"
        else:
            _fail(f"{rsid}: source alleles cannot be reconciled to primary GRCh38 record")

        multiallelic = len(current_alleles) > 2
        palindromic = _is_palindromic(effect, other)
        orientation_ambiguous = (
            multiallelic
            and not palindromic
            and source_pair <= current_alleles
            and complement_pair <= current_alleles
        )
        group = "_".join(
            (
                "multiallelic" if multiallelic else "biallelic",
                orientation,
                "palindromic" if palindromic else "non_palindromic",
            )
        )
        groups[group] += 1
        grch38_chrom, grch38_pos = _parse_location(rsid, current["location"])

        weight: dict[str, Any] = {
            "rsid": rsid,
            "effect_allele": effect,
        }
        if not multiallelic:
            weight["other_allele"] = other
        weight.update(
            {
                "weight": float(source["pgs_effect_weight"]),
                "chrom": harmonized[rsid]["hm_chr"],
                "pos": int(harmonized[rsid]["hm_pos"]),
                "source_row": int(source["source_row"]),
                "source_marker": rsid,
                "source_locus_name": source["locus_name"],
                "source_effect_allele": effect,
                "source_other_allele": other,
                "source_or": float(source["overall_or"]),
                "source_build": "NR",
                "source_strand": "NR",
                "weight_transformation": "natural_log_of_source_or",
                "grch38_chrom": grch38_chrom,
                "grch38_pos": grch38_pos,
                "grch38_current_alleles": current["alleles"],
                "source_to_current_orientation": orientation,
                "orientation_ambiguous_in_current_multiallelic_set": orientation_ambiguous,
                "allele_mapping_class": group,
                "runtime_scoring_eligible": not multiallelic,
                "imputation_eligible": not multiallelic and not palindromic,
            }
        )
        weights.append(weight)

    observed_groups = dict(sorted(groups.items()))
    if observed_groups != _EXPECTED_GROUP_COUNTS:
        _fail(f"unexpected allele-mapping groups: {observed_groups}")
    ambiguous_count = sum(
        weight["orientation_ambiguous_in_current_multiallelic_set"] for weight in weights
    )
    if ambiguous_count != 11:
        _fail(f"expected 11 multiallelic orientation ambiguities, found {ambiguous_count}")
    return weights, observed_groups


def _legacy_audit_record(current_breast: dict[str, Any]) -> dict[str, Any]:
    nested = current_breast.get("legacy_audit_record")
    legacy = copy.deepcopy(nested if isinstance(nested, dict) else current_breast)
    if legacy.get("model_status") != "quarantined_source_unverified":
        _fail("breast legacy audit record is missing or has unexpected model status")
    if len(legacy.get("weights", [])) != 25:
        _fail("breast legacy audit record must retain exactly 25 rows")
    legacy_object = dict(legacy.get("legacy_canonical_metadata", {}))
    legacy_object["weights"] = legacy["weights"]
    ordered_projection = [
        [
            weight["rsid"],
            weight["effect_allele"],
            weight.get("other_allele"),
            weight["weight"],
        ]
        for weight in legacy["weights"]
    ]
    representations = {
        "legacy_canonical_sha256": legacy_object,
        "legacy_weights_sha256": legacy["weights"],
        "legacy_ordered_projection_sha256": ordered_projection,
    }
    for field, representation in representations.items():
        expected = _EXPECTED_LEGACY_HASHES[field]
        if legacy.get(field) != expected or _canonical_sha256(representation) != expected:
            _fail(f"breast legacy audit record failed pinned {field} verification")
    if _canonical_sha256(legacy) != _EXPECTED_LEGACY_RECORD_SHA256:
        _fail("breast legacy audit record failed whole-record verification")
    return legacy


def _build_breast_model(
    weights: list[dict[str, Any]],
    groups: dict[str, int],
    legacy_audit_record: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": "Breast cancer (Mavaddat PRS77; runtime blocked)",
        "trait": "breast_cancer",
        "source_ancestry": "EUR",
        "source_study": "Mavaddat et al. 2015, Supplementary Table 4",
        "source_pmid": "25855707",
        "sample_size": 67054,
        "reference_mean": 0.0,
        "reference_std": 1.0,
        "calibrated": False,
        "model_status": "source_verified_runtime_blocked",
        "scoring_enabled": False,
        "calibration_eligible": False,
        "disabled_reason": (
            "The exact published PRS77 is reproducible, but 39 primary GRCh38 loci are "
            "currently multiallelic. The sample schema does not preserve trusted canonical "
            "allele-set/full multi-ALT dosage context, so a third allele can be mistaken for "
            "a strand flip. Scoring remains fail-closed until that runtime blocker is resolved."
        ),
        "calibration_note": (
            "No source-validated population reference distribution is bundled for this exact "
            "implementation. While runtime scoring is blocked, it must not emit a raw score, "
            "percentile, z-score, absolute risk, or interval."
        ),
        "pgs_id": "PGS000001",
        "pgs_license": None,
        "license_basis": "Mavaddat 2015 article and supplement, CC BY 3.0",
        "development_method": (
            "Sum of effect-allele dosages weighted by the natural logarithm of the published "
            "overall per-allele odds ratio for 77 susceptibility variants"
        ),
        "genome_build": "GRCh37",
        "variants_number": 77,
        "source_url": "https://doi.org/10.1093/jnci/djv036",
        "model_provenance": {
            "model_label": "PRS77_BC",
            "publication_doi": "10.1093/jnci/djv036",
            "publication_pmcid": "PMC4754625",
            "outcome": "overall breast cancer",
            "coefficient_scale": "natural logarithm of overall per-allele odds ratio",
            "published_source": "Supplementary Table 4; second listed allele",
            "published_source_build": "NR",
            "published_source_strand": "NR",
            "evaluation_sample": {"cases": 33673, "controls": 33381, "total": 67054},
            "primary_icogs_sample_size": 89049,
            "variant_source_study": {
                "accession": "GCST001937",
                "pmid": "23535729",
                "eur_sample_size": 22627,
            },
            "harmonization": {
                "source": "PGS Catalog PGS000001 ENSEMBL harmonization",
                "assembly": "GRCh37",
                "snapshot_date": "2022-09-23",
            },
            "current_allele_audit": {
                "source": "Ensembl Variation",
                "assembly": "GRCh38",
                "checked_on": "2026-07-16",
                "mapping_policy": "primary chromosome only; alternate scaffolds excluded",
                "mapping_group_counts": groups,
                "multiallelic_primary_loci": 39,
                "source_and_complement_present_at_multiallelic_loci": 11,
                "activation_blockers": [
                    "trusted canonical genotype allele-set context",
                    "complete multi-ALT dosage vectors for imputation",
                ],
            },
            "reproducibility": {
                "generator": "scripts/build_breast_prs77.py",
                "checked_in_snapshot_sha256": _EXPECTED_FILE_SHA256,
                "source_ordered_projection_sha256": _EXPECTED_SOURCE_PROJECTION_SHA256,
                "grch37_ordered_projection_sha256": _EXPECTED_GRCH37_PROJECTION_SHA256,
                "pgs_source_full_projection_sha256": _EXPECTED_FULL_SOURCE_PROJECTION_SHA256,
                "pgs_harmonized_full_projection_sha256": (
                    _EXPECTED_FULL_HARMONIZED_PROJECTION_SHA256
                ),
                "source_artifact_sha256": {
                    "supplement_docx": (
                        "83086cd11c67dc01bd606539d4d894f2c16f6a1e6182ee54aa916031ab4c8c3e"
                    ),
                    "supplement_archive": (
                        "617952a5528414959dd07e1d5687ab606b1d62d7e88883df2d81995e6c486792"
                    ),
                    "pgs_source_gzip": (
                        "ccccbb2af5579cc284bfea95a9e5845bf39426f8c71f701ddc2f4197016c6277"
                    ),
                    "pgs_source_text": (
                        "a5e5adddb922531789b9f18f4b2eb7ba7f68dc1e99d643c7bc829d10aebc7173"
                    ),
                    "pgs_grch37_gzip": (
                        "5a3069e1a1844df02f3d24e889f97dfad03ee0c40888fc5ecf5f44dc5c36ffc3"
                    ),
                    "pgs_grch37_text": (
                        "1adafcf2ba083cde9616f416281fed7fbaf0e3a833b91c154abdfe96c96a5ea0"
                    ),
                    "ensembl_raw_records": _EXPECTED_ENSEMBL_RAW_SHA256,
                },
            },
        },
        "monogenic_genes": [
            "BRCA1",
            "BRCA2",
            "PALB2",
            "ATM",
            "CHEK2",
            "TP53",
            "PTEN",
            "CDH1",
            "STK11",
        ],
        "legacy_audit_record_sha256": _EXPECTED_LEGACY_RECORD_SHA256,
        "legacy_audit_record": legacy_audit_record,
        "weights": weights,
    }


def _expected_panel(panel: dict[str, Any]) -> dict[str, Any]:
    weight_sets = panel.get("weight_sets")
    if not isinstance(weight_sets, list) or not weight_sets:
        _fail("panel: missing non-empty weight_sets")
    if len(weight_sets) != 4:
        _fail(f"panel: expected exactly four top-level models, found {len(weight_sets)}")
    traits = [weight_set.get("trait") for weight_set in weight_sets]
    if len(set(traits)) != len(traits):
        _fail("panel: duplicate top-level trait")
    breast_indexes = [index for index, trait in enumerate(traits) if trait == "breast_cancer"]
    if len(breast_indexes) != 1:
        _fail("panel: expected exactly one top-level breast_cancer model")

    _validate_file_hashes()
    source_rows = _read_tsv(_SUPPLEMENT)
    harmonized_rows = _read_tsv(_HARMONIZED)
    _validate_source_rows(source_rows)
    harmonized = _validate_harmonized_rows(source_rows, harmonized_rows)
    ensembl_data = json.loads(_ENSEMBL.read_text(encoding="utf-8"))
    ensembl = _validate_ensembl_snapshot(source_rows, ensembl_data)
    weights, groups = _build_weights(source_rows, harmonized, ensembl)

    result = copy.deepcopy(panel)
    result["version"] = "1.2.0"
    result["description"] = (
        "Cancer PRS weight sets for breast, prostate, colorectal, and melanoma. "
        "The exact published breast PRS77 is source-verified but remains non-reporting "
        "because multiallelic runtime harmonization is unresolved; its prior 25-marker "
        "quarantine is retained as a nested audit record. Active models carry calibrated "
        "and calibration_eligible gates: without a validated reference distribution, the "
        "engine withholds percentile, z-score, and interval outputs. Raw PRS and absolute "
        "lifetime risk are never displayed."
    )
    breast_index = breast_indexes[0]
    legacy = _legacy_audit_record(weight_sets[breast_index])
    result["weight_sets"][breast_index] = _build_breast_model(weights, groups, legacy)
    return result


def _render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the generated panel entry")
    mode.add_argument("--check", action="store_true", help="verify the committed panel entry")
    parser.add_argument("--panel", type=Path, default=_PANEL, help="panel JSON path")
    args = parser.parse_args()

    panel = json.loads(args.panel.read_text(encoding="utf-8"))
    expected = _expected_panel(panel)
    rendered = _render(expected)
    if args.write:
        args.panel.write_text(rendered, encoding="utf-8")
        try:
            display_path = args.panel.resolve().relative_to(_REPO)
        except ValueError:
            display_path = args.panel
        print(f"Wrote exact PRS77 model to {display_path}", file=sys.stderr)
        return 0

    observed = args.panel.read_text(encoding="utf-8")
    if observed != rendered:
        diff = difflib.unified_diff(
            observed.splitlines(),
            rendered.splitlines(),
            fromfile=str(args.panel),
            tofile="generated expected panel",
            lineterm="",
        )
        print("\n".join(diff), file=sys.stderr)
        print("ERROR: breast PRS77 panel entry is not generated output", file=sys.stderr)
        return 1
    print("Breast PRS77 panel entry and source snapshots verified", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
