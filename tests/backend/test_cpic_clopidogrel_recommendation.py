"""The shipped CYP2C19/clopidogrel rows must carry CPIC's actual recommendation.

#2026: the Intermediate Metabolizer row read *"Consider alternative antiplatelet
therapy."* — which is not a paraphrase of CPIC's recommendation but **verbatim the
weaker wording CPIC gives a different population**. Checked against the CPIC API
(``RxNorm:32968``, accessed 2026-08-11):

===================  =====================  ==============
population           CYP2C19 IM             classification
===================  =====================  ==============
CVI ACS PCI          Avoid standard dose…   Strong
CVI non-ACS non-PCI  No recommendation      —
NVI                  Consider an alt…       Moderate
===================  =====================  ==============

The app cannot key on indication, so one row must generalize. Adopting the
*weakest* one under-warned the flagship ACS/PCI case — clopidogrel's dominant
real-world use and the basis of its FDA boxed warning — for a phenotype carried
by roughly a quarter of non-Finnish Europeans and over 40% of East and South
Asians. Direction of harm was under-warning.

**Why these tests read the shipped CSV.** The existing clopidogrel tests in
``test_pharma_api.py``, ``test_pharmacogenomics.py`` and
``test_medication_safety_report.py`` construct their own recommendation strings as
fixtures, so all 781 of them passed against both the wrong text and the right one.
Nothing asserted what actually ships. That is the gap this module closes.

**Why the IM-vs-PM check is not generalised.** The tell in #2026 was that the
repo's PM row was directive while its IM row was optional. Turning that into a
cross-drug invariant was surveyed and rejected: it fires on CYP2D6/tamoxifen,
where CPIC *genuinely* rates IM softer ("Consider…") than PM ("Recommend
alternative…"). A guard that flags correct data gets deleted rather than obeyed,
so this stays scoped to the drug the evidence covers.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

CPIC_CSV = (
    Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic" / "cpic_guidelines.csv"
)


def _clopidogrel_rows() -> dict[str, dict[str, str]]:
    rows = {
        r["phenotype"]: r
        for r in csv.DictReader(CPIC_CSV.open(encoding="utf-8"))
        if r["gene"] == "CYP2C19" and r["drug"] == "clopidogrel"
    }
    assert rows, "no CYP2C19/clopidogrel rows parsed — the guard would pass vacuously"
    return rows


@pytest.mark.parametrize("phenotype", ["Intermediate Metabolizer", "Poor Metabolizer"])
def test_reduced_function_rows_are_directive_and_name_cpic_alternatives(phenotype: str) -> None:
    """Both reduced-function phenotypes must say *avoid*, and name the agents.

    CPIC names prasugrel and ticagrelor in every actionable indication for both
    phenotypes. Dropping them leaves "alternative antiplatelet therapy" open to
    substitutes CPIC does not endorse, which is a second way to under-warn.
    """
    recommendation = _clopidogrel_rows()[phenotype]["recommendation"].lower()

    assert "avoid" in recommendation, recommendation
    assert "prasugrel" in recommendation, recommendation
    assert "ticagrelor" in recommendation, recommendation
    # The exact softened string this issue removed must not return.
    assert recommendation.strip() != "consider alternative antiplatelet therapy."


def test_intermediate_leads_with_the_strong_acs_pci_recommendation() -> None:
    """The Strong case must lead, not the Moderate one.

    CPIC's IM guidance is indication-conditional and the app cannot key on
    indication, so the single row has to generalise. It must generalise to the
    Strong ACS/PCI recommendation — the indication that dominates real-world
    clopidogrel use — rather than the Moderate neurovascular one, which is the
    substitution #2026 reported.
    """
    recommendation = _clopidogrel_rows()["Intermediate Metabolizer"]["recommendation"]
    lowered = recommendation.lower()

    acs = lowered.index("acute coronary syndrome")
    neurovascular = lowered.index("neurovascular")
    assert acs < neurovascular, (
        "the Moderate neurovascular guidance precedes the Strong ACS/PCI guidance; "
        f"the strongest applicable recommendation must lead: {recommendation}"
    )
    assert "standard-dose" in lowered or "standard dose" in lowered, recommendation
    # CPIC withholds entirely for other cardiovascular indications; saying so keeps
    # the generalisation honest rather than implying Strong guidance everywhere.
    assert "no recommendation" in lowered, recommendation


def test_both_rows_keep_their_cpic_evidence_classification() -> None:
    """This change is text-only: the CPIC classification must not move.

    #2026 is not a keying or evidence-level defect — both rows were already
    classification ``A`` while disagreeing in force, which is what made the
    softened IM row a wording problem rather than a data-model one.
    """
    rows = _clopidogrel_rows()
    assert rows["Intermediate Metabolizer"]["classification"] == "A"
    assert rows["Poor Metabolizer"]["classification"] == "A"
    assert rows["Normal Metabolizer"]["recommendation"] == "Use label-recommended dosing."
