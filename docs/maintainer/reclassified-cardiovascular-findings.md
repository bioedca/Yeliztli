# Showing a cardiovascular finding outside FH scope

**Proposed for clinical and product review in #2412.** A person reviewing a
cardiovascular finding needs to distinguish what ClinVar reports about the
variant from why Yeliztli excluded it from familial hypercholesterolemia (FH).
Use the same presentation for an APOB or PCSK9 finding that the existing
truncation rule assigns to the lipid category.

## What the person would see

For a synthetic PCSK9 finding with a stop-gained consequence and a ClinVar
record listing both FH and hypocholesterolemia, show:

| Place | Proposed text or content |
| --- | --- |
| Card title | **PCSK9 truncating variant — low-LDL interpretation** |
| Interpretation | **Excluded from the FH variant count.** Yeliztli applies its PCSK9 truncation rule here. This is a direction of effect, not a measured LDL value or a diagnosis. |
| Evidence panel, ClinVar | **Variant-level ClinVar classification: Pathogenic.** Show the accession/version as supplied, review status or stars, and the complete reported condition list under a separate label. If the import has no classification for a particular condition, say **Condition-specific classification unavailable.** |
| Evidence panel, Yeliztli | Show the consequence and its provenance where known, the reason for the lipid category, and the PCSK9 low-LDL references. Label these as evidence for the interpretation. |

For APOB, substitute APOB and its own rule and citations. The card title
never attaches “Pathogenic” to a selected low-LDL condition. If ClinVar lists
only FH, no conditions, conflicting terms or “not provided”, the proposed
interpretation still follows the existing qualifying consequence. Preserve
the source value and describe missing fields as unavailable. Never create a
condition, a condition-specific assertion or a submitter from an aggregate.

A ClinVar VCV combines submissions for the variant across conditions. RCV
identifies a variant–condition aggregate and SCV identifies an individual
submission. A ClinVar label may be paired with a specific condition only
when the actual record, accession and scope establish that association.
The existing flat fields can be displayed as source evidence without
recovering a submitter-specific interpretation. [ClinVar data model](https://www.ncbi.nlm.nih.gov/clinvar/docs/data_model/)

## Decision and data contract

Choose the issue's **separate functional direction** option together with
**classification-scoped references**. Keeping the raw headline with a caveat
leaves the unsupported condition/label pairing in summaries where the note
is easily omitted. Replacing ClinVar's significance or removing the source
conditions would lose the evidence needed to audit the disagreement.

1. Preserve the existing raw `clinvar_significance`, `clinvar_conditions`,
   accession and stars. In the current storage model, `findings.conditions`
   holds the complete source condition string and `detail_json.conditions`
   holds display terms. Preserve these meanings for existing API clients.
2. On scoring, identify the **reason** for the existing reclassification and
   persist it with its presentation revision, gene, applicable consequence
   and source. The lipid category alone is ambiguous: the module can reach
   it through low-LDL-only condition text as well as through a truncating
   consequence. Apply this decision to the truncation path. Keep the
   existing APOB versus PCSK9 consequence sets, compound-term handling,
   carriage, confidence and disclosure gates.
3. Store and expose one derived summary and the gene-specific sources that
   justify it. Leave source-level citations with their source record when
   available. The panel's general FH references do not become citations
   for the low-LDL interpretation. Existing lower-penetrance qualifiers
   and their own references must survive, with their scope labeled. No
   evidence-level or review-star upgrade follows from reclassification.
4. In the structured module response, return the interpretation and its
   provenance alongside the existing raw fields. For example, the same
   response can carry `clinvar_significance = Pathogenic`, raw conditions
   including FH, and a separate lipid interpretation with its reason and
   references. A missing raw field stays missing. Keep an unknown source
   scope explicit. Do not fabricate an RCV or SCV for a VCV.

## Consumers and stored findings

| Consumer | Acceptance condition |
| --- | --- |
| Cardiovascular variants and gene endpoints, cards and detail panel | Render the same interpretation headline for both genes. Show the full source block separately. The category, significance badge, evidence stars, citation list and surrounding page heading must not imply that ClinVar asserted the derived condition. |
| FH status and the separate FH module | A reclassified variant contributes zero FH variants. An independently qualifying LDLR variant in the same synthetic sample still contributes one. Preserve existing handling of gated and unavailable results. |
| All Findings, module summaries, HTML/PDF reports and single-variant evidence cards | The stored finding text, top finding and any derived summary use the new headline. A generic ClinVar badge or raw condition list must be labeled with its scope wherever shown. Preview and final output must agree. |
| Data export | For finding-based output, carry the same scoped text and separate provenance. FHIR variant observations and variant-query/VCF exports originate from variant annotations. Preserve original annotation semantics, filter by the existing disclosure rules and never manufacture a low-LDL disease assertion in a source field. No additional access through SQL or module filters. |

Existing samples contain copied text and citations. On upgrade, mark the
affected cardiovascular analysis presentation as needing regeneration. An
old lipid row may lack the decision reason or a trustworthy consequence.
Do not infer the new headline by splitting old prose, dropping an FH
condition, or guessing from the gene and category. If current annotated
inputs can establish the rule, regenerate through the same scoring path
and atomically replace that module's findings. If they cannot, withhold the
potentially misattributed derived text and references in every reader and
show the reanalysis/unavailable state. Keep separately verifiable source
metadata available under the existing disclosure policy. Coordinate the
revision and retry mechanism with [#2705](https://github.com/bioedca/Yeliztli/issues/2705).
An interrupted or repeated rerun must not publish mixed revisions or
duplicate rows. Previously downloaded reports cannot be rewritten; a new
report must use the current state.

## Synthetic acceptance cases

- For **both** genes, use a carried, qualifying truncation with aggregate P/LP
  and mixed FH/low-LDL terms. Assert the exact neutral headline, FH exclusion,
  the unaltered source values and the correct gene's low-LDL citations.
  Repeat with only FH terms, low-LDL terms alone, and an empty condition
  string. No patient-specific lipid level or diagnosis should appear.
- Distinguish APOB's qualifying splice terms from a PCSK9 splice term alone.
  Include a compound consequence from an eligible source, a nonqualifying
  missense call, and a look-alike consequence token. Include an uncertain
  significance outside the module's existing eligibility and a `hom_ref`
  non-carrier at the same site. Check the negative rows are absent and the
  unaffected qualifying control is present, including in FH status.
- Exercise the API, visible card, evidence drawer, summary and report/export
  payloads before migration, while stale, following a successful rerun, and
  following a second rerun. Verify a missing legacy reason and malformed
  legacy detail fail safely while an unrelated module and a valid raw
  annotation retain their original behavior.

**Decision requested.** Record approval of the neutral headline, separate
source assertion and gene-specific references, and the interim handling of
old rows before implementing this user-facing change. The [evidence packet](https://github.com/bioedca/Yeliztli/tree/main/data/science-evidence/2026-11-19-fh-presentation-2412)
maps the existing rules to their sources and sets out their limits.
