# Liftover chain files

Vendored UCSC liftOver chain(s) used by `backend/ingestion/liftover.py`:

- `hg19ToHg38.over.chain.gz` — lift annotated GRCh37 (hg19) coordinates to
  GRCh38 (hg38) for the parallel `chrom_grch38`/`pos_grch38` columns.
- `hg18ToHg19.over.chain.gz` — lift 23andMe **v3** (NCBI build 36 / hg18)
  uploads to GRCh37 **at ingest** so the primary `(chrom, pos)` are GRCh37
  (issue #562 / #480). This lift is strand-aware: pyliftover returns the target
  strand per position, and alleles are complemented when a region inverted
  between builds.

These are bundled in-repo so liftover **never downloads at runtime**. pyliftover's
default `LiftOver("hg19", "hg38")` fetches the chain from UCSC on first use, which
made CI flaky (a failed download surfaces as
`AttributeError: 'NoneType' object has no attribute 'readline'`). Loading the
bundled file keeps tests offline/deterministic and removes the first-run download
in production.

## Files

| File | Source | Size |
| --- | --- | --- |
| `hg19ToHg38.over.chain.gz` | UCSC goldenPath | ~222 KB |
| `hg18ToHg19.over.chain.gz` | UCSC goldenPath | ~140 KB |

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
gzip -t backend/data/chains/hg19ToHg38.over.chain.gz   # integrity check
gzip -t backend/data/chains/hg18ToHg19.over.chain.gz   # integrity check
```

`tests/backend/test_liftover.py` pins known conversions for both chains
(hg19→hg38: rs1801133 → chr1:11796321; hg18→hg19: rs7412 → 19:45412079, etc.),
so a bad/changed chain fails the suite.
