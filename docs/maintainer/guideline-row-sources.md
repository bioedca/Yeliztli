# Source identity for shipped prescribing-guidance rows

**Proposed decision for #2468.** Show a short drug recommendation only when
the product can identify its source and establish that the source covers
the available inputs. Keep the authority's record and the application's
display text independently identifiable. This decision would govern the
offline conformance guard in #2135 and the source labels in #2467.

## What ships and what CPIC returns

At `cf08f712a130591598eae00648a1b8dce2d0b384`, the production CSV has
81 rows for 24 distinct gene-field/drug pairs across 18 drugs. Three of
those pairs use the combined local gene label `TPMT/NUDT15`. On 2026-11-19
the CPIC v1 recommendation endpoint returned 304 distinct records across
the same drugs. The inventory compares the local gene's phenotype string
with the same gene's entry in CPIC's phenotype map, then compares the
recommendation text byte for byte. This first pass deliberately ignores
other lookup inputs so it can expose ambiguity.

| Result of this limited comparison | Shipped rows |
| --- | ---: |
| At least one candidate with identical text | 6 |
| At least one candidate, all with different text | 63 |
| No literal gene/phenotype candidate | 12 |

Of the 63 in the second group, 32 span more than one distinct source text.
Even the six exact strings need a full applicability review. A single-gene
TPMT row can match the text of multiple joint records while supplying no
NUDT15 result. The 12 unmatched rows comprise three combined thiopurine
rows, three case-sensitive SLCO1B1 phenotype spellings, three warfarin rows
and three FDA-linked irinotecan rows. The last two drugs returned zero
recommendation records. Warfarin still has a CPIC guideline URL in its pair
record. The October count of zero ondansetron records has changed to 22.
Its distinct content discrepancy is tracked in [#2720](https://github.com/bioedca/Yeliztli/issues/2720).

## Recommended representation

Use **verbatim text by default with explicitly reviewed summaries**. Verbatim
records make the source wording directly comparable offline, but reusing a
record requires the correct population and complete lookup key. Summaries
can fit the interface and combine equivalent cases, at the cost of human
review each time a contributing source or the summary changes. A text
similarity threshold or an allowlist of today's differences cannot supply
that judgment.

| Field group | Meaning and verification |
| --- | --- |
| Source authority and document | Distinguish CPIC guidance, FDA material and local editorial text. Keep the authority's stable document or table identifier, citation, human-facing URL and exact machine retrieval URL/date/version. A CPIC pair entry by itself cannot make FDA the author of a CPIC recommendation or vice versa. |
| Applicable result | Store drug identifier, complete gene/allele-status or activity-score key, phenotype map, population and any additional qualifiers in the guideline. Distinguish an explicit indeterminate input from a missing partner, population or score. An exact phenotype string alone cannot certify equivalence. |
| Source row | For each CPIC recommendation, retain its ID, guideline ID, record version, full context, strength and a digest of source text. Preserve distinctions such as “No Recommendation” and missing or inapplicable values. CPIC's separate gene–drug pair level belongs to the pair. FDA material has its own attribution and location; omit a CPIC recommendation strength where none exists. |
| Display row | Mark the mode as source wording or an application summary. Verbatim mode requires the full selected context and exact text. A summary needs its own revision and text digest, all covered source IDs, the scope and exclusions, and a recorded clinical equivalence decision. |
| Unrepresentable or unavailable guidance | Preserve the available source link and show that guidance is unavailable for these inputs. A zero API response, missing key or failed source request cannot produce a default treatment instruction. A source change or incomplete retrieval anywhere in a mapping invalidates its previous conformance verdict. |

A reviewed summary must preserve the action, qualifying conditions, exceptions,
recommendation strength and follow-up instructions. Clinical review must
explicitly cover every member of any many-to-one mapping, including mixed
strengths and the original regimen or indication where applicable. Keep
non-equivalent records distinct. Do not infer a population, default a partner
gene, infer an activity score from a broad phenotype, or reinterpret missing
source fields. The unresolved-input policy in [#2401](https://github.com/bioedca/Yeliztli/issues/2401)
and the existing tamoxifen evidence hold apply independently.

The user-facing link for CPIC guidance should come from the verified guideline
record or pair's current human-facing URL. Keep the prior URL and the precise
API URL in provenance. For FDA-sourced rows, use the official FDA document
and the relevant table section or label revision as the link and anchor. A
pair-level CPIC value can appear as a separately attributed metadata field.
The current pair table has level A where three shipped gene/drug pairs show
B (CYP2D6/tramadol, CYP2D6/ondansetron and UGT1A1/irinotecan). The FDA row
illustrates why a pair-level discrepancy and authorship need separate fixes.

## Delivery and acceptance

1. Curate full mappings independently of the production CSV, seed and mini
   fixtures. Start with one fully representable record and one intentionally
   unresolved record. Verify the actual recommendation returned by the normal
   scoring, storage and API path, including the absence of actionable text
   and a source-specific reason in the unresolved case.
2. Keep an immutable reviewed snapshot for offline CI. Regeneration uses a
   separate online review step. Verify record identity, context, text digest,
   strength, pair level, source authority, attribution links and local summary
   revision. Changed or removed records, new overlapping contexts, empty or
   truncated responses and a changed display row require adjudication. The
   guard must fail for an isolated wrong value even when all repository copies
   agree. Include a correctly classified control and a non-carrier control
   wherever finding emission is carriage-gated.
3. Exercise adult/pediatric, activity-score and joint-gene lookups, a
   missing partner, case-normalization collisions, the combined local label,
   an FDA-only row and an empty API result with a separately published
   guideline. Migration must preserve existing installs across retries and
   keep source-native revisions distinct from the local content revision.
4. Coordinate stored-result delivery with #2304, #2454 and #2705. A pure
   wording change may use an approved projection only where source mapping
   and applicability still match. A changed decision needs a verified re-run
   before presenting it as current in findings, reports or exports. No row
   obtains clinical approval from this inventory or from a passing test.

The dated public evidence and per-row comparison are in
`data/science-evidence/2026-11-19-guideline-row-sources-2468/`. Maintainer
approval is requested for the display modes, required evidence and handling
of unsupported contexts before implementing #2135 or changing #2467 data.
