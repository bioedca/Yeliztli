"""Offline provenance guard for the GWAS seed fixture (#1948).

``tests/fixtures/seed_csvs/gwas_seed.csv`` is the repo's GWAS reference data and test
oracle — it seeds ``gwas_associations`` in ``tests/fixtures/mini_reference.db``. A row
whose ``study``/``pubmed_id`` name different papers makes an unverifiable association
look evidence-backed, which is exactly what #1948 found: 44 of 77 rows cited a paper
that is not the one the ``study`` label names, several of them wildly unrelated
(malaria resistance → a Salmonella antibody paper; alcohol dependence → carbon
nanotubes).

This guard checks each row's ``study`` label against the paper its ``pubmed_id``
actually identifies, using the **committed** snapshot at
``tests/fixtures/gwas_seed_pmid_snapshot.json`` (built by
``scripts/build_gwas_seed_pmid_snapshot.py``). It never touches PubMed at test time.

Coverage model — deliberately different from the panel citation guard: an entry whose
PMID is missing from that snapshot is *skipped*, which let it silently rot (#1983).
Here an uncovered PMID **fails**, so adding a citation without snapshotting it is loud.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "gwas_seed.csv"
_SNAPSHOT_PATH = _ROOT / "tests" / "fixtures" / "gwas_seed_pmid_snapshot.json"

# Rows whose fixture label is a customary CONSORTIUM byline: the cited PMID genuinely is
# the paper the label names, so a first-author surname comparison cannot match. Verified
# individually against PubMed — do not add to this list without doing the same.
_CONSORTIUM_LABELS: dict[tuple[str, str], str] = {
    # PMID 18650507 — Link E et al., "SLCO1B1 variants and statin-induced myopathy",
    # N Engl J Med 2008. Authored by the SEARCH Collaborative Group.
    ("rs4149056", "Simvastatin myopathy"): "18650507",
    # PMID 25056061 — "Biological insights from 108 schizophrenia-associated genetic
    # loci", Nature 2014. Byline is the Schizophrenia Working Group of the Psychiatric
    # Genomics Consortium; Ripke is the customary attribution.
    ("rs6311", "Schizophrenia"): "25056061",
}

# Rows #1948's audit could NOT repair: the intended association could not be pinned down
# from paper-level evidence, so their citations are still wrong. Withholding beats
# guessing (a fabricated "correction" looks authoritative), so they are quarantined here
# rather than patched. Tracked by the #1948 follow-up; REMOVE an entry as it is fixed —
# ``test_quarantined_rows_are_still_mismatched`` fails if one is repaired but left here.
_UNRESOLVED_PROVENANCE: frozenset[tuple[str, str]] = frozenset(
    {
        ("rs12248560", "Clopidogrel response"),
        ("rs12255372", "Type 2 diabetes"),
        ("rs13266634", "Type 2 diabetes"),
        ("rs1535", "Omega-6 fatty acids"),
        ("rs1799963", "Venous thromboembolism"),
        ("rs1799971", "Alcohol dependence"),
        ("rs1799971", "Opioid dose requirement"),
        ("rs1800460", "Thiopurine toxicity"),
        ("rs1800497", "Alcohol dependence"),
        ("rs1800497", "Reward sensitivity"),
        ("rs1801133", "Neural tube defects"),
        ("rs2228479", "Skin pigmentation"),
        ("rs25531", "Depression"),
        ("rs3135388", "Multiple sclerosis"),
        ("rs334", "Sickle cell disease"),
        ("rs4244285", "Clopidogrel response"),
        ("rs4570625", "Anxiety disorders"),
        ("rs4654748", "Vitamin B6 levels"),
        ("rs4680", "Cognitive performance"),
        ("rs4680", "Pain sensitivity"),
        ("rs6025", "Venous thromboembolism"),
        ("rs6265", "Depression"),
        ("rs6313", "Antipsychotic response"),
        ("rs63750066", "Breast cancer"),
        ("rs776746", "Tacrolimus dose"),
        ("rs80357906", "Breast cancer"),
        ("rs854560", "Organophosphate metabolism"),
    }
)


def _rows() -> list[dict[str, str]]:
    with open(_SEED, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _snapshot() -> dict[str, dict[str, str]]:
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))["pmids"]


def _parse_study(label: str) -> tuple[str, str]:
    """``"Zeggini et al. 2008"`` → ``("Zeggini", "2008")``."""
    surname = re.match(r"([A-Za-z\-']+)", label or "")
    year = re.search(r"(19|20)\d{2}", label or "")
    return (surname.group(1) if surname else ""), (year.group(0) if year else "")


def _label_matches(label: str, meta: dict[str, str]) -> bool:
    surname, year = _parse_study(label)
    if not surname or not year:
        return False
    return surname.lower() in meta["author"].lower() and year == meta["year"]


def _key(row: dict[str, str]) -> tuple[str, str]:
    return (row["rsid"], row["trait"])


def test_snapshot_is_well_formed() -> None:
    data = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    prov = data["_provenance"]
    assert prov["source"], "snapshot missing provenance.source"
    assert prov["accessed"], "snapshot missing provenance.accessed"
    assert not prov["unresolved_pmids"], (
        f"snapshot has unresolved PMIDs — they cite nothing real: {prov['unresolved_pmids']}"
    )
    for pmid, meta in data["pmids"].items():
        assert pmid.isdigit(), f"non-numeric snapshot key {pmid!r}"
        assert set(meta) >= {"author", "year", "title"}, f"{pmid}: missing fields"
        assert meta["title"].strip(), f"{pmid}: empty title"


def test_snapshot_covers_every_cited_pmid() -> None:
    """Every cited PMID must be snapshotted, so provenance coverage cannot rot silently.

    The panel citation guard *skips* un-snapshotted PMIDs and thereby stopped checking
    18% of its locked entries without anyone noticing (#1983). This one fails instead:
    if you add or change a ``pubmed_id``, regenerate the snapshot in the same change.
    """
    snapshot = _snapshot()
    missing = sorted({r["pubmed_id"] for r in _rows()} - set(snapshot), key=int)
    assert not missing, (
        f"gwas_seed.csv cites {len(missing)} PMID(s) absent from the snapshot: {missing}. "
        f"Run: PYTHONPATH=. python scripts/build_gwas_seed_pmid_snapshot.py --accessed <today>"
    )


def test_every_row_cites_a_pmid() -> None:
    blank = [_key(r) for r in _rows() if not r["pubmed_id"].strip()]
    assert not blank, f"rows with no pubmed_id: {blank}"


def test_study_label_identifies_the_cited_paper() -> None:
    """Each row's ``study`` must name the paper its ``pubmed_id`` resolves to.

    This is the regression half of #1948: it cannot re-detect the rows already known to
    be broken (they are quarantined), but it stops a NEW unrelated citation from landing.
    """
    snapshot = _snapshot()
    failures = []
    for row in _rows():
        key = _key(row)
        if key in _UNRESOLVED_PROVENANCE or key in _CONSORTIUM_LABELS:
            continue
        meta = snapshot[row["pubmed_id"]]
        if not _label_matches(row["study"], meta):
            failures.append(
                f"{key}: study={row['study']!r} pmid={row['pubmed_id']} "
                f"actually is {meta['author']} {meta['year']} — {meta['title'][:70]!r}"
            )
    assert not failures, "gwas_seed.csv rows cite a paper other than the one named:\n" + "\n".join(
        failures
    )


def test_quarantined_rows_are_still_mismatched() -> None:
    """The quarantine must shrink, never rot.

    If a quarantined row is repaired, this fails until its entry is removed — otherwise
    the list would silently keep excusing rows that are already correct, and the guard
    would stop protecting them.
    """
    snapshot = _snapshot()
    by_key = {_key(r): r for r in _rows()}
    now_fixed = []
    for key in sorted(_UNRESOLVED_PROVENANCE):
        row = by_key.get(key)
        if row is None:
            now_fixed.append(f"{key}: row no longer exists — drop it from the quarantine")
        elif _label_matches(row["study"], snapshot[row["pubmed_id"]]):
            now_fixed.append(f"{key}: now cites {row['study']!r} correctly — drop it")
    assert not now_fixed, "quarantined rows that no longer need quarantining:\n" + "\n".join(
        now_fixed
    )


def test_consortium_allowlist_pmids_still_match_their_rows() -> None:
    """An allowlisted row is exempt only for the exact PMID that was hand-verified."""
    by_key = {_key(r): r for r in _rows()}
    for key, pmid in _CONSORTIUM_LABELS.items():
        row = by_key.get(key)
        assert row is not None, f"{key}: consortium allowlist names a row that does not exist"
        assert row["pubmed_id"] == pmid, (
            f"{key}: allowlisted for PMID {pmid} but now cites {row['pubmed_id']} — "
            f"re-verify the citation by hand before updating the allowlist"
        )


@pytest.mark.parametrize(
    ("rsid", "trait", "pmid", "study"),
    [
        # Spot-locks for #1948's verified repairs: each was confirmed against the primary
        # paper, so a silent revert to the old fabricated citation must fail loudly.
        ("rs334", "Malaria resistance", "11965279", "Aidoo et al. 2002"),
        ("rs662", "Cardiovascular disease", "15001326", "Wheeler et al. 2004"),
        ("rs182549", "Lactase persistence", "15114531", "Bersaglieri et al. 2004"),
        ("rs6265", "Memory performance", "12553913", "Egan et al. 2003"),
        ("rs2476601", "Rheumatoid arthritis", "20453842", "Stahl et al. 2010"),
    ],
)
def test_repaired_rows_keep_their_verified_citation(
    rsid: str, trait: str, pmid: str, study: str
) -> None:
    row = next((r for r in _rows() if _key(r) == (rsid, trait)), None)
    assert row is not None, f"({rsid}, {trait}) missing from gwas_seed.csv"
    assert row["pubmed_id"] == pmid, f"({rsid}, {trait}) citation reverted"
    assert row["study"] == study


def test_pon1_rs662_risk_allele_is_plus_strand() -> None:
    """rs662 must carry the GRCh37 PLUS-strand risk allele, not the cDNA rendering.

    PON1 is transcribed from the minus strand, so the R192 (trait-raising) allele is
    ``C`` on the plus strand — dbSNP states the equivalence outright:
    ``NC_000007.13:g.94937446T>C`` == ``NM_000446.7:c.575A>G`` == ``p.Gln192Arg``.
    The fixture shipped ``G``, the coding-strand allele, which inverts the call for any
    consumer reading plus-strand genotypes (#1948).
    """
    row = next(r for r in _rows() if _key(r) == ("rs662", "Cardiovascular disease"))
    assert row["risk_allele"] == "C", (
        "rs662 risk_allele must be the plus-strand R192 allele C "
        "(G is the NM_000446.7 cDNA allele — a strand trap)"
    )
