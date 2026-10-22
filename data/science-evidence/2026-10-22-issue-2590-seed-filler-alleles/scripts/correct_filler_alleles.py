"""Give every unannotated vep_seed filler row its rsID's GRCh37 alleles (#2590).

Filler rows carry no gene, transcript or HGVS, so their alleles have nothing to stay
coherent with and can take the seed-wide oracle's directly. The oracle's first allele
is the GRCh37 assembly base at every single-base position (two sources, #2583's
packet), so no lookup is needed.

* SNV string, row not reference-first: swap, so REF is the assembly base.
* SNV string, row outside it: REF is the assembly base; ALT is the row's old allele
  when that is in the string, else the string's next allele.
* Padded indel string ("TT/T"): REF/ALT are the string as written.
* Un-padded insertion ("-/T"): REF "-", ALT the inserted base, at Ensembl's start.

Only rows the allele guard rejects are touched; the rest of the file is rewritten
byte-for-byte as read.

Usage: python -I correct_filler_alleles.py <repo-root>
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path


def _is_single_base(allele_string: str) -> bool:
    alleles = allele_string.split("/")
    return "-" not in alleles and all(len(a) == 1 for a in alleles)


def corrected(row: dict[str, str], mapping: dict) -> tuple[str, str, int] | None:
    alleles = mapping["allele_string"].split("/")
    ref, alt = row["ref"], row["alt"]
    start = int(mapping["start"])
    if _is_single_base(mapping["allele_string"]):
        first = alleles[0]
        if ref == first and alt in alleles and alt != first:
            return None
        if {ref, alt} <= set(alleles):
            return first, (alt if alt != first else ref), start
        for old in (alt, ref):
            if old in alleles and old != first:
                return first, old, start
        return first, next(a for a in alleles[1:] if a != first), start
    snv_row = len(ref) == 1 and len(alt) == 1 and "-" not in (ref, alt)
    if not snv_row:
        return None
    if "-" in alleles:
        inserted = [a for a in alleles if a != "-"]
        assert len(inserted) == 1, mapping
        return "-", inserted[0], start
    assert len(alleles) == 2, mapping
    return alleles[0], alleles[1], start


def main() -> None:
    repo = Path(sys.argv[1]).resolve()
    oracle = json.loads(
        (repo / "tests/fixtures/seed_rsid_grch37_coordinates.json").read_text(encoding="utf-8")
    )["rsids"]
    path = repo / "tests/fixtures/seed_csvs/vep_seed.csv"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    header = next(csv.reader([lines[0]]))
    out = [lines[0]]
    changed = []
    for line in lines[1:]:
        row = dict(zip(header, next(csv.reader([line])), strict=True))
        mapping = oracle.get(row["rsid"])
        filler = not (row["gene_symbol"] or row["hgvs_coding"])
        located = (
            mapping is not None
            and str(mapping["chrom"]) == row["chrom"]
            and int(row["pos"]) in (int(mapping["start"]), int(mapping["start"]) - 1)
        )
        fix = corrected(row, mapping) if (filler and located) else None
        if fix is None:
            out.append(line)
            continue
        new_ref, new_alt, new_pos = fix
        changed.append((row["rsid"], row["ref"], row["alt"], row["pos"], new_ref, new_alt, new_pos))
        row.update(ref=new_ref, alt=new_alt, pos=str(new_pos))
        buf = io.StringIO()
        csv.writer(buf, lineterminator=line[len(line.rstrip("\r\n")) :]).writerow(
            [row[h] for h in header]
        )
        out.append(buf.getvalue())
    path.write_text("".join(out), encoding="utf-8")
    print(f"corrected {len(changed)} filler rows")
    for item in changed[:5]:
        print(item)


if __name__ == "__main__":
    main()
