# MONDO/HPO installed-baseline study (#2707)

Across 14 public release triplets, 58.80% to 87.81% of gene–disease scopes in the HPO export matched the Monarch records through the production join. The largest intact drop in calibration was 12.6506 percentage points. A 13-point tolerance alone missed complete loss of Orphanet mappings on all five adjacent held-out refreshes. Tracking whether a previously matched namespace still has any matches caught all five. The decision and persistence design are in [the maintainer proposal](../../../docs/maintainer/mondo-hpo-refresh-baseline.md).

## Source selection and controls

We selected the last dated Monarch KG release in each available calendar month from September 2025 through November 11, 2026. October 2025 has no dated entry in that catalog. For each KG day we took the most recently published stable dated HPO and MONDO releases available by the end of that day (UTC). The 36 distinct versioned input URLs total 354,401,615 bytes. Each entry in [the source register](raw/sources.json) carries its version, publication date, URL, measured byte length and SHA-256. The register also preserves the upstream HPO asset digest when supplied. All registers and articles were accessed 2026-11-19.

The first nine triplets through June are the calibration set. The last five form a chronological holdout. For each release, `measure.py` parses the public Monarch human gene–disease TSV, HPO `genes_to_phenotype.txt` and MONDO SSSOM mapping with the loader at commit `7be123f382be44cc3c0936d2787038886b6488f7`. The denominator counts distinct gene and source-disease pairs with HPO annotation. A join succeeds when the loader can resolve the source disease to a Monarch disease for the same gene. Five triplets overlap #2285. All 15 input hashes and all ten intact counts agree, including the October snapshot now identified by a dated MONDO tag.

One intact and five damaged cases are measured per release. The damaged cases remove exact identifier mappings after production filtering. They omit all OMIM or all Orphanet identifiers, keep about half the Orphanet identifiers, or keep about 75% or 90% of identifiers across all namespaces. The latter three use stable buckets from the first eight bytes of SHA-256 of UTF-8 `2707:<source identifier>`, read big-endian modulo 100. Each HPO source file stays intact. The earlier #2285 experiment sampled source **rows**. These experiments sample parsed **identifiers**, so their losses have distinct denominators. An omitted HPO source namespace itself, ambiguous mappings, newly introduced identifiers and repeated small drops remain separate failure modes.

## Reproduction

From the repository root in the project Python environment, set `packet` as below. Use a new local cache with sufficient space. The loop fetches only versioned public resources listed in the committed register. Each analysis opens and verifies all input lengths and hashes before reading the data. The two analysis scripts use local files throughout.

```bash
set -e
packet=data/science-evidence/2026-11-19-mondo-hpo-refresh-2707
cache=$(mktemp -d)
while IFS=$'\t' read -r url digest; do
    curl --fail --location --silent --show-error "$url" -o "$cache/$digest"
done < <(jq -r '.files[] | [.url, .sha256] | @tsv' "$packet/raw/sources.json")
python "$packet/measure.py" "$cache" > "$cache/measurements.json"
cmp "$cache/measurements.json" "$packet/raw/measurements.json"
python "$packet/compare.py" > "$cache/comparisons.json"
cmp "$cache/comparisons.json" "$packet/raw/comparisons.json"
```

The measurement required about 39 seconds on one CPU with Python 3.12.16. [Measurements](raw/measurements.json) contain integer counts by release, perturbation and namespace. [Comparisons](raw/comparisons.json) contain refusal counts, exact calibration maximum and rounded ranges. The chronology, inputs, integer calculations and synthetic selection determine the result. No patient observations enter the experiment. Reused exports and upstream curation make pairwise comparisons dependent. The counts describe this sample and do not estimate a future false-refusal probability or clinical completeness.

## Attribution, rights and evidence

- This study uses the Human Phenotype Ontology Consortium's dated releases listed in the register. The project requests attribution, release identification, preservation of HPO content and citation of its foundational paper, DOI:10.1093/nar/gkt1026 (accessed 2026-11-19). The tag license files refer to the HPO website. [Dated terms excerpt](raw/hpo-license-2026-11-19.json) · [Project terms](https://human-phenotype-ontology.github.io/license.html)
- The MONDO project repository reports CC BY 4.0 for all sampled tags. Each actual SSSOM export declares an unspecified mapping license in its header. Monarch's project guidance recommends CC0 or CC BY for its own data and defers to repository terms. Source-specific rights in the integrated KG export remain unresolved. The packet distributes counts, public metadata and code. [License records and artifact declarations](raw/licenses.json) · [Dated Monarch excerpt](raw/monarch-license-2026-11-19.json) · [Monarch guidance](https://monarch-app.monarchinitiative.org/Licensing/)
- The MONDO resource paper describes terminology integration, PMID:41052288 / DOI:10.1093/genetics/iyaf215 (accessed 2026-11-19). The HPO resource paper describes its annotations, PMID:37953324 / DOI:10.1093/nar/gkad1005 (accessed 2026-11-19). The Monarch resource paper describes the integrated graph, PMID:38000386 / DOI:10.1093/nar/gkad1082 (accessed 2026-11-19). These sources identify the inputs. Every rate and tolerance here comes from the versioned measurements. [MONDO](https://pubmed.ncbi.nlm.nih.gov/41052288/) · [HPO](https://pubmed.ncbi.nlm.nih.gov/37953324/) · [Monarch](https://pubmed.ncbi.nlm.nih.gov/38000386/)
- [Sanitized discovery queries and results](raw/discovery.json) and [NCBI Entrez search and fetch metadata](raw/pubmed-resources.json) record the provenance of the three resource papers. None had a linked correction or retraction in the fetched records or an editorial notice in the DOI check on the access date. The foundational HPO citation comes from its project terms.

The [dated Entrez EFetch XML excerpt](raw/pubmed-efetch-excerpt-2026-11-19.xml) preserves the source PMID, article title, publication types and article identifiers for each record. The projection retained every original MedlineCitation/CommentsCorrectionsList if present. There were none. It omits author information, abstracts, references and journal metadata. Compare the original-response digest in the search and fetch record. Both license excerpts are literal selections from responses matching the hashes recorded for the project pages.
