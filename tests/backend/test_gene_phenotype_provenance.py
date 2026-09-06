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
import hashlib
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "gene_phenotype_seed.csv"
_SNAPSHOT_PATH = _ROOT / "tests" / "fixtures" / "mondo_label_snapshot.json"
_PACKET = _ROOT / "data" / "science-evidence" / "2026-08-31-gene-phenotype-inheritance-2043"

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
# `inheritance` reaches the user: `_lookup_gene_phenotype`
# (backend/annotation/engine.py) ships it to the frontend as
# `inheritance_pattern`, so a wrong or missing pattern is a wrong or missing
# statement about how a disease is transmitted, shown beside a variant.
#
# #2043 asked to make the column's contract explicit. Its premise -- that every
# blank row is a complex/polygenic susceptibility trait -- was wrong: it
# enumerated 14 of the 15 blanks and omitted IL10, whose row names a monogenic
# disease (MONDO:0016542). A guard keyed on "blank means polygenic" would have
# frozen a blank onto it and certified that as intended.
#
# So the exemption list below records ONLY an observable fact about each row --
# that it carries no inheritance value and none has been verified against an
# authoritative source. It deliberately makes NO biological claim: asserting
# "this gene is polygenic" for 14 rows with no evidence would repeat the error
# this guard exists to prevent, and would turn an unevidenced classification
# into a test-enforced gate.
#
# Keyed by (gene_symbol, disease_id), not by gene: a gene may legitimately carry
# several rows (HBB does), and a gene-wide exemption would silently excuse a new
# Mendelian row whose inheritance was simply omitted.
_UNVERIFIED_BLANK_INHERITANCE: frozenset[tuple[str, str]] = frozenset(
    {
        ("APOE", "MONDO:0004975"),
        ("TCF7L2", "MONDO:0005148"),
        ("SLC30A8", "MONDO:0005148"),
        ("IGF2BP2", "MONDO:0005148"),
        ("PPARG", "MONDO:0005148"),
        ("HHEX", "MONDO:0005148"),
        ("PTPN22", "MONDO:0007179"),
        ("HLA-DRB1", "MONDO:0007462"),
        ("TNF", "MONDO:0021166"),
        ("IL1B", "MONDO:0021166"),
        ("SLC6A4", "MONDO:0002050"),
        ("TPH2", "MONDO:0005371"),
        ("ADRA2A", "MONDO:0007743"),
        ("MCM6", "MONDO:0006065"),
        # Inheritance unverified pending the two-independent-source gate; the
        # exemption records that and nothing else -- see
        # test_il10_inheritance_is_withheld_for_now.
        ("IL10", "MONDO:0016542"),
    }
)


def _association(gene_symbol: str, disease_id: str) -> dict[str, str] | None:
    """Select one gene-disease row by both keys.

    A gene may carry several rows (HBB does, and the unverified-blank exemption is
    keyed by ``(gene_symbol, disease_id)`` for that reason), so "the first row for
    this gene" does not identify an association; a valid second IL10 or VKORC1 row
    inserted earlier in the CSV would silently redirect a gene-only lookup.
    """
    return next(
        (r for r in _rows() if r["gene_symbol"] == gene_symbol and r["disease_id"] == disease_id),
        None,
    )


def _canonical_inheritance_values() -> frozenset[str]:
    """The union of the repository's own controlled vocabularies, not one invented here.

    Two production loaders write this column. The OMIM parser emits
    ``_OMIM_INHERITANCE_ABBREVS`` values; the MONDO/HPO loader that actually seeds
    ``gene_phenotype`` emits ``_INHERITANCE_MAP`` values, several of which the OMIM
    table lacks (``Polygenic``, ``Somatic``, ``Semidominant``, ``Autosomal dominant
    with reduced penetrance``). A seed refreshed from either loader must pass, so
    the guard accepts exactly what production can write and nothing else.
    """
    from backend.annotation.mondo_hpo import _INHERITANCE_MAP
    from backend.annotation.omim import _OMIM_INHERITANCE_ABBREVS

    return frozenset(_OMIM_INHERITANCE_ABBREVS.values()) | frozenset(_INHERITANCE_MAP.values())


def _is_unexcused_blank(row: dict[str, str]) -> bool:
    """A row with no inheritance value whose exact association is not listed as unverified.

    Keyed on ``(gene_symbol, disease_id)`` deliberately -- see
    ``test_a_new_mendelian_row_for_a_listed_gene_is_not_excused``.
    """
    return (
        not row["inheritance"].strip()
        and (row["gene_symbol"], row["disease_id"]) not in _UNVERIFIED_BLANK_INHERITANCE
    )


def test_every_row_declares_or_is_listed_as_unverified() -> None:
    """A row must state its inheritance, or be listed as having none verified."""
    missing = [
        f"{r['gene_symbol']} / {r['disease_id']} ({r['disease_name']})"
        for r in _rows()
        if _is_unexcused_blank(r)
    ]
    assert not missing, (
        "rows with a blank `inheritance` that are not listed as unverified:\n"
        + "\n".join(missing)
        + "\n\nEither supply the pattern (verified against an authoritative source) or add "
        "the (gene_symbol, disease_id) pair to _UNVERIFIED_BLANK_INHERITANCE."
    )


def test_declared_inheritance_uses_the_controlled_vocabulary() -> None:
    """A nonblank value must be a recognised pattern, not free text.

    Without this, `Autosomal recesive` satisfies a "row declares something" check,
    renders verbatim to the user, and is invisible to any consumer matching on the
    canonical spelling.
    """
    allowed = _canonical_inheritance_values()
    bad = [
        f"{r['gene_symbol']} / {r['disease_id']}: {r['inheritance']!r}"
        for r in _rows()
        if r["inheritance"].strip() and r["inheritance"].strip() not in allowed
    ]
    assert not bad, (
        "inheritance values outside the controlled vocabulary "
        f"({sorted(allowed)}):\n" + "\n".join(bad)
    )


def _rows_by_association() -> dict[tuple[str, str], list[dict[str, str]]]:
    """Every row per ``(gene_symbol, disease_id)`` -- a list, never collapsed.

    Collapsing into one row per key would let a duplicate association hide: a
    blank copy behind a declared one passes the row sweep (excused), the
    vocabulary check (declared) and the stale-exemption check (blank) at once,
    while the loader inserts both and their lookup order is ambiguous.
    """
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for r in _rows():
        grouped.setdefault((r["gene_symbol"], r["disease_id"]), []).append(r)
    return grouped


def test_seed_has_no_duplicate_associations() -> None:
    """One row per (gene_symbol, disease_id): duplicates make the contract ambiguous."""
    duplicates = {key: len(rows) for key, rows in _rows_by_association().items() if len(rows) > 1}
    assert not duplicates, f"associations listed more than once in the seed: {duplicates}"


def test_unverified_entries_still_apply() -> None:
    """Self-cleaning, like _TOPIC_ALLOWLIST: an exemption must still be needed.

    Evaluates every row carrying the key rather than the last one seen, so a
    declared copy cannot be shadowed by a blank duplicate.
    """
    grouped = _rows_by_association()
    stale = []
    for key in sorted(_UNVERIFIED_BLANK_INHERITANCE):
        rows = grouped.get(key, [])
        if not rows:
            stale.append(f"{key}: no row carries this (gene, disease_id) anymore")
        for row in rows:
            if row["inheritance"].strip():
                stale.append(
                    f"{key}: listed as unverified but the row now declares "
                    f"{row['inheritance']!r} -- drop the entry"
                )
    assert not stale, (
        "stale _UNVERIFIED_BLANK_INHERITANCE entries (remove by hand):\n" + "\n".join(stale)
    )


def test_every_seed_association_is_in_the_checked_in_fixture_unchanged() -> None:
    """The fixture is what ``_lookup_gene_phenotype`` serves; the CSV is not.

    The production-path regression below exercises two associations. This one
    compares every seed association with the checked-in ``mini_reference.db``:
    the same key set, and the same inheritance for each (a blank in the seed is
    NULL or empty in the fixture). A seed edit without a fixture regeneration
    fails here instead of reaching fixture-backed consumers unnoticed.
    """
    import sqlite3

    fixture = _ROOT / "tests" / "fixtures" / "mini_reference.db"
    assert fixture.exists(), "checked-in mini_reference.db is missing"
    seed = {key: rows[0]["inheritance"].strip() for key, rows in _rows_by_association().items()}
    con = sqlite3.connect(fixture)
    try:
        fixture_rows = con.execute(
            "SELECT gene_symbol, disease_id, inheritance FROM gene_phenotype"
        ).fetchall()
    finally:
        con.close()
    # The production query orders only by these keys, so a duplicated association
    # in the fixture could serve either copy; detect it before collapsing.
    fixture_keys = [(gene, disease) for gene, disease, _ in fixture_rows]
    duplicated = sorted({key for key in fixture_keys if fixture_keys.count(key) > 1})
    assert not duplicated, f"associations stored more than once in the fixture: {duplicated}"
    stored = {
        (gene, disease): (inheritance or "").strip() for gene, disease, inheritance in fixture_rows
    }
    assert len(seed) >= 30, "anti-vacuity: the seed is expected to carry dozens of associations"
    assert set(stored) == set(seed), (
        f"fixture and seed disagree on which associations exist -- only in seed: "
        f"{sorted(set(seed) - set(stored))}; only in fixture: {sorted(set(stored) - set(seed))}"
    )
    drift = {key: (seed[key], stored[key]) for key in seed if seed[key] != stored[key]}
    assert not drift, (
        "inheritance differs between the seed and the checked-in fixture "
        f"(seed, fixture): {drift} -- regenerate mini_reference.db"
    )


def test_a_new_mendelian_row_for_a_listed_gene_is_not_excused() -> None:
    """The reason the exemption is keyed by (gene, disease_id) rather than gene.

    APOE is listed as unverified for MONDO:0004975. A *second* APOE row for a
    different disease with a blank inheritance must still fail, because the
    exemption covers one association, not the gene. The check runs the same
    predicate the row sweep uses, on synthetic rows, so a predicate that regressed
    to keying on ``gene_symbol`` alone fails here rather than passing vacuously.
    """
    assert ("APOE", "MONDO:0004975") in _UNVERIFIED_BLANK_INHERITANCE
    assert ("APOE", "MONDO:0007088") not in _UNVERIFIED_BLANK_INHERITANCE, (
        "test fixture assumption broken: this pair must not be exempted"
    )
    listed_blank = {
        "gene_symbol": "APOE",
        "disease_id": "MONDO:0004975",
        "disease_name": "Alzheimer disease",
        "inheritance": "",
    }
    second_apoe_blank = {**listed_blank, "disease_id": "MONDO:0007088"}
    second_apoe_declared = {**second_apoe_blank, "inheritance": "Autosomal dominant"}

    assert not _is_unexcused_blank(listed_blank), "the listed association is excused"
    assert _is_unexcused_blank(second_apoe_blank), (
        "a blank second APOE row must not be excused by the gene-wide listing"
    )
    assert not _is_unexcused_blank(second_apoe_declared), "a declared row needs no excuse"


def test_controlled_vocabulary_spans_both_production_loaders() -> None:
    """Neither loader's vocabulary may be dropped from the guard.

    The OMIM table and the MONDO/HPO map each carry values the other lacks, so a
    guard built from one alone rejects rows the other loader legitimately writes.
    """
    from backend.annotation.mondo_hpo import _INHERITANCE_MAP
    from backend.annotation.omim import _OMIM_INHERITANCE_ABBREVS

    omim = frozenset(_OMIM_INHERITANCE_ABBREVS.values())
    hpo = frozenset(_INHERITANCE_MAP.values())
    allowed = _canonical_inheritance_values()

    assert hpo - omim, "anti-vacuity: the HPO map contributes nothing the OMIM table lacks"
    assert omim - hpo, "anti-vacuity: the OMIM table contributes nothing the HPO map lacks"
    assert hpo <= allowed, f"HPO-only values rejected by the guard: {sorted(hpo - allowed)}"
    assert omim <= allowed, f"OMIM-only values rejected by the guard: {sorted(omim - allowed)}"


def test_il10_inheritance_is_withheld_for_now() -> None:
    """IL10's inheritance is withheld for want of evidence, and not reclassified.

    #2043 surveyed the blank rows as "all complex/polygenic susceptibility traits"
    and enumerated 14 of the 15, omitting IL10. The row's MONDO term carries an
    inheritance mode in its synonyms, so relabelling the row *polygenic* on the
    strength of a blank would assert something the evidence does not support --
    and so would any other mode: the exemption records only that the field is
    unverified.

    It is listed as UNVERIFIED instead. MONDO's own synonyms for the term include
    "autosomal recessive early-onset inflammatory bowel disease", but this row is
    gene-keyed: `_lookup_gene_phenotype` returns its value as the
    `inheritance_pattern` shown beside an *IL10* variant, which makes it a
    per-gene claim. Three receptor cohorts -- PMID:19890111, PMID:28267044 and
    PMID:21519361 -- characterise only the IL10RA/IL10RB receptor forms. The
    retained searches surfaced two cohorts reporting IL10 ligand mutation carriers,
    PMID:22549091 and PMID:24216686, but they share contributing-clinician authors
    and the retained records cannot show they do not share patients, so the pair
    is not accepted as two independent sources. Per the evidence contract, the
    value is withheld rather than guessed (see the packet's candidates_assessed).

    This test pins both halves: IL10 must stay listed as unverified, and it must
    never be reclassified as polygenic on the strength of its blank.
    """
    row = _association("IL10", "MONDO:0016542")
    assert row is not None, "IL10 / MONDO:0016542 row missing from gene_phenotype_seed.csv"
    assert ("IL10", "MONDO:0016542") in _UNVERIFIED_BLANK_INHERITANCE, (
        "IL10 carries no verified inheritance; it must be listed as unverified"
    )
    assert not row["inheritance"].strip(), (
        "an inheritance value was added for IL10 -- supply two agreeing "
        "ligand-specific sources and drop the unverified entry, or leave it blank"
    )


def test_checked_in_fixture_emits_inheritance_through_the_production_lookup(tmp_path) -> None:
    """The seed is not the consumer: assert the emitted value, not the CSV.

    A CSV-only assertion stays green if the checked-in reference database or
    `_lookup_gene_phenotype` drops the field, while users receive a blank
    `inheritance_pattern` (#2043 review).

    The committed fixture carries no ``mondo_hpo`` version stamp, and
    ``_is_legacy_disease_scope_install`` withholds every ``mondo_hpo`` row without
    positive proof of the current loader revision. So the test copies the fixture
    and stamps a current-revision version -- exercising the serving path a real
    install takes, rather than the withheld one.
    """
    import shutil

    import sqlalchemy as sa

    from backend.annotation.engine import _lookup_gene_phenotype
    from backend.annotation.mondo_hpo import MONDO_HPO_INGESTION_REVISION
    from backend.db.tables import database_versions

    fixture = _ROOT / "tests" / "fixtures" / "mini_reference.db"
    assert fixture.exists(), "checked-in mini_reference.db is missing"
    working = tmp_path / "mini_reference.db"
    shutil.copy(fixture, working)

    resistance_row = _association("VKORC1", "MONDO:0007390")
    assert resistance_row is not None, "VKORC1 / MONDO:0007390 row missing from the seed"
    expected_vkorc1 = resistance_row["inheritance"].strip()
    engine = sa.create_engine(f"sqlite:///{working}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.delete(database_versions).where(database_versions.c.db_name == "mondo_hpo")
            )
            conn.execute(
                sa.insert(database_versions).values(
                    db_name="mondo_hpo",
                    version=f"2026-01-01+{MONDO_HPO_INGESTION_REVISION}",
                )
            )
        emitted = _lookup_gene_phenotype(
            {
                "rs9923231": {"gene_symbol": "VKORC1"},
                "rsIL10": {"gene_symbol": "IL10"},
            },
            engine,
        )
    finally:
        engine.dispose()

    # A row WITH a verified value must reach the consumer intact -- and it must be
    # THIS association: a second VKORC1 disease sorting ahead of it would still
    # emit an inheritance_pattern, so pin the disease before reading the value.
    assert "rs9923231" in emitted, "VKORC1 association did not resolve through the lookup"
    assert emitted["rs9923231"]["disease_id"] == "MONDO:0007390"
    assert emitted["rs9923231"]["inheritance_pattern"] == expected_vkorc1 == "Autosomal dominant"

    # A withheld row must still be SERVED, and emit nothing rather than a
    # placeholder. Requiring the key first means a fixture regeneration that
    # drops IL10, or a lookup that stops returning the association, fails here
    # instead of vacuously passing the blank check (#2043 review).
    assert "rsIL10" in emitted, "IL10 association did not resolve through the lookup"
    assert emitted["rsIL10"]["disease_id"] == "MONDO:0016542", (
        "the served IL10 association is not the early-onset IBD row this test covers"
    )
    assert not emitted["rsIL10"]["inheritance_pattern"], (
        "IL10 inheritance is withheld; the lookup must not emit a value"
    )


def test_evidence_packet_payload_digests_match_the_manifest() -> None:
    """Every retained payload's SHA-256 and byte length equal what the manifest records.

    The packet's ``requests`` rows are the durable integrity record of the
    retained responses. Without this check a payload could be edited or
    regenerated while the manifest kept its old digest and the suite stayed
    green, so the metadata the packet relies on would silently become false.
    Parity between ``raw/`` and the manifest is required in both directions: an
    unlisted file and a listed-but-missing file both fail.
    """
    manifest = json.loads((_PACKET / "source-manifest.json").read_text(encoding="utf-8"))
    rows = manifest["requests"]
    assert len(rows) >= 20, "manifest lists too few payload rows to be the real packet"
    listed: list[str] = []
    for row in rows:
        payload = row["payload"]
        assert payload.startswith("raw/"), payload
        path = _PACKET / payload
        assert path.is_file(), f"{payload} is listed in the manifest but missing from raw/"
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["sha256"], f"sha256 drift: {payload}"
        assert len(data) == row["bytes"], f"byte-length drift: {payload}"
        listed.append(payload)
    assert len(listed) == len(set(listed)), "a payload is listed twice"
    on_disk = sorted(f"raw/{path.name}" for path in (_PACKET / "raw").iterdir())
    assert on_disk == sorted(listed), "raw/ and the manifest are not in 1:1 correspondence"
