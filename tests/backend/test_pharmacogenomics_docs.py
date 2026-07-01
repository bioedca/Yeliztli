"""Documentation guards for pharmacogenomics safety caveats."""

from pathlib import Path

_DOC = (
    Path(__file__).resolve().parent.parent.parent
    / "docs"
    / "modules"
    / "pharma"
    / "pharmacogenomics.md"
)


def test_pharmacogenomics_docs_warn_about_ugt1a1_star28_array_gap() -> None:
    text = _DOC.read_text(encoding="utf-8")
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
