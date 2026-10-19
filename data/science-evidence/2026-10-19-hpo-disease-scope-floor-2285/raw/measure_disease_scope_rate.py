"""Measure the MONDO/HPO disease-scope match rate behind #2285's floor.

The loader's own parsers and join (``backend.annotation.mondo_hpo``) are run on
locally downloaded copies of the three public exports, so the rate measured here
is the one the refresh guard computes. Nothing is fetched by this script.

Usage (from a checkout of the repository):

    python <this packet>/raw/measure_disease_scope_rate.py \
        <snapshot-sources.json> LABEL=DIR [LABEL=DIR ...] > measurements.json

Each DIR holds ``gene_disease.9606.tsv.gz``, ``genes_to_phenotype.txt`` and
``mondo.sssom.tsv`` downloaded from the URLs that ``snapshot-sources.json``
records for LABEL.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from backend.annotation import mondo_hpo  # noqa: E402

SOURCE_FILES = ("gene_disease.9606.tsv.gz", "genes_to_phenotype.txt", "mondo.sssom.tsv")
TRUNCATION_SEED = 2285
TRUNCATION_KEEP_FRACTIONS = (0.75, 0.6, 0.5)


def _file_identity(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _rate(records_by_gene, hpo_data, xrefs) -> dict[str, object]:
    matches, unmatched = mondo_hpo._count_scoped_hpo_matches(records_by_gene, hpo_data, xrefs)
    total = matches + unmatched
    return {
        "unambiguous_exact_xrefs": len(xrefs),
        "hpo_disease_matches": matches,
        "hpo_disease_unmatched": unmatched,
        "match_percent": round(100 * matches / total, 2) if total else None,
    }


def _measure(directory: Path, *, simulate: bool) -> dict[str, object]:
    with gzip.open(directory / SOURCE_FILES[0], "rt", encoding="utf-8") as fh:
        records_by_gene, stats = mondo_hpo._parse_mondo_gene_disease_tsv_reader(fh)
    with open(directory / SOURCE_FILES[1], encoding="utf-8") as fh:
        hpo_data = mondo_hpo._parse_hpo_genes_to_phenotype_reader(fh)
    with open(directory / SOURCE_FILES[2], encoding="utf-8") as fh:
        sssom_lines = fh.readlines()
    xrefs = mondo_hpo._parse_mondo_sssom_reader(iter(sssom_lines))

    reasons: Counter[str] = Counter()
    by_namespace: dict[str, Counter[str]] = {}
    for gene, scopes in hpo_data.items():
        mondo_ids = {record.disease_id for record in records_by_gene.get(gene, [])}
        for source_disease_id in scopes:
            namespace = by_namespace.setdefault(source_disease_id.split(":", 1)[0], Counter())
            namespace["total"] += 1
            target = mondo_hpo._resolve_source_disease_to_mondo(source_disease_id, xrefs)
            if target in mondo_ids:
                namespace["matched"] += 1
            elif target is None:
                reasons["no_unambiguous_exact_xref"] += 1
            elif not mondo_ids:
                reasons["gene_has_no_monarch_rows"] += 1
            else:
                reasons["xref_target_not_a_monarch_disease_for_the_gene"] += 1

    rows = mondo_hpo._records_to_rows(records_by_gene, hpo_data, xrefs)
    result: dict[str, object] = {
        "files": {name: _file_identity(directory / name) for name in SOURCE_FILES},
        "monarch_lines": stats.total_lines,
        "monarch_records": sum(len(records) for records in records_by_gene.values()),
        "hpo_genes": len(hpo_data),
        "hpo_gene_disease_scopes": sum(len(scopes) for scopes in hpo_data.values()),
        **_rate(records_by_gene, hpo_data, xrefs),
        "unmatched_reasons": dict(sorted(reasons.items())),
        "scopes_by_namespace": {
            namespace: {"total": counts["total"], "matched": counts["matched"]}
            for namespace, counts in sorted(by_namespace.items())
        },
        "rows_written": len(rows),
        "rows_with_hpo_terms": sum(1 for row in rows if row["hpo_terms"]),
        "simulated_sssom_without_omim": _rate(
            records_by_gene,
            hpo_data,
            {key: value for key, value in xrefs.items() if not key.startswith("OMIM:")},
        ),
    }
    if not simulate:
        return result

    simulations: dict[str, object] = {
        "sssom_without_orphanet": _rate(
            records_by_gene,
            hpo_data,
            {key: value for key, value in xrefs.items() if not key.startswith("Orphanet:")},
        ),
        "sssom_without_omim_and_orphanet": _rate(
            records_by_gene,
            hpo_data,
            {
                key: value
                for key, value in xrefs.items()
                if not key.startswith(("OMIM:", "Orphanet:"))
            },
        ),
        "hpo_omim_prefix_renamed_to_mim": _rate(
            records_by_gene,
            {
                gene: {
                    ("MIM:" + scope[len("OMIM:") :] if scope.startswith("OMIM:") else scope): data
                    for scope, data in scopes.items()
                }
                for gene, scopes in hpo_data.items()
            },
            xrefs,
        ),
    }
    header = [line for line in sssom_lines if line.startswith("#")]
    body = [line for line in sssom_lines if not line.startswith("#")]
    columns, mapping_rows = body[0], body[1:]
    rng = random.Random(TRUNCATION_SEED)
    for keep in TRUNCATION_KEEP_FRACTIONS:
        kept = [line for line in mapping_rows if rng.random() < keep]
        simulations[f"sssom_random_rows_kept_{keep}"] = _rate(
            records_by_gene,
            hpo_data,
            mondo_hpo._parse_mondo_sssom_reader(iter(header + [columns] + kept)),
        )
    result["simulations"] = simulations
    return result


def main(argv: list[str]) -> None:
    sources = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    directories = dict(argument.split("=", 1) for argument in argv[2:])
    snapshots = sources["snapshots"]
    latest = snapshots[-1]["label"]
    measured = []
    for snapshot in snapshots:
        label = snapshot["label"]
        measured.append(
            {
                "label": label,
                **_measure(Path(directories[label]), simulate=label == latest),
            }
        )
    print(
        json.dumps(
            {
                "measured_with": (
                    "backend.annotation.mondo_hpo parsers and _count_scoped_hpo_matches"
                ),
                "truncation_seed": TRUNCATION_SEED,
                "snapshots": measured,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main(sys.argv)
