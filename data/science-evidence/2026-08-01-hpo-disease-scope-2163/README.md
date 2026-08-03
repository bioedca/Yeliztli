# HPO disease-scope evidence for issue #2163

## Scope

This packet supports a narrow reference-data integrity change: HPO
`genes_to_phenotype` annotations retain their source `disease_id`, and the
loader attaches them to MONDO disease rows only through either a direct valid
MONDO identifier or an authoritative unambiguous MONDO `skos:exactMatch`
cross-reference. It does not make a diagnosis, estimate risk, or assert a
clinical association for an individual.

Repository base: `813d7568b286f2d76c2338a13f035aa3d8632234`

Packet created: `2026-08-01`

On `2026-08-02`, each retained source-native excerpt was checked against its
public endpoint, and the source validators, release metadata, checksums, and
transfer sizes in [`source-manifest.json`](source-manifest.json) were refreshed.
The manifest distinguishes those compact retained artifacts from the complete
remote payloads whose hashes and sizes were observed but which are not
redistributed in this packet.

## Sanitized discovery and validation queries

Consensus:

> "The Human Phenotype Ontology in 2021"

The returned scholarly record was Köhler *et al.*, “The Human Phenotype
Ontology in 2021” (result ID `7da056f699e856e88482ad67b081263f`).

Scite:

> DOI 10.1093/nar/gkaa1043

The connector returned `INVALID_ARGUMENT` on two sanitized query attempts.
Scite therefore did not contribute evidence to this packet.

NCBI Entrez:

- `efetch`, database `pubmed`, ID `33264411`, XML, to identify the paper and
  inspect the `CommentsCorrectionsList`/retraction-related elements.
- The response hash and the minimal public metadata retained from that query
  are in [`pubmed-33264411-sanitized.json`](pubmed-33264411-sanitized.json).

## Primary and authoritative sources

1. Köhler S, et al. “The Human Phenotype Ontology in 2021.”
   `PMID:33264411`; `PMCID:PMC7778952`; `DOI:10.1093/nar/gkaa1043`
   (accessed 2026-08-01).
   - Supports the use of HPO as disease-associated phenotype data; it does not
     authorize treating annotations for distinct diseases of one gene as
     interchangeable.
2. Monarch KG, `gene_disease.9606.tsv.gz`
   <https://data.monarchinitiative.org/monarch-kg/latest/tsv/gene_associations/gene_disease.9606.tsv.gz>
   (accessed 2026-08-02).
   - This is the primary source for canonical MONDO disease rows. Its current
     release metadata, validators, content hash, and a small public,
     source-native excerpt are recorded in
     [`source-manifest.json`](source-manifest.json) and
     [`monarch-gene-disease-excerpt.tsv`](monarch-gene-disease-excerpt.tsv).
   - The observed compressed archive was 183,547 bytes. The loader requires at
     least 100,000 bytes before a replacement; that catches gross truncation,
     not semantic incompleteness.
   - The excerpt documents the schema and identifiers used by the loader; it
     makes no patient-level, diagnostic, or risk claim.
3. Human Phenotype Ontology, `genes_to_phenotype.txt`
   <https://purl.obolibrary.org/obo/hp/hpoa/genes_to_phenotype.txt>
   (accessed 2026-08-02; source snapshot and retained excerpt verified on that date).
   - Its six-column schema includes a `disease_id` for each gene/HPO term.
   - The observed export was 20,732,778 bytes. The loader requires at least
     10,000,000 bytes before a replacement; that catches gross truncation, not
     semantic incompleteness.
   - [`hpo-genes-to-phenotype-excerpt.tsv`](hpo-genes-to-phenotype-excerpt.tsv)
     is a small, public, source-native excerpt showing one gene with distinct
     OMIM and ORPHA disease identifiers.
   - The adjacent
     [`hpo-genes-to-phenotype-excerpt.metadata.json`](hpo-genes-to-phenotype-excerpt.metadata.json)
     records the displayed source version/date, unaltered-row selection,
     acknowledgement, and requested HPO citation. The `ORPHA:` value is copied
     verbatim; the loader changes only its namespace spelling to `Orphanet:`
     before an exact MONDO match.
4. MONDO SSSOM mappings,
   <https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv>
   (accessed 2026-08-02; source snapshot and retained excerpt verified on that date).
   - [`mondo-sssom-excerpt.tsv`](mondo-sssom-excerpt.tsv) preserves source-native
     `skos:exactMatch` rows for those identifiers. Broad/narrow mappings and
     source identifiers with more than one exact MONDO target are deliberately
     excluded by the loader.
   - The accessed export had 109,306 unambiguous exact source identifiers. The
     loader rejects fewer than 50,000 before replacing any installed rows, so a
     heavily truncated but parseable mapping file cannot erase most scoped HPO
     context.

The HPO export and MONDO SSSOM table are complementary authoritative source
files, not independent clinical cohorts. The implementation relies on their
explicit identifier relationship rather than a label, numeric-string, or
gene-level inference.

At runtime, each validated set of the three inputs is published as an immutable
content-and-validator-addressed source bundle. The installed database version
records that bundle's primary archive path only in the same transaction as its
disease rows, so a failed replacement cannot overwrite a prior provenance
record. A dedicated cross-process finalization claim serializes the bundle
snapshot, publish, row/version transaction, and retention decision. After a
successful replacement, the loader retains every validated bundle as
append-only provenance; incomplete, malformed, legacy, symlinked, and
operator-created directories are also not automatically removed. The verified
three-source transfer footprint is 33,992,110 bytes (about 34 MB) per bundle.
The configured downloads directory and its managed `mondo_hpo_sources` child
must be private to the updating account and not group/world-writable. Held
descriptors and the final pre-commit guard fail closed on detectable swaps, but
the entire lexical and resolved ancestor chain must also be non-writable by
group/other users and not writable by a different directory owner. A writable
sticky ancestor is accepted only when it is owned by the updating account or
root. An unknown owner is permitted only when the ancestor's device/inode
matches a fixed, descriptor-pinned system boundary (`/`, `/tmp`, `/var/tmp`, or
`/home`); the exception is never derived from `TMPDIR`. Every lexical
symbolic-link component must be owned by the updating account unless its exact
path and identity are one of those pinned system-boundary links. The
loader hashes each held source before parsing and rechecks the same bytes before
manifest creation and bundle publication. No filesystem primitive makes a
namespace controlled by an administrator, the platform temporary-directory
service, or the same privileged local owner an independently trusted provenance
boundary.

## Corrections, licensing, and data handling

The public PubMed XML response was retrieved on 2026-08-01 and searched for
`CommentsCorrectionsList`, `Retraction`, `Erratum`, and
`ExpressionOfConcern`; none was present in that response. This documents the
PubMed state on the access date only. The retained JSON intentionally excludes
publisher abstract text, author details, affiliations, and references.

The source excerpts contain only public ontology/reference identifiers and no
participants, genotypes, PII, credentials, restricted data, or private paths.
The full per-source versions, access dates, checksums, retained-artifact paths,
license status, and claim mapping are in
[`source-manifest.json`](source-manifest.json). Complete remote payloads are
not retained here: the packet instead records their public canonical URL,
observed size/hash/validators, and the reason for non-retention alongside a
small source-native excerpt or sanitized derivative.

HPO's project terms apply to all HPO files and require acknowledgement/citation,
the displayed source date or version, and unaltered HPO content. This packet
satisfies those excerpt-specific requirements through the adjacent HPO metadata
sidecar and keeps the TSV header and rows unchanged. The SSSOM artifact's own
header declared an unspecified license; MONDO's CC BY 4.0 download-page notice
is recorded only as project-level context, not as an artifact-specific license.
The primary Monarch aggregate export's source-specific license was likewise not
established, so this packet makes no artifact-license claim for it.
