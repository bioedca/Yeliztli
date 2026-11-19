# Evidence for the offline VEP decision (#2715)

Scope is artifact identity, packaging, the historical software path, and a verifier design for #2299. No personal genotype file, donor-derived site list or restricted database was fetched or retained. The published v4 SQLite reference pack was downloaded for an aggregate comparison and verified against its manifest SHA-256. The historical observations were made at commit `4e6788efa6c5a7e888a09cd3ee8aec3c4bf141ea`. Directory names below are relative to this packet.

## Claim map and sources

| Claim or input | Source, version, terms and retained evidence |
| --- | --- |
| What Yeliztli ships and what the API returns | Git blob `ec58414f860f16509e4b87c045aff353022df497`, first present in `8e4f00ee`. Repository code and documentation are MIT. The blob has no accompanying build receipt sufficient to reconstruct its original inputs. Local immutable-SQLite counts, clean wheel contents and synthetic HTTP observations are recorded in `raw/fallback-baseline.json` (accessed 2026-11-19). |
| Reference against which the historical rs1051266 protein name disagrees | dbSNP:rs1051266, RefSeq:NM_194255.4, RefSeq:NP_919231.1 and UniProt:P41440, sequence version 3. Re-read the dated public [nomenclature packet](../2026-08-10-slc19a1-rs1051266-nomenclature-2023/README.md) and its [RefSNP transcript extract](../2026-08-10-slc19a1-rs1051266-nomenclature-2023/raw/dbsnp-refsnp-1051266-nm194255-extract-2026-08-10.json) (accessed 2026-11-19, original retrieval 2026-08-10). NCBI molecular-data terms apply to the RefSeq/RefSNP records. UniProtKB supplies attribution under CC BY 4.0. The separate GRCh37 Ensembl queries in `raw/source-metadata.json` bind the sentinel to ENST00000311124.4 and ENSP00000308895.4, including genomic strand, transcript strand, alternate, coding change and protein change (accessed 2026-11-19). The old row identifies the transcript without a version. The databases report related reference curation. The earlier packet documents their shared upstream records and the exact alternate concerned. No risk, medication or phenotype assertion is inferred. |
| Which assembly to pin | GenBank:GCA_000001405.14 and RefSeq:GCF_000001405.25, **GRCh37.p13**. Ensembl GRCh37 REST returned the assembly name, accession and default coordinate system. NCBI confirmed the pairing in its assembly report. The two hosts describe the same assembly. Retained field selections, the NCBI report header and exact request URLs are in `raw/source-metadata.json` (accessed 2026-11-19). [NCBI assembly](https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_000001405.25/) |
| What can produce allele-specific annotations | McLaren et al., *The Ensembl Variant Effect Predictor*, 2016. DOI:10.1186/s13059-016-0974-4, PMID:27268795, PMCID:PMC4893825 (accessed 2026-11-19). The primary software paper describes transcript consequence calculation, the sensitivity to transcript choice/version and offline input. Article CC BY 4.0. It describes the method, with no certification of these application artifacts. [Primary publication](https://doi.org/10.1186/s13059-016-0974-4) |
| Tractable build inputs | Ensembl release **112** and VEP commit `31a3581b84495b617b2f3980da6c6313ca6d238f`. The release-112 FASTA and variation archive listings include the primary-assembly FASTA, `seq_region.txt.gz` and `variation_feature.txt.gz`. The release-112 GRCh37 VEP cache responded to HEAD with size 16,737,197,552 bytes. The listings' checksum lines and cache headers are retained in `raw/source-metadata.json` (accessed 2026-11-19). These checks identify available inputs. Obtain strong per-input digests before a rebuild. The current general cache documentation can have a different release from these pinned inputs. [Ensembl offline cache guidance](https://grch37.ensembl.org/info/docs/tools/vep/script/vep_cache.html) |
| Release integrity and original catalog | Yeliztli release `bundle-v4.0.0`, asset 463821245, build script `c541f24ad9b700823e151f72c7c7444b7e7b6c7d`. The published asset's digest and size match `bundles/manifest.json`. Release notes identify the union catalog by SHA-256 `544295b6813fb5a288e1824f4ab9e29824dd70ebc5027b9d2db8fdbbd3536317` and give its rsID denominator. Derived catalog availability and redistribution rights still require verification before reproducing/distributing a full or partial artifact. `raw/release-asset.json` contains the selected GitHub fields (accessed 2026-11-19). A fresh download matched the published digest and embedded version. The size and overlap calculation uses the authenticated bytes. The full artifact still lacks a row-by-row independent scientific audit here. [Release record](https://github.com/bioedca/Yeliztli/releases/tag/bundle-v4.0.0) |

Ensembl places no restrictions on project-generated data and licenses its own code under Apache 2.0. The distribution can incorporate components with third-party restrictions. NCBI imposes no restriction on redistribution of molecular data and notes that submitters may hold rights. Before shipping a derived pack, list each selected input and component, its license and attribution, the catalog's provenance and redistribution basis. Restrict a release to inputs with established redistribution terms. These are separate questions from whether a URL resolves. [Ensembl data and software terms](https://www.ensembl.org/info/about/legal/disclaimer.html) (accessed 2026-11-19). [NCBI data policies](https://www.ncbi.nlm.nih.gov/home/about/policies/) (accessed 2026-11-19).

## Discovery and notices

`raw/discovery.json` records the literature queries and field-selected result. Consensus and Scite located the same primary paper. The exact DOI lookup returned one Scite record with no editorial-notice field. PubMed efetch returned its publication types and zero linked correction/comment records. Crossref returned bibliographic metadata and a preprint relation with no update relation. The notices check is dated and uses records for this paper only. `raw/source-metadata.json` preserves the source selections and request URLs (accessed 2026-11-19). The NCBI Datasets report request returned an empty object here, so the NCBI assembly report header supplies the accession evidence. The VEP cache listing lacked a matching checksum line, so only its response headers are recorded.

## Reproduce the measurements

Install the development dependencies in a clean checkout at the recorded revision. For the historical revision, copy this packet into the same repository-relative location shown below. Run the commands from the root of the editable checkout. The route probe uses a fresh temporary data directory and local manifest, disables external socket connections, and runs two synthetic calls through the same Huey task that invokes the production annotator and writes each sample’s version. It reads both variant detail and the VCF export endpoint through the normal routers. It sends diagnostics to stderr and writes only the measurement JSON to stdout. The known incorrect name is a historical observation and must never be used as the scientific expected result for a repaired release.

```bash
python -m pip install -e ".[dev,docs]"
python data/science-evidence/2026-11-19-vep-offline-fallback-2715/probe_builder.py
python data/science-evidence/2026-11-19-vep-offline-fallback-2715/probe_offline_route.py
# Supply the public v4.0.0 release asset identified in raw/release-asset.json.
python data/science-evidence/2026-11-19-vep-offline-fallback-2715/probe_release_projection.py path/to/downloaded/vep_bundle.db
python -m pip wheel --no-deps . --wheel-dir output/wheel-probe
python -m zipfile -l output/wheel-probe/yeliztli-0.2.0-py3-none-any.whl
```

For the historical inventory, open the Git copy with SQLite URI options `mode=ro&immutable=1` and run the following query. Count actual nonempty annotations, including both coding and protein fields. The whole-file SHA-256 binds these counts to the retained baseline. The wheel inventory should include `backend/` and has no member named `vep_bundle.db`.

```sql
SELECT count(*), count(DISTINCT rsid),
       sum(length(ref) = 1 AND length(alt) = 1),
       sum(length(ref) = 1 AND length(alt) = 1 AND ref < alt),
       sum(length(coalesce(hgvs_coding, '')) > 0),
       sum(length(coalesce(hgvs_protein, '')) > 0)
FROM vep_annotations;
SELECT key, value FROM bundle_metadata ORDER BY key;
```

`probe_builder.py` exercises the real vendor-input generator with three invented calls and both real SQLite builder entry points with three invented site records each. The expected genomic reference comes from an artificial four-base string specified independently of the builder's input. In the vendor-input path both heterozygous orderings give the same incorrect reference for that toy site. In the builder both a reversed allele pair and a coding-only reversal survive serialization. Exact expected tuples and cardinality discriminate all six cases. The production verifier proposed for #2299 needs the versioned genomic and transcript checks in the design. The historical file's build provenance remains unknown.

The release projection opens both pinned databases read-only, joins on rsID and GRCh37 position, and constructs a temporary in-memory table with the two runtime indexes. Its measured size excludes bundled metadata and offline verification records. The two source digests, counts and site reconciliation appear in `raw/release-asset.json` and `raw/fallback-baseline.json`. The Chromium witness rendered the response from a loopback instance of the real variant-detail router over the worker-produced sample. Setup, sign-in status and ancillary navigation calls used the existing synthetic browser-test conventions. The Overview and Protein tabs both displayed the measured primary protein. The Overview pane had zero axe violations in that check. Browser logs and temporary sample databases are local test artifacts.
