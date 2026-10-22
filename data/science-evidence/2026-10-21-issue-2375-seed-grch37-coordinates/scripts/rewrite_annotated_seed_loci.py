#!/usr/bin/env python3
"""Place the annotated seed rsIDs at their GRCh37 locus and alleles (#2375).

Scope: the rsIDs annotated in vep_seed.csv whose seed rows are not at their Ensembl GRCh37
primary mapping (derived/seed_row_comparison.json.gz), in vep_seed, clinvar_seed and
gnomad_seed.  The rsID is the row's identity, so each row takes its rsID's locus:

* rsIDs the pinned production VEP bundle carries take a bundle row's (chrom, pos, ref, alt)
  from tests/fixtures/vep_seed_bundle_snapshot.json, preferring the seed's own allele pair,
  then its complement, then either of those reversed (the assemblies' reference bases differ),
  then the bundle's first canonical (mane_select=1) row.  Every chosen bundle locus equals the rsID's Ensembl GRCh37 mapping.
* rs5030655 (CYP2D6*6, Ensembl 22:42525086 A/-), absent from the bundle, takes the VCF form
  of that deletion, 22:42525085 CA>C; the padding base C is GRCh37 assembly sequence at
  22:42525085 (raw/ensembl_sequence/rs5030655_padding.json).

Only the leading ``rsid,chrom,pos,ref,alt`` text of each affected line is replaced, so the
rest of every line is byte-identical.  Writes derived/annotated_rewrite_plan.json.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
ROOT = PACKET.parents[2]
SEED_DIR = ROOT / "tests" / "fixtures" / "seed_csvs"
SEEDS = ("vep_seed", "clinvar_seed", "gnomad_seed")
SNAPSHOT = ROOT / "tests" / "fixtures" / "vep_seed_bundle_snapshot.json"
OUT_OF_SCOPE = {"rs12345"}
COMPLEMENT = str.maketrans("ACGT", "TGCA")
DELETION_VCF = {"rs5030655": ("22", "42525085", "CA", "C")}


def comparison() -> list[dict]:
    with gzip.open(PACKET / "derived" / "seed_row_comparison.json.gz", "rt") as fh:
        return json.load(fh)


def annotated_rsids() -> set[str]:
    with open(SEED_DIR / "vep_seed.csv", newline="") as fh:
        return {row["rsid"] for row in csv.DictReader(fh) if row["gene_symbol"]}


def choose(bundle: list[dict], ref: str, alt: str) -> tuple[dict, str]:
    def first(pred):
        return next((b for b in bundle if pred(b)), None)

    for reason, pred in (
        ("seed_alleles", lambda b: (b["ref"], b["alt"]) == (ref, alt)),
        ("complemented_seed_alleles",
         lambda b: (b["ref"], b["alt"]) == (ref.translate(COMPLEMENT), alt.translate(COMPLEMENT))),
        # The two assemblies' reference bases differ at the locus: the same variant, with
        # GRCh37's reference allele on the other side.
        ("reversed_seed_alleles", lambda b: (b["ref"], b["alt"]) == (alt, ref)),
        ("reversed_complemented_seed_alleles",
         lambda b: (b["ref"], b["alt"]) == (alt.translate(COMPLEMENT), ref.translate(COMPLEMENT))),
        ("bundle_canonical", lambda b: b["mane_select"] == "1"),
    ):
        hit = first(pred)
        if hit is not None:
            return hit, reason
    raise SystemExit(f"no bundle row to choose from: {bundle}")


def main() -> None:
    rows = comparison()
    ensembl = {r["rsid"]: r["primary"][0] for r in rows if r.get("primary")}
    affected = {
        r["rsid"] for r in rows
        if r["class"] in {"same_chrom_other_pos", "other_chrom", "no_grch37_record"}
    } - OUT_OF_SCOPE
    scope = affected & annotated_rsids()
    snapshot = json.loads(SNAPSHOT.read_text())["rows"]
    plan = []
    for name in SEEDS:
        path = SEED_DIR / f"{name}.csv"
        with open(path, newline="") as fh:  # keep each line's own ending (some are CRLF)
            lines = fh.read().splitlines(keepends=True)
        for i, line in enumerate(lines[1:], start=1):
            fields = next(csv.reader(io.StringIO(line)))
            rsid, chrom, pos, ref, alt = fields[:5]
            if rsid not in scope:
                continue
            if rsid in DELETION_VCF:
                new, reason = DELETION_VCF[rsid], "vcf_deletion_from_ensembl_and_assembly"
            else:
                bundle = snapshot[rsid]
                hit, reason = choose(bundle, ref, alt)
                new = (hit["chrom"], hit["pos"], hit["ref"], hit["alt"])
                assert f"{new[0]}:{new[1]}" == ensembl[rsid]["locus"], (rsid, new)
            old_prefix = ",".join((rsid, chrom, pos, ref, alt)) + ","
            assert line.startswith(old_prefix), (name, line)
            lines[i] = ",".join((rsid, *new)) + "," + line[len(old_prefix):]
            plan.append({"seed": name, "line": i + 1, "rsid": rsid,
                         "old": f"{chrom}:{pos} {ref}>{alt}",
                         "new": f"{new[0]}:{new[1]} {new[2]}>{new[3]}", "reason": reason})
        with open(path, "w", newline="") as fh:
            fh.write("".join(lines))
    (PACKET / "derived" / "annotated_rewrite_plan.json").write_text(json.dumps(plan, indent=1) + "\n")
    print(f"{len(scope)} rsIDs, {len(plan)} rows rewritten")


if __name__ == "__main__":
    main()
