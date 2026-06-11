"""Derive analytic reference distribution parameters for the cancer PRS weight
sets from verified public allele frequencies (Ensembl REST → gnomAD genomes NFE,
1000 Genomes Phase 3 EUR).

Under Hardy-Weinberg equilibrium and assuming the (largely independent GWAS-lead)
SNPs segregate independently, the population distribution of the raw weighted
allele-dosage score ``S = Σ wᵢ·Gᵢ`` (Gᵢ ~ Binomial(2, pᵢ)) has:

    reference_mean = Σ wᵢ · 2pᵢ
    reference_var  = Σ wᵢ² · 2pᵢ(1 − pᵢ)
    reference_std  = sqrt(reference_var)

where pᵢ is the EUR effect-allele frequency. This is the standard analytic
reference distribution used when individual-level reference genotypes are not
published. It is an approximation (HWE + no-LD + normal/CLT for the percentile),
not a validated individual-level cohort distribution.

Run as a one-off curation/verification tool (network required):
    python scripts/derive_cancer_prs_reference.py            # report only
    python scripts/derive_cancer_prs_reference.py --write     # update JSON
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_prs_weights.json"
)
ENSEMBL_POST = "https://rest.ensembl.org/variation/human?content-type=application/json;pops=1"
PRIMARY_POP = "gnomADg:nfe"  # gnomAD genomes, non-Finnish European
FALLBACK_POP = "1000GENOMES:phase_3:EUR"
COMPLEMENT = {"A": "T", "T": "A", "C": "G", "G": "C"}


def fetch_populations(rsids: list[str]) -> dict:
    """Batch-fetch Ensembl variation records (with population frequencies)."""
    out: dict = {}
    for i in range(0, len(rsids), 40):
        chunk = rsids[i : i + 40]
        body = json.dumps({"ids": chunk})
        proc = subprocess.run(
            [
                "curl",
                "-s",
                "--max-time",
                "60",
                "-X",
                "POST",
                ENSEMBL_POST,
                "-H",
                "Content-Type: application/json",
                "-d",
                body,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        out.update(json.loads(proc.stdout))
        time.sleep(1)
    return out


def effect_freq(rec: dict, effect_allele: str) -> tuple[float | None, str, str]:
    """Return (freq, population_used, note) for the effect allele.

    Resolves strand: if the effect allele is not directly present in the
    population's allele set, tries the complement. Palindromic SNPs (A/T, C/G)
    are flagged because strand cannot be resolved from alleles alone.
    """
    pops = rec.get("populations", [])
    palindromic = COMPLEMENT.get(effect_allele) == _other_allele(rec, effect_allele)
    for pop_name in (PRIMARY_POP, FALLBACK_POP):
        entries = {p["allele"]: p["frequency"] for p in pops if p.get("population") == pop_name}
        if not entries:
            continue
        if effect_allele in entries:
            note = "palindromic-assumed-plus-strand" if palindromic else "direct"
            return float(entries[effect_allele]), pop_name, note
        comp = COMPLEMENT.get(effect_allele)
        if comp and comp in entries:
            return float(entries[comp]), pop_name, "strand-flipped"
    return None, "", "NOT_FOUND"


def _other_allele(rec: dict, effect_allele: str) -> str | None:
    for pop_name in (PRIMARY_POP, FALLBACK_POP):
        alleles = [
            p["allele"] for p in rec.get("populations", []) if p.get("population") == pop_name
        ]
        for a in alleles:
            if a != effect_allele and len(a) == 1:
                return a
    return None


def main() -> None:
    write = "--write" in sys.argv
    data = json.loads(WEIGHTS_PATH.read_text())
    all_rsids = sorted({w["rsid"] for ws in data["weight_sets"] for w in ws["weights"]})
    print(f"Fetching {len(all_rsids)} SNPs from Ensembl ({PRIMARY_POP} / {FALLBACK_POP})...")
    recs = fetch_populations(all_rsids)

    missing = [r for r in all_rsids if r not in recs]
    if missing:
        print(f"WARNING: no Ensembl record for: {missing}")

    for ws in data["weight_sets"]:
        mean = 0.0
        var = 0.0
        flags = []
        for w in ws["weights"]:
            rec = recs.get(w["rsid"], {})
            p, pop, note = effect_freq(rec, w["effect_allele"])
            if p is None:
                flags.append(f"{w['rsid']}:NO_FREQ")
                continue
            if note in ("strand-flipped", "palindromic-assumed-plus-strand", "NOT_FOUND"):
                flags.append(
                    f"{w['rsid']}:{note}({w['effect_allele']},p={p:.3f},{pop.split(':')[0]})"
                )
            w["effect_allele_freq"] = round(p, 4)
            mean += w["weight"] * 2.0 * p
            var += (w["weight"] ** 2) * 2.0 * p * (1.0 - p)
        std = math.sqrt(var)
        print(f"\n{ws['name']}  (n_snps={len(ws['weights'])})")
        print(f"   reference_mean = {mean:.4f}   reference_std = {std:.4f}")
        if flags:
            print("   flags:", "; ".join(flags))
        ws["reference_mean"] = round(mean, 4)
        ws["reference_std"] = round(std, 4)
        ws["reference_distribution_method"] = (
            "analytic HWE (mean=Σw·2p, var=Σw²·2p(1-p)) from Ensembl "
            f"{PRIMARY_POP}/{FALLBACK_POP} effect-allele frequencies; assumes SNP independence"
        )

    if write:
        WEIGHTS_PATH.write_text(json.dumps(data, indent=2) + "\n")
        print(f"\nWROTE {WEIGHTS_PATH}")
    else:
        print("\n(report only — pass --write to update the JSON)")


if __name__ == "__main__":
    main()
