"""Docs↔UI guard: coverage-driven pathway-level badges stay documented (#1582).

``pathwayLevelDisplayLabel`` (``frontend/src/lib/pathwayCoverage.ts``) renders two
coverage-qualified level badges on wellness/gene-health pathway cards —
**"Tested Standard"** and **"Not Assessed"** — for a Standard pathway whose array
coverage is incomplete. They appeared nowhere in the docs, so a user seeing them
had no way to learn what they mean or how they differ from a plain Standard /
Indeterminate level. This locks both badge words into the interpretation
reference so a coverage-qualified badge can't ship undocumented again.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.annotation.dbnsfp import ENSEMBLE_MIN_AXES, is_ensemble_pathogenic_from_counts
from backend.annotation.insilico_axes import (
    CADD_PHRED_THRESHOLD,
    METALR_THRESHOLD,
    POLYPHEN_PROBABLY_DAMAGING_THRESHOLD,
    REVEL_THRESHOLD,
    SIFT_THRESHOLD,
    assess_insilico_axes,
)

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "docs" / "modules" / "interpretation-reference.md"
_RARE_VARIANTS_DOC = _REPO / "docs" / "modules" / "rare-variants.md"
_VARIANT_DETAIL_DOC = _REPO / "docs" / "features" / "variant-detail.md"
_VARIANT_EXPLORER_DOC = _REPO / "docs" / "features" / "variant-explorer.md"
_RISK_ALLELE_EVIDENCE_DIR = (
    _REPO / "data" / "science-evidence" / "2026-07-31-clingen-risk-allele-2052"
)
_RISK_ALLELE_EVIDENCE_XML = _RISK_ALLELE_EVIDENCE_DIR / "raw" / "pubmed-efetch-38054408.xml"
_RISK_ALLELE_NLM_TERMS = (
    _RISK_ALLELE_EVIDENCE_DIR / "raw" / "nlm-copyright-download-terms-2026-07-31.html"
)
_SRC = _REPO / "frontend" / "src" / "lib" / "pathwayCoverage.ts"
_RARE_VARIANT_PANEL = (
    _REPO / "frontend" / "src" / "components" / "rare-variants" / "VariantDetailPanel.tsx"
)
# Each entry has a synthetic production-path storage assertion in
# test_cancer_analysis.py, test_cardiovascular.py, test_carrier_analysis.py, or
# test_rare_variant_finder.py; the first three also assert the route returns it.
_LOWER_PENETRANCE_MODULES = {
    "cancer.py": (
        "Cancer",
        _REPO / "docs" / "modules" / "health-risk" / "cancer.md",
    ),
    "cardiovascular.py": (
        "Cardiovascular",
        _REPO / "docs" / "modules" / "health-risk" / "cardiovascular.md",
    ),
    "carrier_status.py": (
        "Carrier status",
        _REPO / "docs" / "modules" / "health-risk" / "carrier-status.md",
    ),
    "rare_variant_finder.py": ("Rare variants", _RARE_VARIANTS_DOC),
}

# The coverage-qualified level-badge strings pathwayLevelDisplayLabel can emit.
_COVERAGE_BADGES = ("Tested Standard", "Not Assessed")

# Direction/scale tokens the in-silico-score note must carry so CADD/REVEL aren't
# shown as uninterpretable bare numbers (#1589).
_IN_SILICO_TOKENS = ("cadd", "revel", "phred", "deleterious", "pathogenic")

# The Rare Variant panel's CADD/REVEL red-highlight thresholds, mirrored in the doc.
_UI_SCORE_THRESHOLDS = ("cadd_phred >= 20", "revel >= 0.5")

_ENSEMBLE_THRESHOLD_TOKENS = (
    f"SIFT < {SIFT_THRESHOLD:g}",
    f"PolyPhen-2 HVAR > {POLYPHEN_PROBABLY_DAMAGING_THRESHOLD:g}",
    f"CADD PHRED ≥ {CADD_PHRED_THRESHOLD:g}",
    f"REVEL ≥ {REVEL_THRESHOLD:g}",
    "MetaSVM > 0",
    f"MetaLR > {METALR_THRESHOLD:g}",
)

_ENSEMBLE_EXAMPLES = (
    (2, 2, True),
    (2, 3, True),
    (2, 4, False),
    (3, 4, True),
)


def _documents_distinct_lower_penetrance_category(text: str) -> bool:
    normalized = (
        " ".join(text.lower().split())
        .replace("**", "")
        .replace("lower-penetrance", "lower penetrance")
        .replace("risk-allele", "risk allele")
        .replace("high-penetrance", "high penetrance")
    )
    # One canonical sentence, required verbatim on every page. The earlier
    # Rare-variants-specific alternative ("ClinVar risk assertions are stored
    # under...") is deliberately gone: it named only the risk-allele half of a
    # category that also holds low-penetrance assertions, so accepting it would
    # let that imprecision return.
    return (
        "lower penetrance/risk allele findings are stored under a distinct findings category "
        "from high penetrance p/lp" in normalized
    )


def test_lower_penetrance_output_table_matches_production_contract() -> None:
    """The table matches modules covered by existing store-and-route tests (#2052)."""
    doc = _DOC.read_text(encoding="utf-8")
    row = next(
        line
        for line in doc.splitlines()
        if line.startswith("| ClinVar lower-penetrance/risk-allele variants |")
    )
    documented_modules = {module.strip() for module in row.split("|")[2].split(",")}
    expected_modules = {display_name for display_name, _ in _LOWER_PENETRANCE_MODULES.values()}
    assert documented_modules == expected_modules, (
        "The ClinVar lower-penetrance/risk-allele output row must list every analysis "
        f"module in the production contract; expected {sorted(expected_modules)}, found "
        f"{sorted(documented_modules)} (#2052)."
    )


def test_lower_penetrance_category_is_documented_on_each_module_page() -> None:
    """Each module names the distinct stored category in 'What you'll see' (#2052)."""
    assert _LOWER_PENETRANCE_MODULES, (
        "no lower-penetrance modules configured; an emptied mapping would make this "
        "guard pass vacuously"
    )
    missing: list[str] = []
    for display_name, path in _LOWER_PENETRANCE_MODULES.values():
        doc = path.read_text(encoding="utf-8")
        what_youll_see = doc.split("## What you'll see", 1)[1].split("\n## ", 1)[0]
        if not _documents_distinct_lower_penetrance_category(what_youll_see):
            missing.append(display_name)

    assert not missing, (
        "Module pages returning the distinct ClinVar lower-penetrance/risk-allele category "
        "must describe it under 'What you'll see' as a distinct stored category from "
        f"high-penetrance P/LP findings; missing {missing} (#2052)."
    )


def test_shared_module_views_disclose_that_categories_render_together() -> None:
    """Storage-category wording must not imply a user-visible tier in shared views (#2052)."""
    shared_views = [
        (display_name, path)
        for display_name, path in _LOWER_PENETRANCE_MODULES.values()
        if display_name != "Rare variants"
    ]
    assert shared_views, (
        "no shared-view modules configured; an emptied mapping would make this loop "
        "body never run and the guard pass vacuously"
    )
    for display_name, path in shared_views:
        doc = path.read_text(encoding="utf-8")
        what_youll_see = doc.split("## What you'll see", 1)[1].split("\n## ", 1)[0]
        normalized = " ".join(what_youll_see.lower().split())
        assert "the current page displays both categories together" in normalized, (
            f"{display_name} must distinguish its stored categories "
            "from its shared UI view (#2052)."
        )


def test_pubmed_evidence_payload_excludes_publisher_owned_abstract() -> None:
    """The retained PubMed snapshot is redistributable citation metadata only (#2052)."""
    payload = _RISK_ALLELE_EVIDENCE_XML.read_text(encoding="utf-8")
    assert "<Abstract>" not in payload
    assert "CopyrightInformation" not in payload
    assert "All rights reserved" not in payload
    assert '<PMID Version="1">38054408</PMID>' in payload
    assert '<PublicationType UI="D016428">Journal Article</PublicationType>' in payload
    assert "<Citation>ssing heritability of complex diseases." in payload

    readme = (_RISK_ALLELE_EVIDENCE_DIR / "README.md").read_text(encoding="utf-8")
    assert "source-native EFetch response" in readme
    assert "not used to support this packet's claim" in " ".join(readme.split())

    queries = json.loads((_RISK_ALLELE_EVIDENCE_DIR / "queries.json").read_text(encoding="utf-8"))
    source = next(
        query
        for query in queries["queries"]
        if query["service"] == "NCBI Entrez" and query["endpoint"] == "efetch"
    )
    assert "source-native response starts its first ReferenceList/Citation" in source["result"]
    assert "retained verbatim and is not used for the claim" in source["result"]


def test_risk_allele_evidence_records_narrow_life_science_route() -> None:
    """The packet must record the required source-specific research fallback (#2052)."""
    queries = json.loads((_RISK_ALLELE_EVIDENCE_DIR / "queries.json").read_text(encoding="utf-8"))
    source = next(
        query
        for query in queries["queries"]
        if query.get("route") == "life-science-research:ncbi-entrez-skill"
    )

    assert source["service"] == "NCBI Entrez"
    assert source["runner"] == "scripts/ncbi_entrez.py"
    assert source["endpoint"] == "esummary"
    assert source["params"] == {
        "db": "pubmed",
        "id": "38054408",
        "retmode": "json",
    }
    assert source["status"] == "success"
    assert "ok=true, source=ncbi-entrez, no warnings" in source["result"]

    readme = (_RISK_ALLELE_EVIDENCE_DIR / "README.md").read_text(encoding="utf-8")
    assert "Narrow Life Science Research fallback" in readme
    assert "`life-science-research:ncbi-entrez-skill`" in readme


def test_nlm_license_source_is_preserved_with_the_evidence_packet() -> None:
    """The packet must retain the authoritative terms supporting redistribution (#2052)."""
    relative_path = (
        "data/science-evidence/2026-07-31-clingen-risk-allele-2052/raw/"
        "nlm-copyright-download-terms-2026-07-31.html"
    )
    payload = _RISK_ALLELE_NLM_TERMS.read_text(encoding="utf-8")
    assert 'content="https://www.nlm.nih.gov/databases/download.html"' in payload
    assert 'content="2026-07-31"' in payload
    assert "Although a signed license agreement is not needed" in payload
    assert "acknowledge NLM as the source of the data" in payload
    assert "not indicate or imply that NLM has endorsed" in payload
    assert "do not reflect the most current/accurate data available from NLM" in payload
    assert "<script" not in payload

    readme = (_RISK_ALLELE_EVIDENCE_DIR / "README.md").read_text(encoding="utf-8")
    assert relative_path in readme

    queries = json.loads((_RISK_ALLELE_EVIDENCE_DIR / "queries.json").read_text(encoding="utf-8"))
    source = next(
        query for query in queries["queries"] if query["service"] == "National Library of Medicine"
    )
    assert source["endpoint"] == "GET https://www.nlm.nih.gov/databases/download.html"
    assert source["status"] == "success"
    assert source["raw_payload"] == relative_path


def test_lower_penetrance_category_guard_rejects_high_penetrance_equivalence() -> None:
    """Keyword-only text cannot satisfy the stored-category documentation guard (#2052)."""
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings use the same tier as high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are not a separate tier from "
        "high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are not reported separately from "
        "high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are never reported in a separate tier from "
        "high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are no longer reported in a separate tier "
        "from high-penetrance P/LP findings."
    )


def test_coverage_badges_are_documented() -> None:
    doc = _DOC.read_text(encoding="utf-8")
    missing = [b for b in _COVERAGE_BADGES if b not in doc]
    assert not missing, (
        "docs/modules/interpretation-reference.md does not document pathway-level "
        f"badge(s): {missing}. pathwayLevelDisplayLabel renders these — document them "
        "in the 'Categorical pathway levels' section (#1582)."
    )


def test_badges_are_still_emitted_by_the_code() -> None:
    """Premise guard: the two labels are still the literals pathwayCoverage.ts
    emits, so a rename trips this (revisit the doc + the list above) rather than
    leaving the doc pinning a badge word the code no longer shows (#1582)."""
    src = _SRC.read_text(encoding="utf-8")
    missing = [b for b in _COVERAGE_BADGES if f'"{b}"' not in src]
    assert not missing, (
        f"frontend/src/lib/pathwayCoverage.ts no longer emits: {missing}. "
        "Update _COVERAGE_BADGES and docs/modules/interpretation-reference.md."
    )


def test_in_silico_scores_are_documented() -> None:
    """CADD and REVEL are shown as bare numbers on the variant surfaces; the
    interpretation reference must state their direction and scale so a user can
    read them (they were undocumented while SIFT/PolyPhen got labels) (#1589)."""
    doc = _DOC.read_text(encoding="utf-8").lower()
    missing = [t for t in _IN_SILICO_TOKENS if t not in doc]
    assert not missing, (
        "docs/modules/interpretation-reference.md no longer documents the in-silico "
        f"pathogenicity scores' direction/scale (missing {missing}) — keep the CADD/REVEL "
        "note (#1589)."
    )


def test_documented_score_thresholds_match_the_ui() -> None:
    """Premise guard: the CADD ≥ 20 / REVEL ≥ 0.5 cut-offs the doc states are the
    ones the Rare Variant panel actually red-highlights, so a threshold change in
    the UI forces the doc to be revisited rather than silently drifting (#1589)."""
    doc = _DOC.read_text(encoding="utf-8")
    assert "CADD ≥ 20" in doc and "REVEL ≥ 0.5" in doc, (
        "interpretation-reference.md must state the UI's CADD ≥ 20 / REVEL ≥ 0.5 "
        "display thresholds (#1589)."
    )
    src = _RARE_VARIANT_PANEL.read_text(encoding="utf-8")
    missing = [t for t in _UI_SCORE_THRESHOLDS if t not in src]
    assert not missing, (
        f"{_RARE_VARIANT_PANEL.name} no longer applies the documented thresholds "
        f"{missing}. Update the UI copy and the CADD/REVEL note in "
        "docs/modules/interpretation-reference.md (#1589)."
    )


def test_ensemble_pathogenic_rule_is_documented() -> None:
    """The badge's project-specific axes and fraction must be readable outside code (#1971)."""
    doc = _DOC.read_text(encoding="utf-8")
    missing = [token for token in _ENSEMBLE_THRESHOLD_TOKENS if token not in doc]
    assert not missing, (
        "interpretation-reference.md no longer documents the ensemble-pathogenic "
        f"axis threshold(s): {missing} (#1971)."
    )

    plain_doc = " ".join(doc.replace("**", "").split())
    rule_tokens = (
        f"at least {ENSEMBLE_MIN_AXES} axes have data and a strict majority of those "
        "assessed axes vote deleterious",
        "deleterious axes / axes with data",
        "not a percentage, probability, confidence score",
        "not a claim that the underlying scores are statistically independent",
        "other present categorical results vote non-deleterious",
        "that axis is absent",
        "their strict majority becomes one axis",
        "If none has data, META is absent",
    )
    missing = [token for token in rule_tokens if token not in plain_doc]
    assert not missing, (
        "interpretation-reference.md no longer explains the ensemble-pathogenic "
        f"denominator or limitations: {missing} (#1971)."
    )


def test_documented_ensemble_operators_match_backend_boundaries() -> None:
    """Strict/inclusive operators in the threshold table match executable boundaries (#1971)."""
    epsilon = 0.001
    cases = (
        ("SIFT at boundary", {"sift_score": SIFT_THRESHOLD}, False),
        ("SIFT below boundary", {"sift_score": SIFT_THRESHOLD - epsilon}, True),
        (
            "PolyPhen-2 at boundary",
            {"polyphen2_hsvar_score": POLYPHEN_PROBABLY_DAMAGING_THRESHOLD},
            False,
        ),
        (
            "PolyPhen-2 above boundary",
            {"polyphen2_hsvar_score": POLYPHEN_PROBABLY_DAMAGING_THRESHOLD + epsilon},
            True,
        ),
        ("CADD at boundary", {"cadd_phred": CADD_PHRED_THRESHOLD}, True),
        ("CADD below boundary", {"cadd_phred": CADD_PHRED_THRESHOLD - epsilon}, False),
        ("REVEL at boundary", {"revel": REVEL_THRESHOLD}, True),
        ("REVEL below boundary", {"revel": REVEL_THRESHOLD - epsilon}, False),
        ("MetaSVM at boundary", {"metasvm": 0.0}, False),
        ("MetaSVM above boundary", {"metasvm": epsilon}, True),
        ("MetaLR at boundary", {"metalr": METALR_THRESHOLD}, False),
        ("MetaLR above boundary", {"metalr": METALR_THRESHOLD + epsilon}, True),
    )
    for label, variant, expected_deleterious in cases:
        deleterious, assessed = assess_insilico_axes(variant)
        assert assessed == 1, label
        assert bool(deleterious) is expected_deleterious, label


def test_documented_categorical_fallbacks_match_backend_denominator() -> None:
    """Present categorical results assess an axis even when they vote non-deleterious (#1971)."""
    cases = (
        ("SIFT deleterious", {"sift_pred": "D"}, True),
        ("SIFT tolerated", {"sift_pred": "T"}, False),
        ("PolyPhen-2 probably damaging", {"polyphen2_hsvar_pred": "D"}, True),
        ("PolyPhen-2 benign", {"polyphen2_hsvar_pred": "B"}, False),
    )
    for label, variant, expected_deleterious in cases:
        deleterious, assessed = assess_insilico_axes(variant)
        assert assessed == 1, label
        assert bool(deleterious) is expected_deleterious, label

    assert assess_insilico_axes({}) == (0, 0)


def test_documented_ensemble_examples_match_backend_rule() -> None:
    """The inverse-looking 2/2 versus 3/4 examples stay tied to the shipped rule (#1971)."""
    doc = _DOC.read_text(encoding="utf-8")
    for deleterious, assessed, expected in _ENSEMBLE_EXAMPLES:
        row_prefix = f"| `{deleterious}/{assessed}` | {'Yes' if expected else 'No'} |"
        assert row_prefix in doc
        assert is_ensemble_pathogenic_from_counts(deleterious, assessed) is expected

    assert "`2/2` is the minimum firing state" in doc
    assert "not stronger than `3/4`" in doc


def test_ensemble_definition_is_linked_from_variant_docs() -> None:
    """Every user-facing docs mention points to the canonical definition (#1971)."""
    anchor = "interpretation-reference.md#ensemble-pathogenic"
    docs = (_RARE_VARIANTS_DOC, _VARIANT_DETAIL_DOC, _VARIANT_EXPLORER_DOC)
    missing = [
        path.relative_to(_REPO).as_posix()
        for path in docs
        if anchor not in path.read_text(encoding="utf-8")
    ]
    assert not missing, f"Ensemble-pathogenic definition is not linked from: {missing} (#1971)."
