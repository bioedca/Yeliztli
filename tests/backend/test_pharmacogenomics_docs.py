"""Documentation guards for pharmacogenomics safety caveats."""

import csv
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PHARMA_DOC = _REPO_ROOT / "docs" / "modules" / "pharma" / "pharmacogenomics.md"
_INTERPRETATION_DOC = _REPO_ROOT / "docs" / "modules" / "interpretation-reference.md"
_CPIC_DIPLOTYPES = _REPO_ROOT / "backend" / "data" / "cpic" / "cpic_diplotypes.csv"


def test_pharmacogenomics_docs_warn_about_ugt1a1_star28_array_gap() -> None:
    text = _PHARMA_DOC.read_text(encoding="utf-8")
    good_to_know = text.split("## Good to know", 1)[1].split("!!! danger", 1)[0]

    for required in (
        "*UGT1A1*",
        "`*28`",
        "promoter TA-repeat",
        "indeterminate",
        "normal `*1/*1` call does **not** rule out reduced *UGT1A1* activity",
        "irinotecan",
        "atazanavir",
    ):
        assert required in good_to_know


def test_pharmacogenomics_docs_cover_all_cpic_metabolizer_phenotypes() -> None:
    expected_terms = _cpic_metabolizer_terms()
    assert expected_terms, "cpic_diplotypes.csv should define metabolizer phenotype terms"

    sections = {
        "pharmacogenomics page": _between(
            _PHARMA_DOC.read_text(encoding="utf-8"),
            "## What you'll see",
            "## Good to know",
        ),
        "interpretation reference": _between(
            _INTERPRETATION_DOC.read_text(encoding="utf-8"),
            "### Star-allele diplotypes & metabolizer status",
            "### Polygenic scores",
        ),
    }

    for name, section in sections.items():
        missing = [
            term for term in expected_terms if not re.search(rf"\b{re.escape(term)}\b", section)
        ]
        assert not missing, (
            f"{name} omits CPIC metabolizer phenotype term(s) present in "
            f"cpic_diplotypes.csv: {missing}"
        )


def _cpic_metabolizer_terms() -> list[str]:
    with _CPIC_DIPLOTYPES.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        terms = {
            phenotype.removesuffix(" Metabolizer").lower()
            for row in reader
            if (phenotype := row["phenotype"]).endswith(" Metabolizer")
        }

    return sorted(terms)


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0].lower()
