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
`SO:NNNNNNN|term` entries are decoded into the existing consequence token format,
without deriving consequences from indel lengths or selecting a transcript.
VEP's existing selected consequence takes precedence. Ambiguous allele selection,
noncanonical multi-ALT records, malformed MC and mismatched VEP identities do not
supply an effective fallback. Source attribution is `clinvar` or `vep`; historical
unknown provenance stays NULL. The MC fallback does not set a VEP coverage bit,
transcript, HGVS, gene or inheritance.

All tests use synthetic loci/calls. Indel I/D carriage remains unscoreable; an
absent VEP gene is separately tracked in #2464. The production FH regression uses
a synthetic sequence-resolved SNV with source `stop_gained`, an already-known
PCSK9 gene and an explicit hom-ref negative control. This tests the existing
clinical software rule, not the biological truth of a synthetic variant.
