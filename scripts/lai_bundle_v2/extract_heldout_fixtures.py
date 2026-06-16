#!/usr/bin/env python3
"""Extract AncestryDNA-density (GRCh38) fixtures for the held-out validation set.

For each held-out 1000G/HGDP sample, write its true genotypes at the SAME 666k
site set as the bundle liftover map, pulled from the phasing panel
(03_subsetted_panels/ref_panel_chrN.vcf.gz). The sample is held OUT of gnomix
training (sample_map.txt) but is in the panel, so this is a genuine held-out
inference test. Output mirrors the HG01502 fixture format
(rsid, chromosome, GRCh38 position, allele1, allele2).
"""

from __future__ import annotations

import argparse
import gzip
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-dir", required=True, type=Path)
    parser.add_argument("--validation-dir", required=True, type=Path)
    parser.add_argument(
        "--site-map",
        required=True,
        type=Path,
        help="rsid<TAB>chrom<TAB>GRCh38-pos map, e.g. 02_liftover/rsid_to_grch38.tsv",
    )
    parser.add_argument("--chroms", nargs="+", default=[str(i) for i in range(1, 23)])
    return parser.parse_args()


def load_sites(site_map: Path) -> dict[tuple[str, str], str]:
    """Return (chrom without chr-prefix, pos) -> rsid from a 3-column site map."""
    sites: dict[tuple[str, str], str] = {}
    for line in site_map.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rsid, chrom, pos = parts[:3]
        sites[(chrom.replace("chr", ""), pos)] = rsid
    return sites


def main() -> int:
    args = parse_args()
    outdir = args.validation_dir / "heldout_fixtures"
    outdir.mkdir(exist_ok=True)

    sites = load_sites(args.site_map)
    print(f"site map: {len(sites):,} sites", flush=True)

    held = [
        ln.split("\t")
        for ln in (args.validation_dir / "held_out_validation.tsv").read_text().splitlines()[1:]
        if ln.strip()
    ]
    iids = [h[0] for h in held]
    region = {h[0]: h[1] for h in held}
    acc: dict[str, list[str]] = {i: [] for i in iids}

    for chrom_token in args.chroms:
        panel = args.panel_dir / f"ref_panel_chr{chrom_token}.vcf.gz"
        cmd = [
            "bcftools",
            "query",
            "-s",
            ",".join(iids),
            "-f",
            r"%CHROM\t%POS\t%REF\t%ALT[\t%TGT]\n",
            str(panel),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(f"chr{chrom_token} bcftools ERROR: {proc.stderr[:300]}", flush=True)
            return 1
        kept = 0
        for row in proc.stdout.splitlines():
            fields = row.split("\t")
            if len(fields) < 4 + len(iids):
                continue
            chrom = fields[0].replace("chr", "")
            pos = fields[1]
            rsid = sites.get((chrom, pos))
            if rsid is None:
                continue
            kept += 1
            for iid, tgt in zip(iids, fields[4:]):
                alleles = tgt.replace("|", "/").split("/")
                if len(alleles) != 2 or "." in alleles or "" in alleles:
                    continue
                acc[iid].append(f"{rsid}\t{chrom}\t{pos}\t{alleles[0]}\t{alleles[1]}")
        print(f"chr{chrom_token}: kept {kept} sites", flush=True)

    for iid in iids:
        out = outdir / f"{iid}_{region[iid]}.adna.txt.gz"
        with gzip.open(out, "wt") as fh:
            fh.write(
                f"#held-out 1000G/HGDP sample {iid} ({region[iid]}); NOT in gnomix training\n"
            )
            fh.write("#Derived from public 1000G+HGDP phased reference; NOT real user data.\n")
            fh.write("rsid\tchromosome\tposition\tallele1\tallele2\n")
            fh.write("\n".join(acc[iid]) + "\n")
        print(f"WROTE {out.name}: {len(acc[iid]):,} variants", flush=True)
    print("DONE_EXTRACT", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
