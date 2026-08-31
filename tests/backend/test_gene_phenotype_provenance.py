"""Offline provenance guard for the gene-phenotype seed fixture (#1959).

``tests/fixtures/seed_csvs/gene_phenotype_seed.csv`` is the repo's gene→disease
reference data and test oracle — it seeds the ``gene_phenotype`` table of the
checked-in ``mini_reference.db``, which ``_lookup_gene_phenotype``
(``backend/annotation/engine.py``) reads and the frontend renders as gene-disease
"associations". #1959 found 40 of 59 rows carried a ``disease_id`` for an *unrelated*
disease — a 16-ID block (``MONDO:0015612``–``0015627``) was a fabricated sequential
counter (Tay-Sachs → Mounier-Kuhn, PON1 → cavitary myiasis), and 10 rows pointed at
``obsolete`` MONDO terms whose clean-looking *names* slipped past the F21 label filter.

This guard checks each row's ``disease_id`` against the **committed** OLS4 snapshot at
``tests/fixtures/mondo_label_snapshot.json`` (built by
``scripts/build_mondo_label_snapshot.py``): the id must resolve, be non-obsolete, and
its MONDO label must be topically consistent with the row's ``disease_name``. It never
touches OLS4 at test time.

Coverage model — deliberately loud, unlike the panel citation guard that silently
*skips* un-snapshotted ids and thereby rotted (#1983): an id absent from the snapshot
**fails** here, so adding a row without snapshotting its id cannot pass unnoticed.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "gene_phenotype_seed.csv"
_SNAPSHOT_PATH = _ROOT / "tests" / "fixtures" / "mondo_label_snapshot.json"

# Longest run of consecutive MONDO ids permitted across distinct genes. The #1959
# fabricated block was 16 consecutive ids for 16 unrelated genes; real ontology lookups
# do not land on runs like that. Two adjacent ids can legitimately co-occur (sibling
# subtypes), so the guard only trips on a longer contiguous run.
_MAX_CONSECUTIVE_RUN = 3

# Stop-words stripped before topic comparison — generic disease scaffolding that would
# spuriously "match" any two labels.
_STOPWORDS = frozenset(
    {
        "disease",
        "disorder",
        "syndrome",
        "deficiency",
        "type",
        "susceptibility",
        "to",
        "of",
        "the",
        "and",
        "due",
        "familial",
        "hereditary",
        "variation",
        "alteration",
        "response",
        "1",
        "2",
        "3",
        "i",
        "ii",
    }
)

# Rows whose ``disease_name`` uses a lay/clinical term that shares no token with the
# canonical MONDO label, hand-verified as the same disease. Each entry pins the exact
# (disease_id, disease_name) pair so a later edit of either side re-triggers review.
# Keep this SMALL — it is an escape hatch, not a place to launder weak matches.
_TOPIC_ALLOWLIST: dict[str, tuple[str, str]] = {
    # MTHFR: homocysteinemia == homocystinuria; MTHFR == methylene tetrahydrofolate reductase.
    "MONDO:0009353": (
        "Homocysteinemia due to MTHFR deficiency",
        "homocystinuria due to methylene tetrahydrofolate reductase deficiency",
    ),
    # SLC6A4 (5-HTTLPR): depression susceptibility == depressive disorder (base disease).
    "MONDO:0002050": ("Depression susceptibility", "depressive disorder"),
    # ADRA2A: "ADHD" is the acronym of attention deficit-hyperactivity disorder.
    "MONDO:0007743": ("ADHD susceptibility", "attention deficit-hyperactivity disorder"),
    # MCM6 lactase non-persistence == adult-type lactose intolerance.
    "MONDO:0006065": ("Lactase persistence/non-persistence", "lactose intolerance adult type"),
    # CETP hyperalphalipoproteinemia == cholesterol-ester transfer protein deficiency (HALP1).
    "MONDO:0007744": (
        "Hyperalphalipoproteinemia",
        "cholesterol-ester transfer protein deficiency",
    ),
    # HNF1B MODY5 == renal cysts and diabetes syndrome (RCAD); "MODY type 5" is a synonym.
    "MONDO:0007669": ("MODY type 5", "renal cysts and diabetes syndrome"),
    # MTRR cblE: homocysteinemia phenotype; MONDO labels it by the cobalamin defect.
    "MONDO:0009354": (
        "Homocysteinemia due to MTRR deficiency",
        "methylcobalamin deficiency type cblE",
    ),
}


def _rows() -> list[dict[str, str]]:
    with open(_SEED, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _snapshot() -> dict[str, dict[str, object]]:
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))["labels"]


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOPWORDS}


def test_snapshot_is_well_formed() -> None:
    data = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    prov = data["_provenance"]
    assert prov["source"] and prov["accessed"], "snapshot missing provenance"
    assert data["labels"], "snapshot contains no labels (would pass every check vacuously)"
    for mid, meta in data["labels"].items():
        assert re.fullmatch(r"MONDO:\d+", mid), f"malformed snapshot key {mid!r}"
        assert meta["label"], f"{mid}: empty label"


def test_snapshot_covers_every_cited_id() -> None:
    """Every cited disease_id must be snapshotted — no silent coverage rot (#1983)."""
    missing = sorted({r["disease_id"] for r in _rows()} - set(_snapshot()))
    assert not missing, (
        f"gene_phenotype_seed.csv cites {len(missing)} MONDO id(s) absent from the snapshot: "
        f"{missing}. Run: python scripts/build_mondo_label_snapshot.py --accessed <today>"
    )


def test_no_disease_id_is_obsolete() -> None:
    """No row may cite an ``obsolete`` MONDO term.

    This is the F21 hygiene contract the fixture is supposed to *exercise*. #1959's rows
    paired a clean name (e.g. "Warfarin resistance/sensitivity") with an obsolete id, so
    ``_is_obsolete_disease`` (which filters on the *name*) never fired.
    """
    snap = _snapshot()
    obsolete = [
        (r["gene_symbol"], r["disease_id"], snap[r["disease_id"]]["label"])
        for r in _rows()
        if snap[r["disease_id"]]["obsolete"]
    ]
    assert not obsolete, f"rows cite obsolete MONDO terms: {obsolete}"


def test_disease_id_label_matches_disease_name() -> None:
    """Each row's MONDO label must be topically consistent with its ``disease_name``.

    This is the regression half of #1959: it catches a new row citing an id for an
    unrelated disease. A row whose name shares no token with the canonical label must be
    hand-verified into ``_TOPIC_ALLOWLIST`` (lay-vs-canonical naming), not left to slip.
    """
    snap = _snapshot()
    failures = []
    for r in _rows():
        mid = r["disease_id"]
        label = str(snap[mid]["label"])
        if mid in _TOPIC_ALLOWLIST:
            continue
        if not (_tokens(r["disease_name"]) & _tokens(label)):
            failures.append(
                f"{r['gene_symbol']}: name={r['disease_name']!r} but {mid} is {label!r}"
            )
    assert not failures, (
        "gene_phenotype rows cite a MONDO id whose label is unrelated to disease_name "
        "(add a hand-verified _TOPIC_ALLOWLIST entry only if the naming is genuinely "
        "lay-vs-canonical for the SAME disease):\n" + "\n".join(failures)
    )


def test_no_fabricated_consecutive_id_block() -> None:
    """No long run of consecutive MONDO ids across distinct genes.

    #1959's smoking gun was 16 unrelated genes carrying ``MONDO:0015612``–``0015627``
    with zero gaps — a counter, not ontology lookups. Real resolutions do not cluster
    like that, so a run longer than a couple of sibling subtypes is the signature.
    """
    numbered = sorted(
        (int(m.group(1)), r["gene_symbol"])
        for r in _rows()
        if (m := re.fullmatch(r"MONDO:(\d+)", r["disease_id"]))
    )
    longest, run = [], []
    for i, (num, gene) in enumerate(numbered):
        if run and num == run[-1][0] + 1 and gene != run[-1][1]:
            run.append((num, gene))
        else:
            run = [(num, gene)]
        if len(run) > len(longest):
            longest = list(run)
    assert len(longest) <= _MAX_CONSECUTIVE_RUN, (
        f"found {len(longest)} consecutive MONDO ids across distinct genes "
        f"(counter signature): {longest}"
    )


def test_topic_allowlist_entries_still_apply() -> None:
    """Every allowlist entry must still match a live (id, name) pair AND the canonical
    label it was verified against — self-cleaning on all three.

    If a row's id or name changes, or MONDO relabels the term, the exemption must be
    re-earned by hand rather than linger and keep excusing a pairing that no longer
    holds. Every row sharing an allowlisted id is checked, not just the first.
    """
    snap = _snapshot()
    rows_by_id: dict[str, list[dict[str, str]]] = {}
    for r in _rows():
        rows_by_id.setdefault(r["disease_id"], []).append(r)

    stale = []
    for mid, (name, canon) in _TOPIC_ALLOWLIST.items():
        rows = rows_by_id.get(mid)
        if not rows:
            stale.append(f"{mid}: no row cites this id anymore")
            continue
        if mid in snap and str(snap[mid]["label"]) != canon:
            stale.append(
                f"{mid}: allowlisted canonical label {canon!r} but snapshot now says "
                f"{snap[mid]['label']!r}"
            )
        for row in rows:
            if row["disease_name"] != name:
                stale.append(
                    f"{mid}: allowlisted name {name!r} but a row now says {row['disease_name']!r}"
                )
    assert not stale, (
        "stale _TOPIC_ALLOWLIST entries (re-verify by hand and update):\n" + "\n".join(stale)
    )


@pytest.mark.parametrize(
    ("gene", "disease_id"),
    [
        # Spot-locks for #1959's headline repairs — a silent revert to the fabricated
        # counter-block id must fail loudly.
        ("HEXA", "MONDO:0010100"),  # Tay-Sachs (was Mounier-Kuhn)
        ("GBA", "MONDO:0018150"),  # Gaucher (was congenital myasthenic syndrome)
        ("F5", "MONDO:0008560"),  # Factor V Leiden (was 16p12.1 deletion syndrome)
        ("TPMT", "MONDO:0012503"),  # TPMT deficiency (was Charcot-Marie-Tooth 1C)
        ("SLCO1B1", "MONDO:0009379"),  # Rotor syndrome (was CAH 11-beta-hydroxylase)
    ],
)
def test_repaired_rows_keep_verified_id(gene: str, disease_id: str) -> None:
    row = next((r for r in _rows() if r["gene_symbol"] == gene), None)
    assert row is not None, f"{gene} row missing from gene_phenotype_seed.csv"
    assert row["disease_id"] == disease_id, f"{gene} disease_id reverted"


def test_fabricated_pgx_rows_stay_dropped() -> None:
    """The PGx / response phenotypes #1959 withdrew (no real MONDO disease term exists)
    must not reappear — re-adding one silently re-introduces a fabricated id."""
    present = {r["gene_symbol"] for r in _rows()}
    must_stay_gone = {"CYP2D6", "CYP2C19", "CYP2C9", "CYP3A5", "PON1", "ABCB1", "MC1R", "HERC2"}
    resurrected = present & must_stay_gone
    assert not resurrected, (
        f"genes withdrawn by #1959 (no MONDO disease term) reappeared: {sorted(resurrected)} — "
        "if a real MONDO disease now exists, add it with a snapshot entry and remove from here"
    )


# ── inheritance-column contract (#2043) ──────────────────────────────────────
#
# `inheritance` is not decoration: `_lookup_gene_phenotype`
# (backend/annotation/engine.py) prefers the first association carrying HPO terms
# *or* an inheritance pattern when choosing a gene's primary disease, and ships the
# value to the frontend as `inheritance_pattern`. A blank on a Mendelian row both
# demotes that row in primary selection and renders a monogenic disease with no
# inheritance mode.
#
# Blank is legitimate for complex/polygenic susceptibility rows, which have no
# Mendelian pattern to state. That was previously implicit, so nothing distinguished
# "polygenic, deliberately blank" from "Mendelian, value missing" — and #2043's own
# survey mis-classified IL10-related early-onset IBD as a susceptibility trait when
# it is autosomal recessive (PMID:19890111, PMID:28267044).
#
# Each entry is the reason blank is correct for that gene. Adding a row without an
# inheritance value now FAILS unless it is listed here deliberately.
_POLYGENIC_BLANK_INHERITANCE: dict[str, str] = {
    "APOE": "Alzheimer risk allele; complex susceptibility, not Mendelian",
    "TCF7L2": "T2D susceptibility locus (polygenic)",
    "SLC30A8": "T2D susceptibility locus (polygenic)",
    "IGF2BP2": "T2D susceptibility locus (polygenic)",
    "PPARG": "T2D susceptibility locus (polygenic)",
    "HHEX": "T2D susceptibility locus (polygenic)",
    "PTPN22": "autoimmune susceptibility allele (polygenic)",
    "HLA-DRB1": "MS/autoimmune HLA susceptibility (polygenic)",
    "TNF": "inflammatory susceptibility variation (polygenic)",
    "IL1B": "inflammatory response variation (polygenic)",
    "SLC6A4": "depression susceptibility (polygenic)",
    "TPH2": "mood-disorder susceptibility (polygenic)",
    "ADRA2A": "ADHD susceptibility (polygenic)",
    "MCM6": "lactase persistence regulatory variant; a trait, not a disease",
}


def test_every_non_polygenic_row_declares_an_inheritance() -> None:
    """A Mendelian row must state its inheritance; only listed polygenic rows may be blank."""
    missing = [
        f"{r['gene_symbol']} ({r['disease_name']})"
        for r in _rows()
        if not r["inheritance"].strip() and r["gene_symbol"] not in _POLYGENIC_BLANK_INHERITANCE
    ]
    assert not missing, (
        "rows with a blank `inheritance` that are not listed as polygenic:\n"
        + "\n".join(missing)
        + "\n\nEither supply the inheritance pattern (verified against an authoritative "
        "source) or add the gene to _POLYGENIC_BLANK_INHERITANCE with its reason."
    )


def test_polygenic_blank_entries_still_apply() -> None:
    """Self-cleaning, like _TOPIC_ALLOWLIST: an exemption must still be needed.

    If a listed gene disappears or later gains an inheritance value, the entry has to
    be removed by hand rather than linger and keep excusing a blank that no longer
    exists.
    """
    by_gene = {r["gene_symbol"]: r for r in _rows()}
    stale = []
    for gene in _POLYGENIC_BLANK_INHERITANCE:
        row = by_gene.get(gene)
        if row is None:
            stale.append(f"{gene}: no row carries this gene anymore")
        elif row["inheritance"].strip():
            stale.append(
                f"{gene}: listed as polygenic-blank but the row now declares "
                f"{row['inheritance']!r}"
            )
    assert not stale, "stale _POLYGENIC_BLANK_INHERITANCE entries (remove by hand):\n" + "\n".join(
        stale
    )


def test_il10_row_states_autosomal_recessive() -> None:
    """IL10-related early-onset IBD is monogenic recessive, not a susceptibility trait.

    #2043 surveyed the blank-inheritance rows as "all complex/polygenic susceptibility
    traits" and enumerated 14 of the 15, omitting IL10. Its row names a specific
    monogenic disease: IL10/IL10R deficiency causes very-early-onset IBD through
    homozygous or compound-heterozygous loss-of-function variants
    (PMID:19890111 Glocker 2009 NEJM; PMID:28267044 Huang 2017, disjoint cohort).
    A guard keyed only on "blank == polygenic" would have frozen the wrong value here.
    """
    row = next((r for r in _rows() if r["gene_symbol"] == "IL10"), None)
    assert row is not None, "IL10 row missing from gene_phenotype_seed.csv"
    assert row["disease_id"] == "MONDO:0016542"
    assert row["inheritance"] == "Autosomal recessive"
    assert "IL10" not in _POLYGENIC_BLANK_INHERITANCE, (
        "IL10 is Mendelian recessive; it must not be exempted as polygenic"
    )


def test_vkorc1_keeps_autosomal_dominant() -> None:
    """VKORC1 coumarin resistance is dominant, and the row must not drift to recessive.

    The same gene carries two phenotypes with opposite inheritance: heterozygous
    missense variants cause warfarin/coumarin resistance, while homozygous loss of
    function causes VKCFD2, which is recessive (PMID:14765194 Rost 2004 Nature;
    PMID:26513304 Lewis 2016). This row names resistance, so dominant is correct --
    #2043 asked for that confirmation explicitly.
    """
    row = next((r for r in _rows() if r["gene_symbol"] == "VKORC1"), None)
    assert row is not None, "VKORC1 row missing from gene_phenotype_seed.csv"
    assert row["disease_name"] == "Coumarin (warfarin) resistance"
    assert row["inheritance"] == "Autosomal dominant"
