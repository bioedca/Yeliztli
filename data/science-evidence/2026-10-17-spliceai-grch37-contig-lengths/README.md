# SpliceAI GRCh37 contig lengths (#2175)

Claim: the GRCh37 lengths that `backend/annotation/spliceai.py` (`_GRCH37_CONTIG_LENGTHS`)
uses to verify a SpliceAI VCF header.

| contig | GRCh37 length |
| --- | --- |
| 1 | 249250621 |
| 2 | 243199373 |
| 10 | 135534747 |
| X | 155270560 |
| Y | 59373566 |

## Source (accessed 2026-10-17)

- Illumina SpliceAI repository, `examples/input.vcf` at commit
  `03f42437aaf56dc5dfd822c4ccee5aec1a705079` (the commit #2175 cites for the README):
  https://raw.githubusercontent.com/Illumina/SpliceAI/03f42437aaf56dc5dfd822c4ccee5aec1a705079/examples/input.vcf
  - sha256 `ce40abfc71d5f10e9cea5b778ad647a7527b73ce45d0e52a8017e779db3dd140`
  - declares `##reference=GRCh37/hg19` and one `##contig` line per chromosome; the five values
    above are copied from those lines.
  - not redistributed here; referenced by URL and digest.

## Cross-check

The same five values appear, with `chr` prefixes, in `_GRCH37_HG19_SENTINEL_LENGTHS`
(`backend/api/routes/igv_tracks.py`, #1449), which validates the UCSC hg19 FASTA index.
Both trace to the GRCh37 assembly, so they confirm the transcription rather than providing
independent evidence; the values are definitional for the named assembly.
`test_contig_lengths_match_the_genome_browser_sentinels` keeps the two copies equal.

MT is excluded: GRCh37's MT and hg19's chrM are different sequences, so a genuine GRCh37
file can declare either length and a length check there could reject a valid file.
