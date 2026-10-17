# SpliceAI GRCh37 contig lengths (#2175)

Claim: the GRCh37 lengths that `backend/annotation/spliceai.py` (`_GRCH37_CONTIG_LENGTHS`)
uses to verify a SpliceAI VCF header.

| contig | GRCh37 length | RefSeq | GenBank |
| --- | --- | --- | --- |
| 1 | 249250621 | NC_000001.10 | CM000663.1 |
| 2 | 243199373 | NC_000002.11 | CM000664.1 |
| 10 | 135534747 | NC_000010.10 | CM000672.1 |
| X | 155270560 | NC_000023.10 | CM000685.1 |
| Y | 59373566 | NC_000024.9 | CM000686.1 |

MT is excluded. NCBI lists GRCh37's MT as NC_012920.1 (rCRS), 16569 bp. hg19's chrM is
16571 bp, a different sequence (see `backend/ingestion/liftover.py`). So a genuine GRCh37 file
can declare either length, and a length check there could reject a valid file.

## Sources

1. **NCBI Datasets, GRCh37.p13 assembled-molecule sequence report** (accessed 2026-10-17).
   This is the authoritative source for the lengths.
   - Accession `GCF_000001405.25`; NCBI Assembly uid 37871 names it `GRCh37.p13`, submitted by
     the Genome Reference Consortium.
   - Request:
     `https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession/GCF_000001405.25/sequence_reports?role_filters=assembled-molecule&page_size=100`
   - Raw copy: [`raw/ncbi_grch37p13_assembled_molecules.json`](raw/ncbi_grch37p13_assembled_molecules.json),
     sha256 `c4b4f70f4371422cdb7468f464e54478063adeb4e8c656951dfa78530fbaf02b`.
   - Assembly summary: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=assembly&id=37871&retmode=json`
   - Raw copy: [`raw/ncbi_assembly_esummary_37871.json`](raw/ncbi_assembly_esummary_37871.json),
     sha256 `c6cf6d226680db5039335dda45673fc3ccfe3c05d070d8b1d797d81740fec085`.
   - License: NCBI states that it "places no restrictions on the use or distribution of the
     data" (https://www.ncbi.nlm.nih.gov/home/about/policies/, accessed 2026-10-17).
2. **Illumina SpliceAI example VCF**, `examples/input.vcf` at commit
   `03f42437aaf56dc5dfd822c4ccee5aec1a705079` (accessed 2026-10-17). This shows what a SpliceAI
   VCF header carries.
   - URL: https://raw.githubusercontent.com/Illumina/SpliceAI/03f42437aaf56dc5dfd822c4ccee5aec1a705079/examples/input.vcf
   - sha256 `ce40abfc71d5f10e9cea5b778ad647a7527b73ce45d0e52a8017e779db3dd140`.
   - It declares `##reference=GRCh37/hg19` and one `##contig` line per chromosome, whose lengths
     for 1, 2, 10, X and Y equal the NCBI values above.
   - License: PolyForm Strict 1.0.0 (code) and CC BY-NC 4.0 (models). The strict license does not
     permit redistribution, so no copy is kept here. The file is referenced by URL and digest only.
3. **SpliceAI**, PMID:30661751 / DOI:10.1016/j.cell.2018.12.015 (accessed 2026-10-17). This is
   the primary paper for the resource whose files the check guards. PubMed's record shows no
   retraction or erratum.

## Cross-check

The same five values appear, with `chr` prefixes, in `_GRCH37_HG19_SENTINEL_LENGTHS`
(`backend/api/routes/igv_tracks.py`, #1449), which validates the UCSC hg19 FASTA index.
`test_contig_lengths_match_the_genome_browser_sentinels` keeps the two copies equal.

Sources 1 and 2 both describe the GRCh37 assembly, so source 2 confirms the transcription rather
than adding independent evidence. The lengths are definitional for the named assembly, and
NCBI's registry record is the defining source.

Evidence tools: NCBI Datasets and E-utilities directly, with PubMed for the paper record.
Consensus and Scite were not used, because the claim is a registry value checked at the registry.
