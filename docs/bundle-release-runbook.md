# VEP bundle — build & release runbook

How to build, verify, and publish the **VEP consequence bundle** (`vep_bundle.db`). This is the
operator runbook referenced by the release workflow and the bundle release notes.

## 1. Overview

The VEP bundle is an indexed SQLite database of pre-computed variant consequences (gene,
transcript, HGVS, MANE/canonical transcript flag, ...) for every site in the genotyping union
catalog. It is published as a GitHub release asset and pinned in
[`bundles/manifest.json`](https://github.com/bioedca/Yeliztli/blob/main/bundles/manifest.json).

- **Asset:** `vep_bundle.db` (uncompressed SQLite, ~340–360 MB)
- **Release tag:** `bundle-v<version>` (e.g. `bundle-v4.0.0`)
- **Manifest URL pattern:** `https://github.com/bioedca/Yeliztli/releases/download/bundle-v<version>/vep_bundle.db`
- **Embedded version:** the build stores `bundle_version` in the DB's `bundle_metadata` table, which the release workflow checks against the manifest.

## 2. Prerequisites

- The genotyping **union catalog** (rsid, chrom, pos on GRCh37), produced by
  [`scripts/build_union_catalog.py`](https://github.com/bioedca/Yeliztli/blob/main/scripts).
- An **Ensembl VEP** run of that catalog producing a VCF (off-repo; pin the Ensembl version).
- `gh` CLI authenticated with permission to manage releases.

## 3. Run VEP and build the bundle

The production build uses VEP's default coordinate input so it can run against the local
GRCh37 cache. First resolve the union-catalog rsIDs to Ensembl variation features on GRCh37
and write one tab-delimited row per feature in VEP default format:
`chrom start end allele_string strand rsid`. Keep only rows whose resolved `chrom`/`start`
match the union catalog coordinate, so merged or remapped rsIDs do not inflate coverage.

For the v4.0.0 build, the fastest reliable route was to stream the Ensembl
`variation_feature.txt.gz` MySQL dump, map `seq_region_id` through `seq_region.txt.gz`, keep
`map_weight = 1`, and write matching primary-chromosome rows:

```bash
curl -L -o seq_region.txt.gz \
  https://ftp.ensembl.org/pub/release-112/mysql/homo_sapiens_variation_112_37/seq_region.txt.gz
curl -L -o variation_feature.txt.gz \
  https://ftp.ensembl.org/pub/release-112/mysql/homo_sapiens_variation_112_37/variation_feature.txt.gz

python - <<'PY' > vep_default_input.unsorted.txt
import gzip
from pathlib import Path

catalog = {}
with Path("union_sites.tsv").open() as handle:
    for line in handle:
        rsid, chrom, pos, *_ = line.rstrip("\n").split("\t")
        if rsid.startswith("rs"):
            catalog[rsid] = (chrom.removeprefix("chr"), pos)

seq_regions = {}
with gzip.open("seq_region.txt.gz", "rt") as handle:
    for line in handle:
        seq_region_id, name, *_ = line.rstrip("\n").split("\t")
        seq_regions[seq_region_id] = name.removeprefix("chr")

written = set()
with gzip.open("variation_feature.txt.gz", "rt") as handle:
    for line in handle:
        cols = line.rstrip("\n").split("\t")
        seq_region_id = cols[1]
        start = cols[2]
        end = cols[3]
        strand = cols[4]
        alleles = cols[6]
        rsid = cols[8]
        map_weight = cols[9]
        key = catalog.get(rsid)
        chrom = seq_regions.get(seq_region_id, seq_region_id).removeprefix("chr")
        if map_weight != "1" or "/" not in alleles:
            continue
        if key != (chrom, start):
            continue
        strand_symbol = "-" if strand == "-1" else "+"
        feature_key = (rsid, chrom, start, end, alleles, strand_symbol)
        if feature_key in written:
            continue
        print(chrom, start, end, alleles, strand_symbol, rsid, sep="\t")
        written.add(feature_key)
PY

LC_ALL=C sort -k1,1V -k2,2n -k3,3n -k6,6 \
  vep_default_input.unsorted.txt > vep_default_input.txt
```

Run Ensembl VEP against that coordinate input to produce `vep_output.vcf.gz`. The
`--canonical` flag is required: the GRCh37 VEP bundle does not receive `MANE_SELECT`, so the
builder uses `CANONICAL=YES` as the preferred-transcript tiebreaker. The `--fasta` flag is
also required for offline `--hgvs` generation. The coordinate input must be sorted by chromosome
and position before VEP runs.

```bash
vep \
  --species homo_sapiens \
  --assembly GRCh37 \
  --cache \
  --offline \
  --dir_cache vep_cache \
  --fasta Homo_sapiens.GRCh37.dna.primary_assembly.fa \
  --input_file vep_default_input.txt \
  --output_file vep_output.vcf \
  --vcf \
  --symbol \
  --hgvs \
  --numbers \
  --canonical \
  --fork 4 \
  --buffer_size 5000 \
  --force_overwrite
gzip -c vep_output.vcf > vep_output.vcf.gz
```

For a small smoke test only, `scripts/generate_vep_input.py --rsid-list union_sites.tsv`
can produce rsIDs for `vep --database --format id`. Do not use that live-database ID route
for the full catalog; it is too slow for release-scale builds and cannot run offline.

Then build the SQLite bundle:

```bash
python scripts/build_vep_bundle.py \
  --vep-vcf vep_output.vcf.gz \
  --output vep_bundle.db \
  --ensembl-version 112 \
  --bundle-version v4.0.0 \
  --rsid-catalog union_sites.tsv \
  --write-stats vep_bundle_build_stats.json
```

The `--bundle-version` you pass is embedded in the DB and **must** match the manifest entry.
After building, verify that canonical rows are present and the known regressions resolve through
the canonical transcripts:

```sql
SELECT mane_select, COUNT(*) FROM vep_annotations GROUP BY mane_select;
SELECT rsid, alt, gene_symbol, transcript_id, consequence, hgvs_coding, hgvs_protein, mane_select
FROM vep_annotations
WHERE rsid IN ('rs771467011', 'rs1801133')
ORDER BY rsid, alt;
```

`rs771467011` should resolve to the AGTRAP canonical synonymous `p.Leu98=` row, and
`rs1801133` should keep the MTHFR `NM_005957`/`ENST00000376592` pathogenic HGVS
(`c.665C>T`, `p.Ala222Val`).

## 4. Capture integrity values

```bash
sha256sum vep_bundle.db   # → manifest sha256
stat -c %s vep_bundle.db  # → manifest size_bytes
```

## 5. Update the manifest

Edit the `vep_bundle` entry in `bundles/manifest.json` with the new `version`, `build_date`,
`url` (release-tag pattern above), `sha256`, `size_bytes`, and `min_app_version`.

!!! warning "Keep the version contract consistent"
    The manifest `version`, the release **tag** (`bundle-v<version>`), and the `bundle_version`
    **embedded** in the database must all match — the verification workflow (step 7) rejects
    a release where they don't. Build the bundle with the same `--bundle-version` you put in
    the manifest, and tag the release to match.

## 6. Draft the GitHub release

```bash
gh release create bundle-v4.0.0 --draft \
  --title "VEP bundle v4.0.0" \
  --notes-file docs/release-notes/bundle-v4.0.0.md \
  vep_bundle.db
```

## 7. Verify before publishing

Trigger the
[`bundle-release.yml`](https://github.com/bioedca/Yeliztli/blob/main/.github/workflows/bundle-release.yml)
workflow (`workflow_dispatch`) with `release_tag=bundle-v4.0.0` and `bundle_key=vep_bundle`. It
downloads the draft asset and verifies the **tag ↔ manifest version**, the **SHA-256**, the
**size**, and the **embedded `bundle_version`**. Only proceed if it passes.

## 8. Publish

```bash
gh release edit bundle-v4.0.0 --draft=false
```

## 9. Rollback

If a regression is found after publishing:

1. **Revert the `bundles/manifest.json` change** (point `vep_bundle` back at the prior version)
   in a PR — the app then downloads the previous, known-good bundle.
2. **Do not delete** the GitHub release; instead edit its notes to mark the version superseded,
   so existing references stay valid.
3. Note the rollback in `docs/release-notes/`.
