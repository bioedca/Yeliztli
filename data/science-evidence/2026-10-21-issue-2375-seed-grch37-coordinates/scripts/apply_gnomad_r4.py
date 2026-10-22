#!/usr/bin/env python3
"""Write gnomAD v4.1 joint frequencies into the re-oriented gnomad_seed rows (#2579 review).

For each rsID in raw/gnomad_r4/request.json, orient the gnomAD variant (GRCh38 ref/alt) to the
fixture row's GRCh37 ref/alt:

* same pair (or its complement, for a locus reversed between builds): AF = AC/AN per
  population and the joint homozygote count;
* reversed pair: AF = 1 - AC/AN, and the homozygote count is withheld - it counts the
  other allele's homozygotes and cannot be derived from AC/AN;
* rsID multiallelic or absent in gnomAD r4, or no orientation match: every frequency column
  and the count are withheld (empty -> NULL), as gnomad_seed.af_asj.provenance.json does.

af_eur is gnomAD's nfe (backend/annotation/gnomad.py). Updates the af_asj provenance to match.
Writes derived/gnomad_r4_values.json.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1]
ROOT = PACKET.parents[2]
SEED = ROOT / "tests" / "fixtures" / "seed_csvs" / "gnomad_seed.csv"
ASJ = ROOT / "tests" / "fixtures" / "seed_csvs" / "gnomad_seed.af_asj.provenance.json"
COMPLEMENT = str.maketrans("ACGT", "TGCA")
POPS = (("af_afr", "afr"), ("af_amr", "amr"), ("af_asj", "asj"), ("af_eas", "eas"),
        ("af_eur", "nfe"), ("af_fin", "fin"), ("af_sas", "sas"))
COLUMNS = ("af_global", *(c for c, _ in POPS), "homozygous_count")


def _fmt(x: float) -> str:
    text = f"{x:.8f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0.0"


def _orientation(gnomad_id: str, ref: str, alt: str) -> str | None:
    _, _, g_ref, g_alt = gnomad_id.split("-")
    c_ref, c_alt = ref.translate(COMPLEMENT), alt.translate(COMPLEMENT)
    if (g_ref, g_alt) in ((ref, alt), (c_ref, c_alt)):
        return "same"
    if (g_ref, g_alt) in ((alt, ref), (c_alt, c_ref)):
        return "reversed"
    return None


def main() -> None:
    request = json.loads((PACKET / "raw" / "gnomad_r4" / "request.json").read_text())
    with open(SEED, newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    header = next(csv.reader(io.StringIO(lines[0])))
    asj = json.loads(ASJ.read_text())
    decisions = {}
    for i, line in enumerate(lines[1:], start=1):
        ending = line[len(line.rstrip("\r\n")):]
        fields = next(csv.reader(io.StringIO(line)))
        row = dict(zip(header, fields))
        rsid = row["rsid"]
        if rsid not in request["rsids"]:
            continue
        with gzip.open(PACKET / "raw" / "gnomad_r4" / f"{rsid}.json.gz", "rt") as fh:
            payload = json.load(fh)
        variant = (payload.get("data") or {}).get("variant")
        values = {c: "" for c in COLUMNS}
        record = None
        if variant is None:
            reason = "withheld: " + "; ".join(e["message"] for e in payload.get("errors", []))
        else:
            orient = _orientation(variant["variant_id"], row["ref"], row["alt"])
            joint = variant["joint"]
            pops = {p["id"]: p for p in joint["populations"]}
            if orient is None:
                reason = f"withheld: {variant['variant_id']} matches neither orientation"
            else:
                def af(ac: int, an: int) -> str:
                    if not an:
                        return ""
                    f = ac / an
                    return _fmt(1 - f if orient == "reversed" else f)

                values["af_global"] = af(joint["ac"], joint["an"])
                for column, pop in POPS:
                    p = pops.get(pop)
                    values[column] = af(p["ac"], p["an"]) if p else ""
                if orient == "same":
                    values["homozygous_count"] = str(joint["homozygote_count"])
                reason = f"{orient} as {variant['variant_id']}"
                a = pops.get("asj")
                if a and a["an"]:
                    record = {"af_asj": float(values["af_asj"]), "asj_ac": a["ac"],
                              "asj_an": a["an"], "gnomad_variant_id": variant["variant_id"],
                              "orientation": orient}
        row.update(values)
        buf = io.StringIO()
        csv.writer(buf, lineterminator="").writerow([row[h] for h in header])
        lines[i] = buf.getvalue() + ending
        decisions[rsid] = {"reason": reason, **values}
        withheld = asj["withheld_rsids"]["rsids"]
        if record is not None:
            asj["records"][rsid] = record
            if rsid in withheld:
                withheld.remove(rsid)
        else:
            asj["records"].pop(rsid, None)
            if rsid not in withheld:
                withheld.append(rsid)
                withheld.sort(key=lambda r: int(r[2:]) if r[2:].isdigit() else 0)
    asj["withheld_rsids"]["count"] = len(asj["withheld_rsids"]["rsids"])
    with open(SEED, "w", newline="") as fh:
        fh.write("".join(lines))
    ASJ.write_text(json.dumps(asj, indent=2))  # the committed file has no trailing newline
    (PACKET / "derived" / "gnomad_r4_values.json").write_text(json.dumps(decisions, indent=1) + "\n")


if __name__ == "__main__":
    main()
