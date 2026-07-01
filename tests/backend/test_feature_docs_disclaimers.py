"""Documentation guards for clinical variant feature disclaimers."""

from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parent.parent.parent / "docs"
FEATURES_ROOT = DOCS_ROOT / "features"

CLINICAL_VARIANT_FEATURE_PAGES = {
    "export.md",
    "genome-browser.md",
    "variant-detail.md",
    "variant-explorer.md",
}

CLINICAL_VARIANT_TERMS = (
    "clinvar",
    "pathogenic",
    "pathogenicity",
    "clinical significance",
    "cadd",
    "sift",
    "polyphen",
    "revel",
)

SAFEGUARD_MARKERS = (
    '--8<-- "health-disclaimer.md"',
    "../intended-use.md",
)


def _feature_pages_that_describe_clinical_variant_data() -> set[str]:
    pages = set(CLINICAL_VARIANT_FEATURE_PAGES)

    for path in FEATURES_ROOT.glob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        if any(term in text for term in CLINICAL_VARIANT_TERMS):
            pages.add(path.name)

    return pages


def test_clinical_variant_feature_docs_include_health_safeguard() -> None:
    missing = []

    for page_name in sorted(_feature_pages_that_describe_clinical_variant_data()):
        text = (FEATURES_ROOT / page_name).read_text(encoding="utf-8")
        if not any(marker in text for marker in SAFEGUARD_MARKERS):
            missing.append(page_name)

    assert missing == []
