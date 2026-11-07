# ClinVar molecular-consequence fallback (#2393)

Accessed 2026-11-07. Public reference metadata only; no sample data or genotypes.
This packet supports source-field semantics and the public issue witness. It does
not establish a new clinical effect or extend the existing APOB/PCSK9 rules.

## Queries and discovery

Consensus and Scite were queried first for ClinVar molecular-consequence/VCF MC
metadata; sanitized queries and citation metadata are in
`raw/discovery-metadata.json`. The Consensus result for the selected paper was
fetched before use. Scite verification of its DOI reported a verified paper and
no editorial notice or contrasting evidence (`raw/scite-verification.json`).
These tools are discovery aids, not scientific sources.

The ClinVar Variation skill's beta lookup for variation 431555 returned HTTP 404;
Clinical Tables search for VCV000431555 returned zero records, which is not proof
of absence. The NCBI Entrez skill successfully retrieved ClinVar ESummary 431555,
PubMed EFetch 29165669, and PMC EFetch 5753237. The public ClinVar record page also
confirmed the identity. Browser access to the PMC article/help pages encountered
a challenge; Entrez supplied the public article through its documented endpoint.

## Sources and claim mapping

1. **MC is an NCBI-predicted molecular consequence, including an SO identifier and
   term, rather than a submitted clinical classification.** Landrum MJ et al.
   *ClinVar: improving access to variant interpretations and supporting evidence.*
   Nucleic Acids Research. 2018;46(D1):D1062–D1067 (online 2017).
   [PMID:29165669](https://pubmed.ncbi.nlm.nih.gov/29165669/),
   [DOI:10.1093/nar/gkx1153](https://doi.org/10.1093/nar/gkx1153),
   [PMCID:PMC5753237](https://pmc.ncbi.nlm.nih.gov/articles/PMC5753237/)
   (accessed 2026-11-07).
   Source copies: `raw/clinvar-paper-pubmed.xml` and
   `raw/clinvar-mc-definition.xml`. The latter preserves the source paragraph and
   license; the former omits abstracts, authors and affiliations. The article is
   a US government work in the public domain in the US. PubMed's returned record
   contains no linked correction/retraction; Scite reported no editorial notice
   as of this access date. This is a bounded database check, not proof that no
   future notice will appear.
2. **The issue's public variant has a source-native frameshift annotation.**
   [ClinVar:VCV000431555.1](https://www.ncbi.nlm.nih.gov/clinvar/variation/431555/)
   (accessed 2026-11-07), also dbSNP:rs371488778. ESummary reports
   NM_174936.4(PCSK9):c.42_43insTG (p.Leu15fs), an insertion, and
   `molecular_consequence_list = ["frameshift variant"]`.
   `raw/clinvar-431555-esummary.json` preserves selected returned identity,
   molecular-consequence and build fields; classifications, submissions,
   frequencies, genes and other unnecessary fields were omitted. ClinVar's
   public NCBI data-use terms apply. No individual-level record was retrieved.
   GRCh37 coordinates are 1:55505552–55505553, assembly GCF_000001405.25;
   source-native GRCh38 coordinates and SPDI are retained for audit only.
   Production joins continue on GRCh37/b37.

These sources share NCBI upstream annotation and are **not independent**. The
paper defines the field and the registry implements it; this meets the contract's
narrow definitional exception. They are not counted as independent evidence for
biological effect. Existing clinical rules and their independent source mapping
remain in `data/science-evidence/2026-10-11-pcsk9-ptv-low-ldl-2064/README.md` and
`data/science-evidence/2026-07-31-apob-low-ldl-2075/README.md`; this change does not
expand their effect claims or treat a ClinVar classification as mechanistic proof.

## Software contract and limits

The VCF MC field is retained verbatim for a resolved single ALT. Valid
`SO:NNNNNNN|term` entries are decoded into the existing consequence token format (with verified SO
canonicalization), without deriving consequences from indel lengths or selecting a transcript.
VEP's existing selected consequence takes precedence. Ambiguous allele selection,
noncanonical multi-ALT records, malformed MC and mismatched VEP identities do not
supply an effective fallback. Source attribution is `clinvar` or `vep`; historical
unknown provenance stays NULL. The MC fallback does not set a VEP coverage bit,
transcript, HGVS, gene or inheritance.

All tests use synthetic loci/calls. Indel I/D carriage remains unscoreable; an
absent VEP gene is separately tracked in #2464. The production FH regression uses
a synthetic sequence-resolved SNV with source `nonsense` normalized to `stop_gained`, an already-known
PCSK9 gene and an explicit hom-ref negative control. This tests the existing
clinical software rule, not the biological truth of a synthetic variant.

## Canonical terminology verification after local review

Additional discovery queries were `Sequence Ontology SO:0001587 stop_gained
nonsense ClinVar molecular consequence terminology` (Consensus) and
`"Sequence Ontology" AND "ClinVar"` (Scite). Scite surfaced
DOI:10.1186/s13326-015-0030-4 and the already-retrieved ClinVar paper; no new
biological-effect claim was taken from either discovery result.

The authoritative [Sequence Ontology source](https://github.com/The-Sequence-Ontology/SO-Ontologies/blob/master/Ontology_Files/so.obo)
(accessed 2026-11-07), data version **2026-08-07**, Git blob
`8b9442f40c5eb2dc32e02c0d632c0301ba6fa2ea`, defines **SO:0001587** as
`stop_gained` and includes `nonsense` as an exact synonym. It defines
**SO:0001619** as `non_coding_transcript_variant`, **SO:0001623** as
`5_prime_UTR_variant`, and **SO:0001624** as `3_prime_UTR_variant`.
`raw/sequence-ontology-terms.obo` retains selected IDs, names and synonyms,
including the existing frameshift token. Attribution: The Sequence Ontology;
[CC-BY-SA-4.0](https://creativecommons.org/licenses/by-sa/4.0/), as declared by
its repository README (blob `24c383e00788f21ef61c4a58dc016f5a80fe13a3`). The
extracted ontology metadata remains under that license.

[ClinVar:VCV000135067.54](https://www.ncbi.nlm.nih.gov/clinvar/variation/135067/)
(accessed 2026-11-07) confirms the source spelling `nonsense`
alongside `non-coding transcript variant` in its molecular-consequence list.
This is an implementation cross-check, **not independent evidence for an effect**:
ClinVar uses the SO vocabulary. The defining ontology plus implementing registry
meet the same narrow definitional exception. The Entrez query
`RCV003997356[accession]` returned zero; the unfielded `RCV003997356` query resolved
to variation 135067. `raw/clinvar-135067-esummary.json` contains only selected
public identity and terminology fields. The web term/record pages were unavailable;
the official GitHub ontology blob and NCBI Entrez responses supplied the evidence.

A bounded 256 KiB HTTP range request to the official GRCh37 ClinVar VCF confirmed
the current MC header syntax and source dates (fileDate 2026-11-01, Last-Modified
2026-11-02). `raw/clinvar-vcf-mc-metadata.json` retains only headers and request
metadata; this prefix contained no MC-bearing variant examples. No genotype or
variant rows from the VCF were retained. Normalization keys on the SO identifier
for the two differing source labels; raw MC is retained separately. Valid uppercase,
digit-prefixed and hyphenated labels do not invalidate a combined consequence list.

Resolved VEP and ClinVar identities are separately persisted as source-native
(chromosome, position, reference, alternate) tuples. A standalone refresh cannot
use ClinVar's own alleles as evidence that a retained VEP gene describes that same
allele. Legacy rows without the identity stay unproven until re-annotation.
