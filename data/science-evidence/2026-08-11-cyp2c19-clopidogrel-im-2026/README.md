# CYP2C19 / clopidogrel Intermediate Metabolizer recommendation, for #2026

## Claim and scope

`backend/data/cpic/cpic_guidelines.csv` shipped `"Consider alternative antiplatelet
therapy."` for CYP2C19 Intermediate Metabolizer. This packet supports one factual
claim: **what CPIC actually recommends for CYP2C19 phenotypes on clopidogrel, and
at what strength.**

It asserts no independent biological or clinical judgement. The change transcribes
a guideline; it does not evaluate the evidence behind that guideline, and nothing
here should be read as this repository endorsing or re-deriving CPIC's position.

## Source, and why there is only one

- **CPIC API** — `GET https://api.cpicpgx.org/v1/recommendation?drugid=eq.RxNorm:32968`
  (accessed 2026-08-11). The **unfiltered** response is retained at
  `raw/cpic-api-clopidogrel-full-2026-08-11.json` — that file is the authority.
  `raw/cpic-api-clopidogrel-2026-08-11.json` beside it is a normalized subset kept
  only for readability; an earlier draft called that subset the full payload while
  applying a reduction rule, which would have stopped an auditor confirming that no
  applicable row was dropped. CPIC releases its guidelines and API
  under **CC0 1.0**.

**On the two-source rule: it is the wrong gate for this claim, and an earlier
draft of this packet mis-applied it.** That rule exists for facts whose truth is
established by evidence and where independent replication is therefore possible —
which allele raises risk, which direction an effect runs. This change asserts
something different: *that a named guideline says a particular thing.* CPIC is the
sole authority on CPIC's own recommendations, so the correct verification is not
corroboration but **faithfulness to the source**, and that is what was done — the
API payload is retained verbatim and the shipped rows transcribe it.

The guideline publication (PMID:35034351, DOI:10.1002/cpt.2526, indexed by PubMed
as a *Practice Guideline*) is **the same working group's same assertion** — it is
the paper the API serves — so it is used as a **cross-check against API or
transcription error**, not as corroboration. Its abstract does confirm the
direction of travel, listing *"increased strength of recommendation for CYP2C19
intermediate metabolizers"* among the 2022 update's changes: exactly the upgrade
the shipped text had not taken up.

Writing that the rule "cannot be met" invited the reading that this change
proceeds past a failed evidence gate. It does not. The gate that applies is
faithful transcription, and withholding instead would not return to a neutral
state — it would keep shipping a *different population's weaker wording* as
universal guidance, which is itself an unsourced clinical claim and the one
causing harm.

## What CPIC says

CYP2C19 **Intermediate Metabolizer**:

| population | recommendation | classification |
| --- | --- | --- |
| CVI ACS PCI | Avoid standard dose (75 mg) clopidogrel if possible. Use prasugrel or ticagrelor at standard dose if no contraindication. | **Strong** |
| CVI non-ACS non-PCI | No recommendation | — |
| NVI | Consider an alternative P2Y12 inhibitor at standard dose if clinically indicated and no contraindication. | Moderate |

CYP2C19 **Poor Metabolizer** is "avoid clopidogrel if possible" in all three, Strong
for ACS/PCI and Moderate otherwise.

**The shipped text was not a paraphrase.** `"Consider alternative antiplatelet
therapy."` is the NVI/Moderate row's wording, adopted as the universal
recommendation — so the row stated a *different population's weaker* guidance for
every patient.

## The generalisation, stated plainly

CPIC's recommendations here are indication-conditional and the application cannot
key on indication, so one row must cover all three populations. This change
generalises to the **Strong ACS/PCI** recommendation and names the other two, rather
than silently adopting the weakest.

That choice is a judgement and is recorded as one. Its basis: ACS/PCI is
clopidogrel's dominant indication and the basis of its FDA boxed warning, and the
direction of harm from the alternative is **under-warning** a phenotype carried by
~25% of non-Finnish Europeans and >40% of East and South Asian populations. The row
also states that CPIC withholds for other cardiovascular indications, so the
generalisation does not imply Strong guidance everywhere.

## What this change does not do

`risk_allele`-equivalent keying, phenotype translation, diplotype calling and the
CPIC `classification` are all untouched — both rows were already `A`. Actionability
ordering is unchanged: `classify_actionability` returns `actionable` for the old and
new wording alike, verified before the edit.
