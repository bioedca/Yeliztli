"""Held-out per-superpopulation LAI inference accuracy gate (incl. EUR).

THE gold-standard gate the 0.97 mean per-window accuracy missed: it measured the
native gnomix .pkl on an EUR-poor validation split, so it never noticed that the
shipped bundle classified every European as CSA. This runs REAL held-out
1000G/HGDP samples (NOT in gnomix training) through the production inference path
(run_lai_analysis -> gnomix_inference) against the assembled bundle and checks
that each sample's top global-ancestry equals its true superpopulation.

Usage:
  YELIZTLI_LAI_BUNDLE_PATH=<assembled bundle dir>
  YELIZTLI_DATA_DIR=<scratch data dir>
  python 06f_heldout_superpop_accuracy.py <fixtures_dir> <held_out_validation.tsv> [out.json]

Each fixture: <IID>_<REGION>.adna.txt.gz, 5-col (rsid, chrom, pos, a1, a2).
Mirrors TestRealBundleLAIAccuracy / calibrate_lai.py exactly.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import sqlalchemy as sa

from backend.analysis.lai import run_lai_analysis
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, sample_metadata_table


def read_accuracy_threshold(name: str, default: str = "1.0") -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number between 0.0 and 1.0; got {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise SystemExit(f"{name} must be between 0.0 and 1.0; got {value!r}")
    return value


def parse_fixture(path: Path) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()  # raw_variants.rsid is UNIQUE; dedup keep-first (real
    # AncestryDNA uploads have one row per marker; our panel-derived fixtures can
    # repeat an rsid via multiallelic / multi-position records).
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            rsid, chrom, pos, a1, a2 = parts
            if rsid == "rsid" or rsid in seen:
                continue
            try:
                pos_int = int(pos)
            except ValueError:
                continue
            seen.add(rsid)
            rows.append(
                {
                    "rsid": rsid,
                    "chrom": chrom,
                    "pos": pos_int,
                    "genotype": f"{a1}{a2}",
                    "source": "",
                }
            )
    return rows


def run_one(sample_id: int, iid: str, fixture: Path) -> dict:
    # Unique sample_id per worker: run_lai_analysis writes intermediates to
    # data_dir/lai_work/sample_{id}/, so parallel workers MUST use distinct ids
    # or they clobber each other's per-chrom VCFs (tabix/pysam truncation errors).
    variants = parse_fixture(fixture)
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    with engine.begin() as conn:
        # sample_metadata is single-row (CHECK id=1); run_lai_analysis reads the
        # single row, not WHERE id=sample_id. sample_id is used ONLY for the
        # output_dir (data_dir/lai_work/sample_{id}), so a unique sample_id per
        # worker isolates the working dir without violating the row constraint.
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=f"heldout_{iid}",
                file_format="ancestrydna_v2.0",
                file_hash=iid,
            )
        )
        conn.execute(raw_variants.insert(), variants)
    result = run_lai_analysis(sample_id=sample_id, sample_engine=engine, progress_callback=None)
    fracs = {pop: info["fraction"] for pop, info in result.global_ancestry.items()}
    top = max(fracs, key=fracs.get) if fracs else None
    return {"n_variants": len(variants), "fractions": fracs, "top": top}


def main() -> None:
    fixtures_dir = Path(sys.argv[1])
    labels_tsv = Path(sys.argv[2])
    out_path = (
        Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/heldout_validation_report.json")
    )

    truth = {}
    for line in labels_tsv.read_text().splitlines()[1:]:
        if not line.strip():
            continue
        iid, region = line.split("\t")[:2]
        truth[iid] = region

    workers = int(os.environ.get("VAL_WORKERS", "6"))
    jobs = []  # (sample_id, iid, region, fixture)
    sid = 0
    for iid, region in sorted(truth.items(), key=lambda kv: (kv[1], kv[0])):
        matches = list(fixtures_dir.glob(f"{iid}_*.adna.txt.gz"))
        if not matches:
            print(f"SKIP {iid} ({region}): no fixture", flush=True)
            continue
        sid += 1
        jobs.append((sid, iid, region, matches[0]))

    results = []
    per_region_total: dict[str, int] = defaultdict(int)
    per_region_correct: dict[str, int] = defaultdict(int)

    def _record(iid: str, region: str, r: dict) -> None:
        correct = r["top"] == region
        per_region_total[region] += 1
        per_region_correct[region] += int(correct)
        true_frac = r["fractions"].get(region, 0.0)
        top_frac = r["fractions"].get(r["top"], 0.0)
        results.append(
            {
                "iid": iid,
                "true": region,
                "top": r["top"],
                "correct": correct,
                "true_frac": round(true_frac, 4),
                "top_frac": round(top_frac, 4),
                "n_variants": r["n_variants"],
                "fractions": {k: round(v, 4) for k, v in r["fractions"].items()},
            }
        )
        flag = "OK " if correct else "XX "
        print(
            f"{flag}{iid:18} true={region:4} top={r['top']:4} "
            f"true_frac={true_frac:.3f} top_frac={top_frac:.3f} n={r['n_variants']:,}",
            flush=True,
        )

    if workers <= 1 or len(jobs) <= 1:
        for sid_, iid, region, fix in jobs:
            _record(iid, region, run_one(sid_, iid, fix))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(run_one, sid_, iid, fix): (iid, region)
                for sid_, iid, region, fix in jobs
            }
            for fut in as_completed(futs):
                iid, region = futs[fut]
                _record(iid, region, fut.result())

    per_region_acc = {
        reg: per_region_correct[reg] / per_region_total[reg] for reg in per_region_total
    }
    overall = sum(per_region_correct.values()) / max(1, sum(per_region_total.values()))
    eur_acc = per_region_acc.get("EUR", 0.0)
    min_region_accuracy = read_accuracy_threshold("HELDOUT_MIN_REGION_ACCURACY")
    min_eur_accuracy = read_accuracy_threshold("HELDOUT_MIN_EUR_ACCURACY")
    regions_below_threshold = {
        reg: round(acc, 4) for reg, acc in per_region_acc.items() if acc < min_region_accuracy
    }
    eur_passes = eur_acc >= min_eur_accuracy
    all_regions_pass = not regions_below_threshold

    report = {
        "overall_accuracy": round(overall, 4),
        "per_region_accuracy": {k: round(v, 4) for k, v in per_region_acc.items()},
        "per_region_n": dict(per_region_total),
        "min_region_accuracy": min_region_accuracy,
        "min_eur_accuracy": min_eur_accuracy,
        "eur_accuracy": round(eur_acc, 4),
        "eur_passes": eur_passes,
        "regions_below_threshold": regions_below_threshold,
        "all_regions_pass": all_regions_pass,
        "samples": results,
    }
    out_path.write_text(json.dumps(report, indent=2))
    print("\n==== PER-SUPERPOPULATION HELD-OUT INFERENCE ACCURACY ====", flush=True)
    for reg in sorted(per_region_acc):
        n_ok, n_tot = per_region_correct[reg], per_region_total[reg]
        print(f"  {reg:4}  {n_ok}/{n_tot} = {per_region_acc[reg]:.3f}", flush=True)
    print(
        f"  OVERALL = {overall:.3f}   EUR = {eur_acc:.3f} (was 0.000 on the broken bundle)",
        flush=True,
    )
    print(f"report -> {out_path}", flush=True)
    if not eur_passes or not all_regions_pass:
        print(
            "HELD-OUT SUPERPOPULATION GATE FAILED: "
            f"eur_passes={eur_passes} regions_below_threshold={regions_below_threshold}",
            flush=True,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
