"""Documentation guards for APOB's direction-specific cardiovascular scope."""

from __future__ import annotations

import re
from pathlib import Path

from backend.analysis.cardiovascular import (
    CATEGORY_FH,
    CATEGORY_LIPID,
    _variant_condition_scope,
    load_cardiovascular_panel,
)

DOCS_ROOT = Path(__file__).resolve().parent.parent.parent / "docs"
CARDIOVASCULAR_DOC = DOCS_ROOT / "modules" / "health-risk" / "cardiovascular.md"
FH_DOC = DOCS_ROOT / "modules" / "health-risk" / "familial-hypercholesterolemia.md"

LOW_LDL_SCOPE = (
    "Protein-truncating APOB variants linked to familial hypobetalipoproteinemia "
    "are reported under Other lipid metabolism and excluded from FH status."
)
FH_SCOPE = "including its ClinVar condition-label and lower-penetrance/risk-allele filters."
SOURCE_URLS = {
    "https://doi.org/10.1161/CIRCGEN.118.002376",
    "https://doi.org/10.1001/jamacardio.2022.5271",
}


def _plain_markdown(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", text))


def test_apob_docs_match_direction_specific_production_scope() -> None:
    panel = load_cardiovascular_panel()
    apob = panel.get_gene("APOB")

    assert apob is not None
    assert apob.conditions

    low_ldl_conditions, low_ldl_category = _variant_condition_scope(
        apob,
        "Familial hypobetalipoproteinemia 1",
        "stop_gained",
    )
    fh_conditions, fh_category = _variant_condition_scope(
        apob,
        "Familial hypercholesterolemia",
        "missense_variant",
    )
    low_ldl_missense_conditions, low_ldl_missense_category = _variant_condition_scope(
        apob,
        "Familial hypobetalipoproteinemia 1",
        "missense_variant",
    )
    unlabeled_conditions, unlabeled_category = _variant_condition_scope(
        apob,
        None,
        "missense_variant",
    )

    assert low_ldl_conditions == ["Familial hypobetalipoproteinemia 1"]
    assert low_ldl_category == CATEGORY_LIPID
    assert fh_conditions
    assert fh_category == CATEGORY_FH
    assert low_ldl_missense_conditions == ["Familial hypobetalipoproteinemia 1"]
    assert low_ldl_missense_category == CATEGORY_LIPID
    assert unlabeled_conditions == apob.conditions
    assert unlabeled_category == CATEGORY_FH

    for path in (CARDIOVASCULAR_DOC, FH_DOC):
        documentation = _plain_markdown(path)
        assert LOW_LDL_SCOPE in documentation
        assert FH_SCOPE in documentation
        assert "(accessed 2026-07-31)" in documentation
        for source_url in SOURCE_URLS:
            assert source_url in documentation
