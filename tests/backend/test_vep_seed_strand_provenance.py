"""Offline provenance guard for ``vep_seed.csv``'s annotation columns.

``tests/fixtures/seed_csvs/vep_seed.csv`` seeds ``mini_vep_bundle.db``, whose rows
``backend/annotation/engine.py`` copies into ``annotated_variants`` unchanged. It
shipped a ``strand`` column that was wrong for **19 of its 57 annotated genes**,
and ``rs28399504`` (CYP2C19``*4``) annotated ``missense_variant`` / ``p.Met1Val``
when Ensembl VEP calls it ``start_lost`` / ``p.Met1?`` -- downgrading a HIGH-impact
consequence to MODERATE and asserting a residue at a codon that is no longer a
start (#2045).

Three guards, cheapest first:

1. **Internal consistency** needs no network and no snapshot: a gene has exactly
   one strand, so two rows of the same gene disagreeing is provably wrong on its
   own terms. Four genes did (CYP2C19, MTHFR, PON1, CYP2R1).
2. **Snapshot agreement** against ``tests/fixtures/vep_gene_strand_snapshot.json``
   (Ensembl GRCh37 ``lookup/symbol``). Loud like the gene-phenotype guard rather
   than silently skipping, so a new gene cannot slip in unchecked.
3. **Start-loss semantics**: a variant at ``c.1`` of a coding transcript destroys
   the initiator codon, so it cannot be a ``missense_variant`` naming a
   substituted residue.

None of these touches the network at test time.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from scripts.build_vep_strand_snapshot import SYNTHETIC_GENES, reduce_response, validate_response
from scripts.build_vep_strand_snapshot import _genes as _generator_genes
from scripts.build_vep_strand_snapshot import main as _generator_main

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "vep_seed.csv"
_SNAPSHOT = _ROOT / "tests" / "fixtures" / "vep_gene_strand_snapshot.json"
_BUNDLE = _ROOT / "tests" / "fixtures" / "mini_vep_bundle.db"

# The seed carries one deliberately synthetic locus (rs12345 / ENST00000999999),
# used by several suites as a neutral stand-in. It has no Ensembl record, so it is
# exempt from the snapshot check -- but the exemption is pinned to this exact
# symbol, and `test_only_the_known_synthetic_gene_is_exempt` fails if another
# unresolvable symbol appears, so a fabricated gene cannot hide behind it. The set
# is the generator's own, so the guard cannot exempt a symbol the generator still
# submits to Ensembl (which fails the rebuild) or vice versa -- and because that
# shared set would let a widened exemption pass every guard at once,
# `test_synthetic_exemption_is_exactly_the_one_reviewed_symbol` pins it to a
# literal that lives here, not in the generator.
_SYNTHETIC_GENES = SYNTHETIC_GENES

# "c.1A>G" -- a substitution at the first coding base. Anchored so "c.10A>G" and
# "c.1_2del" do not match: only a single-base substitution at position 1.
_CODING_START = re.compile(r"^c\.1[ACGT]>[ACGT]$")

# A protein change naming a concrete residue at codon 1, e.g. "p.Met1Val".
_NAMED_START_RESIDUE = re.compile(r"^p\.Met1(?!\?)[A-Za-z]{2,3}$")


def _rows() -> list[dict[str, str]]:
    with _SEED.open(encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row.get("gene_symbol")]


def _snapshot() -> dict[str, dict[str, str]]:
    return json.loads(_SNAPSHOT.read_text(encoding="utf-8"))["strands"]


def _strands_by_gene(*, include_synthetic: bool = False) -> dict[str, set[str]]:
    by_gene: dict[str, set[str]] = collections.defaultdict(set)
    for row in _rows():
        gene = row["gene_symbol"]
        if not include_synthetic and gene in _SYNTHETIC_GENES:
            continue
        by_gene[gene].add(row["strand"])
    return dict(by_gene)


def test_only_the_known_synthetic_gene_is_exempt() -> None:
    """The exemption must not become a hiding place for a fabricated symbol."""
    snapshot = set(_snapshot())
    unresolvable = {
        gene for gene in _strands_by_gene(include_synthetic=True) if gene not in snapshot
    }
    assert unresolvable == _SYNTHETIC_GENES, (
        f"unresolvable gene symbols in vep_seed.csv: {sorted(unresolvable)} -- expected only "
        f"{sorted(_SYNTHETIC_GENES)}. A real gene missing from the snapshot means the snapshot "
        "needs regenerating; an unknown symbol means the row is fabricated."
    )


def test_synthetic_exemption_is_exactly_the_one_reviewed_symbol() -> None:
    """The exemption is pinned to a literal here, independently of the generator.

    Every other guard in this module compares against the imported
    ``SYNTHETIC_GENES`` -- the same set that drives the generator's exclusion and
    the snapshot's ``synthetic_excluded`` provenance. Add a symbol to that set and
    regenerate, and it is skipped everywhere while every guard still passes
    (#2045 review). Widening the exemption must therefore be a deliberate,
    reviewed edit to THIS literal as well, never a side effect of editing the
    generator: the value is spelled out rather than derived from the import.
    """
    assert SYNTHETIC_GENES == frozenset({"GENE1"}), (
        f"the synthetic-gene exemption is now {sorted(SYNTHETIC_GENES)}; if widening it is "
        "intended, update this literal in the same reviewed change and say why the new "
        "symbol has no Ensembl record"
    )


def test_generator_skips_the_synthetic_gene_it_cannot_resolve() -> None:
    """The documented rebuild must be runnable.

    The generator fails closed on any symbol Ensembl cannot resolve, so feeding it
    ``GENE1`` made ``scripts/build_vep_strand_snapshot.py`` exit 1 on the very seed
    it documents rebuilding from (#2045 review). Its input must be exactly the
    genes the committed snapshot covers: nothing synthetic, nothing missing.
    """
    submitted = set(_generator_genes())
    assert submitted.isdisjoint(_SYNTHETIC_GENES), (
        f"generator submits synthetic symbols to Ensembl: {sorted(submitted & _SYNTHETIC_GENES)}"
    )
    assert submitted == set(_snapshot()), (
        "generator input and committed snapshot disagree -- "
        f"only in generator: {sorted(submitted - set(_snapshot()))}; "
        f"only in snapshot: {sorted(set(_snapshot()) - submitted)}"
    )


def _fake_lookup_payloads() -> dict[str, dict]:
    """One canned lookup/symbol-shaped response per gene the generator submits."""
    return {
        gene: {
            "id": f"ENSG{index:011d}",
            "strand": 1 if index % 2 else -1,
            "display_name": gene,
            "assembly_name": "GRCh37",
            "object_type": "Gene",
            "start": 1_000 + index,
            "end": 2_000 + index,
            "description": f"fake record {index} [Source:test]",
        }
        for index, gene in enumerate(_generator_genes())
    }


def test_generator_retains_the_verbatim_responses_it_reduces(tmp_path: Path) -> None:
    """The snapshot must name the source-native payload it was reduced from.

    The first revision of the generator reduced each Ensembl response to strand
    and id and discarded it, so a regenerated oracle passed every guard while no
    payload existed to re-derive it from (#2364 review). Drive the write path with
    a fake fetcher: the evidence file must hold every response verbatim, keyed by
    symbol, and the snapshot's provenance must name that file and its sha256.
    """
    payloads = _fake_lookup_payloads()
    evidence = tmp_path / "evidence" / "lookup.json"
    out = tmp_path / "snapshot.json"

    rc = _generator_main(
        ["--evidence-out", str(evidence), "--out", str(out)],
        fetch=lambda gene: payloads.get(gene),
        root=tmp_path,
    )

    assert rc == 0
    written = json.loads(evidence.read_text(encoding="utf-8"))
    assert written["responses"] == payloads, "retained responses are not the fetched ones"
    assert set(written) == {"_artifact_note", "responses"}, "wrapper must be the only addition"
    snapshot = json.loads(out.read_text(encoding="utf-8"))
    provenance = snapshot["_provenance"]
    assert provenance["evidence_path"] == "evidence/lookup.json", (
        "path must be root-relative POSIX"
    )
    assert (tmp_path / provenance["evidence_path"]).resolve() == evidence.resolve()
    assert provenance["evidence_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()
    assert provenance["evidence_bytes"] == evidence.stat().st_size
    assert snapshot["strands"] == {
        gene: {"strand": "+" if payload["strand"] == 1 else "-", "ensembl_gene_id": payload["id"]}
        for gene, payload in payloads.items()
    }, "snapshot is not the strand/id reduction of the retained responses"
    assert set(snapshot["strands"]) == set(_generator_genes())
    assert set(provenance["synthetic_excluded"]) == _SYNTHETIC_GENES


def test_generator_refuses_to_run_without_an_evidence_path(tmp_path: Path) -> None:
    """No evidence path, no snapshot: the reduction must never outlive its source."""
    payloads = _fake_lookup_payloads()
    out = tmp_path / "snapshot.json"
    with pytest.raises(SystemExit) as refused:
        _generator_main(["--out", str(out)], fetch=lambda gene: payloads.get(gene), root=tmp_path)
    assert refused.value.code == 2, "argparse must reject the call as a usage error"
    assert not out.exists(), "a snapshot was written although no evidence path was given"


def _run_refused(tmp_path: Path, argv: list[str]) -> int:
    """Run the generator expecting a usage error; return its exit code, assert nothing written."""
    payloads = _fake_lookup_payloads()
    before = sorted(str(p) for p in tmp_path.rglob("*") if p.is_file())
    with pytest.raises(SystemExit) as refused:
        _generator_main(argv, fetch=lambda gene: payloads.get(gene), root=tmp_path)
    after = sorted(str(p) for p in tmp_path.rglob("*") if p.is_file())
    assert after == before, (
        f"a usage error must write nothing; new files: {set(after) - set(before)}"
    )
    return refused.value.code


def _retained_file(tmp_path: Path, payloads: dict[str, dict] | None = None) -> Path:
    retained = tmp_path / "retained.json"
    retained.write_text(
        json.dumps({"_artifact_note": "fake", "responses": payloads or _fake_lookup_payloads()}),
        encoding="utf-8",
    )
    return retained


def test_generator_refuses_paths_outside_the_repository(tmp_path: Path) -> None:
    """An evidence path outside the checkout would serialise a host path into the oracle."""
    inside_out = tmp_path / "snapshot.json"
    outside = tmp_path.parent / f"{tmp_path.name}-elsewhere"
    outside.mkdir()
    code = _run_refused(
        tmp_path, ["--evidence-out", str(outside / "evidence.json"), "--out", str(inside_out)]
    )
    assert code == 2
    assert not (outside / "evidence.json").exists()
    # --responses-from is serialised into the provenance too, so it obeys the same rule.
    retained_outside = outside / "retained.json"
    retained_outside.write_text(
        json.dumps({"responses": _fake_lookup_payloads()}), encoding="utf-8"
    )
    code = _run_refused(
        tmp_path,
        [
            "--evidence-out",
            str(tmp_path / "evidence.json"),
            "--out",
            str(inside_out),
            "--responses-from",
            str(retained_outside),
            "--accessed",
            "2026-09-05",
        ],
    )
    assert code == 2


def test_generator_refuses_colliding_output_paths(tmp_path: Path) -> None:
    """Snapshot over evidence would leave a provenance describing lost content."""
    same = tmp_path / "same.json"
    assert _run_refused(tmp_path, ["--evidence-out", str(same), "--out", str(same)]) == 2
    assert not same.exists()
    retained = _retained_file(tmp_path)
    code = _run_refused(
        tmp_path,
        [
            "--evidence-out",
            str(retained),
            "--out",
            str(tmp_path / "snapshot.json"),
            "--responses-from",
            str(retained),
            "--accessed",
            "2026-09-05",
        ],
    )
    assert code == 2
    assert json.loads(retained.read_text(encoding="utf-8"))["_artifact_note"] == "fake", (
        "the retained input was overwritten"
    )


def test_generator_accepts_accessed_only_when_replaying(tmp_path: Path) -> None:
    """A live fetch dates itself; a caller-supplied date there is a usage error."""
    code = _run_refused(
        tmp_path,
        [
            "--evidence-out",
            str(tmp_path / "evidence.json"),
            "--out",
            str(tmp_path / "snapshot.json"),
            "--accessed",
            "2026-09-05",
        ],
    )
    assert code == 2


@pytest.mark.parametrize(
    "accessed",
    [
        "1900-02-31",  # not a calendar date
        "2026-9-5",  # not YYYY-MM-DD
        "20260905",  # ISO basic form, which date.fromisoformat alone would accept
        (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)).isoformat(),  # future
    ],
)
def test_generator_refuses_impossible_or_future_access_dates(
    tmp_path: Path, accessed: str
) -> None:
    retained = _retained_file(tmp_path)
    code = _run_refused(
        tmp_path,
        [
            "--evidence-out",
            str(tmp_path / "evidence.json"),
            "--out",
            str(tmp_path / "snapshot.json"),
            "--responses-from",
            str(retained),
            "--accessed",
            accessed,
        ],
    )
    assert code == 2


def test_generator_dates_a_live_fetch_with_todays_utc_date(tmp_path: Path) -> None:
    payloads = _fake_lookup_payloads()
    evidence = tmp_path / "evidence.json"
    out = tmp_path / "snapshot.json"
    before = dt.datetime.now(dt.UTC).date().isoformat()
    rc = _generator_main(
        ["--evidence-out", str(evidence), "--out", str(out)],
        fetch=lambda gene: payloads.get(gene),
        root=tmp_path,
    )
    after = dt.datetime.now(dt.UTC).date().isoformat()
    assert rc == 0
    provenance = json.loads(out.read_text(encoding="utf-8"))["_provenance"]
    assert provenance["accessed"] in {before, after}
    assert "evidence_replayed_from" not in provenance
    note = json.loads(evidence.read_text(encoding="utf-8"))["_artifact_note"]
    assert f"fetched {provenance['accessed']}" in note


def test_generator_replay_records_the_given_date_and_source(tmp_path: Path) -> None:
    payloads = _fake_lookup_payloads()
    retained = _retained_file(tmp_path, payloads)
    evidence = tmp_path / "evidence.json"
    out = tmp_path / "snapshot.json"
    rc = _generator_main(
        [
            "--evidence-out",
            str(evidence),
            "--out",
            str(out),
            "--responses-from",
            str(retained),
            "--accessed",
            "2026-09-05",
        ],
        root=tmp_path,
    )
    assert rc == 0
    provenance = json.loads(out.read_text(encoding="utf-8"))["_provenance"]
    assert provenance["accessed"] == "2026-09-05"
    assert provenance["evidence_replayed_from"] == {
        "path": "retained.json",
        "sha256": hashlib.sha256(retained.read_bytes()).hexdigest(),
    }
    assert json.loads(evidence.read_text(encoding="utf-8"))["responses"] == payloads


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "SOMETHING_ELSE"),
        ("assembly_name", "GRCh38"),
        ("strand", 0),
        ("strand", True),
        ("id", "NM_000769"),
        ("id", "ENSG_NOT_A_GENE"),
        ("id", "ENSG0000016584"),
    ],
)
def test_generator_refuses_an_invalid_response_instead_of_coercing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str, value: object
) -> None:
    """Wrong symbol, foreign assembly, strand outside {1, -1} or a malformed id: refuse.

    The id check requires the complete Ensembl gene-identifier format (ENSG plus
    eleven digits), so a bare prefix or a truncated id is refused, not just a
    foreign accession.
    """
    payloads = _fake_lookup_payloads()
    victim = sorted(payloads)[3]
    payloads[victim] = {**payloads[victim], field: value}
    evidence = tmp_path / "evidence.json"
    out = tmp_path / "snapshot.json"

    rc = _generator_main(
        ["--evidence-out", str(evidence), "--out", str(out)],
        fetch=lambda gene: payloads.get(gene),
        root=tmp_path,
    )

    assert rc == 1
    assert not evidence.exists() and not out.exists(), "an invalid response must write nothing"
    err = capsys.readouterr().err
    assert victim in err and field in err, f"error must name the symbol and field: {err!r}"


def _assert_snapshot_is_the_reduction_of_its_evidence(snapshot_path: Path, root: Path) -> None:
    """Shared body of the committed-reduction guard, so a corrupted copy can be exercised."""
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    provenance = snapshot["_provenance"]
    assert not Path(provenance["evidence_path"]).is_absolute(), (
        "evidence_path must be root-relative"
    )
    evidence = root / provenance["evidence_path"]
    assert evidence.is_file(), f"snapshot names evidence that is not checked in: {evidence}"
    assert provenance["evidence_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest(), (
        "snapshot provenance sha256 does not match the checked-in evidence file"
    )
    assert provenance["evidence_bytes"] == evidence.stat().st_size
    responses = json.loads(evidence.read_text(encoding="utf-8"))["responses"]
    assert set(responses) == set(snapshot["strands"]), (
        f"evidence and snapshot cover different genes -- only in evidence: "
        f"{sorted(set(responses) - set(snapshot['strands']))}; only in snapshot: "
        f"{sorted(set(snapshot['strands']) - set(responses))}"
    )
    assert len(responses) >= 50, "anti-vacuity: expected ~57 retained responses"
    invalid = []
    for gene, payload in responses.items():
        try:
            validate_response(gene, payload)
        except ValueError as exc:
            invalid.append(str(exc))
    assert not invalid, "retained evidence carries invalid responses:\n" + "\n".join(invalid)
    wrong = sorted(
        f"{gene}: snapshot={entry} evidence={reduce_response(responses[gene])}"
        for gene, entry in snapshot["strands"].items()
        if entry != reduce_response(responses[gene])
    )
    assert not wrong, "snapshot is not the reduction of its evidence:\n" + "\n".join(wrong)


def test_committed_snapshot_is_the_reduction_of_its_named_evidence() -> None:
    """The checked-in oracle must name a retained payload and equal its reduction.

    Provenance alone is a claim; this re-derives the strands from the named
    evidence so the committed snapshot cannot drift from (or outlive) the
    responses it says it was built from.
    """
    _assert_snapshot_is_the_reduction_of_its_evidence(_SNAPSHOT, _ROOT)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strand", 0),
        ("assembly_name", "GRCh38"),
        ("display_name", "OTHER"),
        ("id", "LRG_1"),
        ("id", "ENSG_NOT_A_GENE"),
    ],
)
def test_committed_reduction_guard_rejects_a_corrupted_evidence_copy(
    tmp_path: Path, field: str, value: object
) -> None:
    """Negative control: the guard must not bless evidence it would refuse to reduce.

    Copy the committed snapshot and evidence into ``tmp_path``, corrupt one retained
    response, re-stamp the sha256 so the hash check passes, and require the guard
    to fail on the response itself -- a strand of 0 or a foreign assembly must not
    be silently coerced into a strand sign.
    """
    snapshot = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    evidence_rel = snapshot["_provenance"]["evidence_path"]
    evidence_copy = tmp_path / evidence_rel
    evidence_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_ROOT / evidence_rel, evidence_copy)
    retained = json.loads(evidence_copy.read_text(encoding="utf-8"))
    victim = sorted(retained["responses"])[5]
    retained["responses"][victim][field] = value
    evidence_copy.write_text(json.dumps(retained, indent=1) + "\n", encoding="utf-8")
    snapshot["_provenance"]["evidence_sha256"] = hashlib.sha256(
        evidence_copy.read_bytes()
    ).hexdigest()
    snapshot["_provenance"]["evidence_bytes"] = evidence_copy.stat().st_size
    snapshot_copy = tmp_path / "snapshot.json"
    snapshot_copy.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=rf"(?s)invalid responses.*{victim}.*{field}"):
        _assert_snapshot_is_the_reduction_of_its_evidence(snapshot_copy, tmp_path)


def test_snapshot_records_the_exclusion_the_guard_applies() -> None:
    """A snapshot built under a different exclusion set must not pass silently."""
    provenance = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))["_provenance"]
    assert set(provenance["synthetic_excluded"]) == _SYNTHETIC_GENES, (
        f"snapshot was generated excluding {provenance['synthetic_excluded']}, but the "
        f"guard exempts {sorted(_SYNTHETIC_GENES)} -- regenerate the snapshot"
    )


def test_seed_is_substantial_enough_to_guard() -> None:
    """Anti-vacuity: a guard over an empty or tiny sweep proves nothing."""
    rows = _rows()
    assert len(rows) >= 90, f"expected the annotated seed to be substantial, got {len(rows)} rows"
    assert len(_strands_by_gene()) >= 50, "expected ~56 real annotated genes in the sweep"


def test_each_gene_has_exactly_one_strand() -> None:
    """A gene's strand is invariant -- two rows disagreeing is self-contradictory.

    This half needs neither network nor snapshot: it is wrong on its own terms.
    """
    contradictory = {
        gene: sorted(strands) for gene, strands in _strands_by_gene().items() if len(strands) > 1
    }
    assert not contradictory, (
        "genes carrying more than one strand in vep_seed.csv (impossible for a real gene): "
        f"{contradictory}"
    )


def test_every_gene_strand_matches_the_ensembl_snapshot() -> None:
    """Loud on an uncovered gene, rather than skipping it."""
    snapshot = _snapshot()
    by_gene = _strands_by_gene()

    uncovered = sorted(set(by_gene) - set(snapshot))
    assert not uncovered, (
        f"genes absent from the strand snapshot: {uncovered} -- regenerate with "
        "scripts/build_vep_strand_snapshot.py"
    )

    wrong = [
        f"{gene}: seed={sorted(strands)} ensembl={snapshot[gene]['strand']}"
        for gene, strands in sorted(by_gene.items())
        if strands != {snapshot[gene]["strand"]}
    ]
    assert not wrong, "gene strands disagreeing with Ensembl GRCh37:\n" + "\n".join(wrong)


def test_every_bundle_gene_strand_matches_the_ensembl_snapshot() -> None:
    """The on-disk bundle is what production reads; the CSV is only its seed.

    The CSV sweep above cannot see a stale ``mini_vep_bundle.db``: if the bundle is
    not regenerated after a seed correction, every strand guard stays green while
    the annotation engine serves the old strands (#2045 review). Sweep every
    annotated bundle row against the same snapshot, so each of the 19 corrected
    genes is covered by the production artifact and not only by its seed.

    Coverage is asserted as *equality*, not containment: a bundle regenerated
    from a truncated seed could drop up to seven genes -- corrected ones among
    them -- and still clear an anti-vacuity floor of 50, so the bundle's
    annotated gene set must equal the snapshot's and the CSV's (#2364 review).
    """
    import sqlite3

    assert _BUNDLE.exists(), "checked-in mini_vep_bundle.db is missing"
    con = sqlite3.connect(_BUNDLE)
    try:
        stored = con.execute(
            "SELECT DISTINCT gene_symbol, strand FROM vep_annotations "
            "WHERE gene_symbol IS NOT NULL AND gene_symbol != ''"
        ).fetchall()
    finally:
        con.close()
    snapshot = _snapshot()
    genes = {gene for gene, _ in stored} - _SYNTHETIC_GENES
    assert len(genes) >= 50, (
        f"anti-vacuity: expected ~57 annotated bundle genes, found {len(genes)}"
    )
    assert genes == set(snapshot), (
        "bundle and strand snapshot cover different genes -- "
        f"only in bundle: {sorted(genes - set(snapshot))}; "
        f"only in snapshot (missing from the bundle): {sorted(set(snapshot) - genes)}"
    )
    assert genes == set(_strands_by_gene()), (
        "bundle and vep_seed.csv cover different genes (regenerate the bundle) -- "
        f"only in bundle: {sorted(genes - set(_strands_by_gene()))}; "
        f"only in CSV: {sorted(set(_strands_by_gene()) - genes)}"
    )
    wrong = sorted(
        f"{gene}: bundle={strand!r} ensembl={snapshot[gene]['strand']!r}"
        for gene, strand in stored
        if gene not in _SYNTHETIC_GENES and strand != snapshot[gene]["strand"]
    )
    assert not wrong, (
        "bundle strands disagreeing with Ensembl GRCh37 (regenerate the bundle):\n"
        + "\n".join(wrong)
    )


def test_snapshot_entries_still_apply() -> None:
    """Self-cleaning: a snapshot entry must still describe a gene the seed cites."""
    stale = sorted(set(_snapshot()) - set(_strands_by_gene()))
    assert not stale, f"snapshot genes no longer cited by vep_seed.csv: {stale}"


def test_a_start_codon_substitution_is_not_called_missense() -> None:
    """``c.1`` destroys the initiator methionine: that is a start loss.

    CYP2C19``*4`` (rs28399504, ``c.1A>G``) shipped as ``missense_variant`` /
    ``p.Met1Val``. Ensembl VEP calls it ``start_lost`` / ``p.Met1?`` -- HIGH impact,
    and it cannot name a downstream product. Calling it a missense understates the
    consequence for any severity ranking. (Nothing here asserts the allele's
    function; that is a pharmacogenomic claim the VEP payload does not make.)
    """
    offenders = []
    for row in _rows():
        if not _CODING_START.match(row.get("hgvs_coding", "") or ""):
            continue
        if row.get("consequence") != "start_lost":
            offenders.append(
                f"{row['rsid']} ({row['gene_symbol']}): {row['hgvs_coding']} is annotated "
                f"{row['consequence']!r}, expected 'start_lost'"
            )
        if _NAMED_START_RESIDUE.match(row.get("hgvs_protein", "") or ""):
            offenders.append(
                f"{row['rsid']} ({row['gene_symbol']}): hgvs_protein "
                f"{row['hgvs_protein']!r} names a residue at a codon that is no longer a start"
            )
    assert not offenders, "start-codon variants annotated as substitutions:\n" + "\n".join(
        offenders
    )


def test_cyp2c19_star4_stays_a_start_loss() -> None:
    """Spot-lock for #2045's headline row, so a silent revert fails loudly.

    This reads the CSV, so it cannot see a stale ``mini_vep_bundle.db`` or a lookup
    that drops a field. The production path is locked separately: the on-disk bundle
    through ``lookup_vep_by_rsids`` in ``test_vep_bundle_lookup.py`` and through
    ``run_annotation`` into ``annotated_variants`` in ``test_annotation_engine.py``.
    """
    row = next((r for r in _rows() if r["rsid"] == "rs28399504"), None)
    assert row is not None, "rs28399504 (CYP2C19*4) missing from vep_seed.csv"
    assert row["gene_symbol"] == "CYP2C19"
    assert row["transcript_id"] == "ENST00000371321"
    assert row["hgvs_coding"] == "c.1A>G"
    assert row["consequence"] == "start_lost"
    assert row["hgvs_protein"] == "p.Met1?"
    assert row["strand"] == "+"
