# Liftover chain files

Vendored UCSC liftOver chains used by `backend/ingestion/liftover.py`:

- `hg19ToHg38.over.chain.gz` — GRCh37 (hg19) → GRCh38 (hg38), for the parallel
  `chrom_grch38`/`pos_grch38` annotation columns (`convert_coordinate`).
- `hg18ToHg19.over.chain.gz` — NCBI build 36 (hg18) → GRCh37 (hg19), to lift
  23andMe **v3** uploads at ingest so the stored `(chrom, pos)` are GRCh37
  (`lift_build36_to_grch37`, #562).

These are bundled in-repo so liftover **never downloads at runtime**. pyliftover's
default `LiftOver("hg19", "hg38")` fetches the chain from UCSC on first use, which
made CI flaky (a failed download surfaces as
`AttributeError: 'NoneType' object has no attribute 'readline'`). Loading the
bundled file keeps tests offline/deterministic and removes the first-run download
in production.

## Files

| File | Source | Size | Accessed |
| --- | --- | --- | --- |
| `hg19ToHg38.over.chain.gz` | UCSC goldenPath | ~222 KB | 2025-06-10 |
| `hg18ToHg19.over.chain.gz` | UCSC goldenPath | ~140 KB | 2026-06-13 |

## Provenance / license

Downloaded from UCSC:

```text
https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz
https://hgdownload.soe.ucsc.edu/goldenPath/hg18/liftOver/hg18ToHg19.over.chain.gz
```

UCSC genome-annotation data (including liftOver chains) is freely available for
both academic and commercial use; see <https://genome.ucsc.edu/license/>.

## Refreshing the chains

The chains are stable, but to re-fetch (e.g. to verify integrity):

```bash
curl -fL -o backend/data/chains/hg19ToHg38.over.chain.gz \
  https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz
curl -fL -o backend/data/chains/hg18ToHg19.over.chain.gz \
  https://hgdownload.soe.ucsc.edu/goldenPath/hg18/liftOver/hg18ToHg19.over.chain.gz
gzip -t backend/data/chains/*.over.chain.gz   # integrity check
```

`tests/backend/test_liftover.py` pins known conversions (rs1801133 → chr1:11796321
for hg19→hg38; rs7412 build36 19:50103919 → GRCh37 19:45412079 for hg18→hg19),
so a bad/changed chain fails the suite.
