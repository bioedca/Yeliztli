"""Docs↔cancer-PRS status consistency guard (#2028).

The cancer module page used to advertise breast PRS as computed after the
canonical PRS77 model had become fail-closed at runtime.  Keep the page's active
trait list tied to the weight-set gates, and require a distinct explanation for
the disabled breast model so readers do not mistake it for ordinary ancestry
calibration withholding.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_PANEL_PATH = REPO_ROOT / "backend" / "data" / "panels" / "cancer_prs_weights.json"
_DOC_PATH = REPO_ROOT / "docs" / "modules" / "health-risk" / "cancer.md"
_EVIDENCE_DIR = (
    REPO_ROOT / "data" / "science-evidence" / "2026-08-03-breast-prs77-availability-2028"
)

_TRAIT_LABELS = {
    "breast_cancer": "breast",
    "prostate_cancer": "prostate",
    "colorectal_cancer": "colorectal",
    "melanoma": "melanoma",
}
_ADVERTISED_TRAITS_RE = re.compile(r"\(PRS\) for (?P<traits>[^.]+)\.", re.IGNORECASE)
_BREAST_PRS_NOTE_RE = re.compile(
    r'^!!! note "Breast-cancer PRS is currently unavailable"\n'
    r"(?P<body>(?: {4}.*(?:\n|$))+)",
    re.IGNORECASE | re.MULTILINE,
)
_REFERENCE_ENTRY_RE = re.compile(
    r"^\[(?P<number>[1-9][0-9]*)\]\s+(?P<body>.*?)(?=^\[[1-9][0-9]*\]\s+|\Z)",
    re.DOTALL | re.MULTILINE,
)
_PROVENANCE_ID_RE = re.compile(
    r"\b(?:PMID|DOI|ChEMBL|NCT):[A-Z0-9][A-Z0-9./_-]*",
    re.IGNORECASE,
)
_ACCESS_DATE_RE = re.compile(r"\(accessed (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\)", re.IGNORECASE)


def _assert_real_access_date(text: str, where: str) -> str:
    """Require a *calendar-valid* access date, not merely a digit pattern.

    A shape-only regex accepts impossible values such as ``2026-13-45``, and a
    typo propagated consistently across the documentation and the packet would
    otherwise satisfy every cross-check between them.
    """
    match = _ACCESS_DATE_RE.search(text)
    assert match, f"{where} must include an '(accessed YYYY-MM-DD)' date"
    raw = match.group("date")
    try:
        date.fromisoformat(raw)
    except ValueError:  # pragma: no cover - only reached on a malformed date
        raise AssertionError(f"{where} records an impossible access date: {raw}") from None
    return raw


def _weight_sets() -> list[dict[str, object]]:
    data = json.loads(_PANEL_PATH.read_text(encoding="utf-8"))
    return data["weight_sets"]


def _advertised_traits(doc_text: str) -> set[str]:
    match = _ADVERTISED_TRAITS_RE.search(doc_text)
    assert match, "cancer.md must retain an explicit '(PRS) for ...' active-trait list"
    prose = match.group("traits")
    advertised_labels = {
        label.strip().lower()
        for label in re.split(r",|\band\b", prose, flags=re.IGNORECASE)
        if label.strip()
    }
    trait_by_label = {label: trait for trait, label in _TRAIT_LABELS.items()}
    unknown = sorted(advertised_labels - trait_by_label.keys())
    assert not unknown, f"cancer.md advertises unknown PRS trait labels: {unknown}"
    return {trait_by_label[label] for label in advertised_labels}


def _breast_prs_note(doc_text: str) -> str:
    match = _BREAST_PRS_NOTE_RE.search(doc_text)
    assert match, "cancer.md must retain the breast-cancer PRS availability note"
    return " ".join(match.group("body").lower().split())


def _breast_weight_set() -> dict[str, object]:
    breast = next(
        (weight_set for weight_set in _weight_sets() if weight_set["trait"] == "breast_cancer"),
        None,
    )
    assert breast is not None, "cancer PRS panel must contain a breast_cancer weight set"
    return breast


def _breast_allele_audit() -> dict[str, object]:
    breast = _breast_weight_set()
    provenance = breast["model_provenance"]
    assert isinstance(provenance, dict), "breast weight set must carry model_provenance"
    audit = provenance["current_allele_audit"]
    assert isinstance(audit, dict), "breast model_provenance must carry current_allele_audit"
    return audit


def _reference_entry(doc_text: str, number: int) -> str:
    match = next(
        (
            candidate
            for candidate in _REFERENCE_ENTRY_RE.finditer(doc_text)
            if int(candidate.group("number")) == number
        ),
        None,
    )
    assert match, f"cancer.md must retain reference [{number}]"
    return " ".join(match.group("body").split())


def test_advertised_traits_reject_unknown_labels() -> None:
    with pytest.raises(AssertionError, match="unknown PRS trait labels.*lung"):
        _advertised_traits("(PRS) for prostate and lung.")


def test_cancer_docs_advertise_exactly_the_enabled_prs_models() -> None:
    doc_text = _DOC_PATH.read_text(encoding="utf-8")
    weight_sets = _weight_sets()
    assert weight_sets, "cancer PRS panel must contain at least one weight set"
    enabled = {
        weight_set["trait"]
        for weight_set in weight_sets
        if weight_set.get("scoring_enabled", True)
    }
    assert _advertised_traits(doc_text) == enabled, (
        "cancer.md's active PRS list must exactly match weight sets whose "
        "scoring_enabled gate is true"
    )


def test_cancer_docs_distinguish_the_runtime_block_from_ancestry_withholding() -> None:
    breast = _breast_weight_set()
    assert breast["model_status"] == "source_verified_runtime_blocked"
    assert breast["scoring_enabled"] is False

    doc_text = _DOC_PATH.read_text(encoding="utf-8")
    note_text = _breast_prs_note(doc_text)
    audit = _breast_allele_audit()
    multiallelic = audit["multiallelic_primary_loci"]
    biallelic = audit["biallelic_palindromic_loci"]
    blocked = audit["runtime_blocked_loci"]
    assert isinstance(multiallelic, int) and not isinstance(multiallelic, bool)
    assert isinstance(biallelic, int) and not isinstance(biallelic, bool)
    assert multiallelic > 0 and biallelic > 0, (
        "the audit must report a positive count in both blocked-locus groups, "
        "otherwise the documented rationale describes nothing"
    )
    assert blocked == multiallelic + biallelic, (
        "current_allele_audit is internally inconsistent: runtime_blocked_loci "
        f"({blocked}) is not multiallelic_primary_loci ({multiallelic}) plus "
        f"biallelic_palindromic_loci ({biallelic})"
    )

    # Derive the documented counts from the bundled audit rather than hard-coding
    # them.  A regenerated audit with different blocker counts must redden this
    # guard instead of leaving cancer.md asserting stale numbers.
    required_phrases = {
        "source-verified breast-cancer prs77",
        "not scored or reported",
        f"{multiallelic} multiallelic primary loci",
        f"{biallelic} additional biallelic loci",
        "multiallelic",
        "palindromic",
        "current sample schema",
        "does not preserve enough allele context",
        "harmonize those loci safely",
        "not an ancestry-calibration result",
    }
    missing = sorted(phrase for phrase in required_phrases if phrase not in note_text)
    assert not missing, (
        "cancer.md must explain the disabled breast PRS77 and distinguish its "
        f"runtime block from ancestry withholding; missing phrases: {missing}"
    )

    assert "[2]" in note_text, "breast PRS note must link to its scientific reference"
    reference_text = _reference_entry(doc_text, 2)
    assert _PROVENANCE_ID_RE.search(reference_text), (
        "breast PRS reference must include a PMID:, DOI:, ChEMBL:, or NCT: identifier"
    )
    # Bind it to the model the panel actually derives from. A generic identifier
    # check would still pass if reference [2] were swapped for any other approved
    # identifier already in the packet -- the Ensembl DOI, say -- even though it
    # would no longer identify the withheld breast model at all.
    source_pmid = breast["source_pmid"]
    # Compare the complete set of PMIDs the reference carries. A prefix match
    # would accept an invalid extension such as PMID:258557070 propagated
    # consistently through the documentation and the packet.
    documented_pmids = {match.group(1) for match in re.finditer(r"PMID:([0-9]+)", reference_text)}
    assert documented_pmids == {str(source_pmid)}, (
        f"breast PRS reference [2] cites PMIDs {sorted(documented_pmids)}, but the "
        f"panel's source_pmid is {source_pmid}"
    )
    # Parse the complete link target and compare it exactly. Excluding one
    # character class at a time from a lookahead never terminates: `-beta` was
    # followed by `+other`, and the next suffix would be something else again.
    model_doi = str(breast["source_url"]).rsplit("doi.org/", 1)[-1]
    linked_dois = set(re.findall(r"\]\((https://doi\.org/[^)]+)\)", reference_text))
    assert linked_dois == {f"https://doi.org/{model_doi}"}, (
        f"breast PRS reference [2] links {sorted(linked_dois)}, but the model's DOI is "
        f"https://doi.org/{model_doi}"
    )
    _assert_real_access_date(reference_text, "breast PRS reference [2]")

    assert "[3]" in note_text, "breast PRS note must link to the current allele audit"
    audit_reference = _reference_entry(doc_text, 3)
    assert "model_provenance.current_allele_audit" in audit_reference, (
        "breast PRS allele counts must cite their bundled audit provenance"
    )
    assert "DOI:10.1093/database/bay119" in audit_reference, (
        "breast PRS allele audit must cite the Ensembl Variation resource"
    )
    # Tie the documented panel version to the bundled panel. Regenerating the
    # panel with a new version but unchanged traits and counts would otherwise
    # leave this reference presenting a stale version as the current audit.
    panel_version = json.loads(_PANEL_PATH.read_text(encoding="utf-8"))["version"]
    documented_version = re.search(r"panel version (?P<version>\S+?)[,;)\s]", audit_reference)
    assert documented_version, (
        f"breast PRS allele-audit reference must name a panel version; "
        f"reference reads: {audit_reference}"
    )
    assert documented_version.group("version") == str(panel_version), (
        f"breast PRS allele-audit reference documents panel version "
        f"{documented_version.group('version')!r}, but the bundled panel is "
        f"{panel_version!r}"
    )
    _assert_real_access_date(audit_reference, "breast PRS allele-audit reference [3]")
    # The "immutable source" link is the reader's route to the audited panel, so
    # it must actually name that panel. Otherwise a regenerated audit can satisfy
    # the counts and the version while the link still resolves to a stale blob or
    # an unrelated file entirely.
    panel_rel = _PANEL_PATH.relative_to(REPO_ROOT).as_posix()
    immutable = re.search(
        r"https://github\.com/\S*?/blob/(?P<sha>[0-9a-f]{40})/(?P<path>\S+?)\)",
        audit_reference,
    )
    assert immutable, (
        "breast PRS allele-audit reference [3] must link an immutable blob of the panel; "
        f"reference reads: {audit_reference}"
    )
    assert immutable.group("path") == panel_rel, (
        f"reference [3]'s immutable link points at {immutable.group('path')}, not the "
        f"bundled panel {panel_rel}"
    )
    # The link's *content* is deliberately not verified here. Doing so requires
    # reading the linked commit, and CI checks out at depth 1
    # (`actions/checkout` with no `fetch-depth`), so that object is absent in the
    # only environment that gates merges. A check that silently skips exactly
    # where it would matter is worse than no check: it reads as coverage while
    # providing none. Verifying the blob would need either a network fetch from a
    # test or a CI fetch-depth change; both belong in their own change, not in a
    # documentation fix. The path assertion above holds in every checkout.


def test_breast_prs_references_carry_a_science_evidence_packet() -> None:
    """Every identifier the note cites must appear in the stored packet (#2028).

    The repository requires the queries, source IDs and access dates behind a
    cited identifier to live under ``data/science-evidence/``. Binding the packet
    to the documentation here means a reference cannot be added to, or changed
    in, ``cancer.md`` without its provenance following it.
    """
    packet = json.loads((_EVIDENCE_DIR / "queries.json").read_text(encoding="utf-8"))

    services = {str(entry["service"]) for entry in packet["queries"]}
    # The narrowest specialist is part of the ladder, not an optional extra: the
    # packet's README describes all three tiers, so dropping one would leave the
    # documentation describing evidence the packet no longer records.
    missing_tiers = sorted({"Consensus", "Scite", "PubMed connector"} - services)
    assert not missing_tiers, (
        "the packet must record the Consensus and Scite first tier and the narrowest "
        f"specialist, even when a service was unavailable; missing {missing_tiers} "
        f"(recorded {sorted(services)})"
    )

    # Every entry and every retained payload must carry the packet's access date;
    # checking only the top-level field lets an individual record drift.
    for entry in packet["queries"]:
        assert entry.get("accessed") == packet["accessed"], (
            f"the {entry['service']} entry records access date {entry.get('accessed')!r}, "
            f"not the packet's {packet['accessed']!r}"
        )
        raw = entry.get("raw_payload")
        if not raw:
            continue
        payload = json.loads((REPO_ROOT / raw).read_text(encoding="utf-8"))
        assert payload.get("accessed") == packet["accessed"], (
            f"{raw} records access date {payload.get('accessed')!r}, not the packet's "
            f"{packet['accessed']!r}"
        )

    # A recorded digest is a claim; check it rather than let it sit unverified.
    for entry in packet["queries"]:
        artifact = entry.get("repository_artifact")
        if not artifact:
            continue
        assert not Path(artifact).is_absolute(), f"artifact path must be relative: {artifact}"
        path = (REPO_ROOT / artifact).resolve()
        assert path.is_relative_to(REPO_ROOT.resolve()), f"artifact escapes the repo: {artifact}"
        assert path.is_file(), f"missing referenced artifact: {artifact}"
        # Compare against the panel's pin as well as the file: a self-declared
        # digest matching a repointed artifact would otherwise pass.
        panel_pins = _breast_weight_set()["model_provenance"]["reproducibility"][
            "checked_in_snapshot_sha256"
        ]
        pinned = panel_pins.get(Path(artifact).name)
        assert pinned, (
            f"{artifact} is not pinned by the panel's checked_in_snapshot_sha256; "
            f"the panel pins {sorted(panel_pins)}"
        )
        assert pinned == entry.get("artifact_sha256"), (
            f"{artifact} digest {entry.get('artifact_sha256')} disagrees with the "
            f"panel's pin {pinned}"
        )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry.get("artifact_sha256"), (
            f"{artifact} digest is {actual}, but the packet records {entry.get('artifact_sha256')}"
        )

    # The packet's correction/retraction screening is the one claim here whose
    # failure mode is scientific rather than bookkeeping, so it is checked against
    # the retained payload rather than taken on the packet's word. Cutting the
    # larger validator dropped this; it is restored deliberately.
    screening = json.loads(
        (_EVIDENCE_DIR / "raw" / "scite-doi-lookup-2026-08-03.json").read_text(encoding="utf-8")
    )
    screened = {str(r.get("doi", "")).upper() for r in screening.get("records", [])}
    cited_dois = {
        match.group(0).split(":", 1)[1].upper()
        for citation in packet["citations"]
        for match in _PROVENANCE_ID_RE.finditer(str(citation))
        if match.group(0).upper().startswith("DOI:")
    }
    unscreened = sorted(cited_dois - screened)
    assert not unscreened, (
        f"the packet cites {unscreened} but the screening payload holds no record for "
        "them, so their retraction status is unverified"
    )
    for record in screening["records"]:
        notices = record.get("editorial_notices")
        assert notices is not None, (
            f"the screened record for {record.get('doi')} has no editorial_notices field, so "
            "the packet's zero-notice claim is unverifiable"
        )
        assert notices == [], (
            f"the screened record for {record.get('doi')} carries editorial notices "
            f"{notices!r}; the screening must be re-done and the documentation updated "
            "before this source can be cited"
        )

    doc_text = _DOC_PATH.read_text(encoding="utf-8")
    note_text = _breast_prs_note(doc_text)
    referenced = sorted({int(number) for number in re.findall(r"\[([1-9][0-9]*)\]", note_text)})
    assert referenced, "the breast PRS note must cite at least one numbered reference"

    recorded = {
        match.group(0).upper()
        for citation in packet["citations"]
        for match in _PROVENANCE_ID_RE.finditer(str(citation))
    }
    for number in referenced:
        text = _reference_entry(doc_text, number)
        documented_date = _assert_real_access_date(text, f"breast PRS reference [{number}]")
        assert documented_date == packet["accessed"], (
            f"reference [{number}] records access date {documented_date}, but the packet "
            f"records {packet['accessed']}"
        )
        cited = {match.group(0).upper() for match in _PROVENANCE_ID_RE.finditer(text)}
        assert cited, f"breast PRS reference [{number}] carries no approved identifier"
        missing = sorted(cited - recorded)
        assert not missing, (
            f"reference [{number}] cites {missing}, which the evidence packet does not "
            f"record; packet citations are {sorted(recorded)}"
        )
