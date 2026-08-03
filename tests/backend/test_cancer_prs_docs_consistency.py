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
    assert f"PMID:{source_pmid}" in reference_text, (
        f"breast PRS reference [2] must cite the panel's source_pmid ({source_pmid}); "
        f"reference reads: {reference_text}"
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
    assert re.search(rf"version {re.escape(str(panel_version))}(?![0-9.])", audit_reference), (
        f"breast PRS allele-audit reference must cite panel version {panel_version}; "
        f"reference reads: {audit_reference}"
    )
    _assert_real_access_date(audit_reference, "breast PRS allele-audit reference [3]")


def test_breast_prs_references_carry_a_science_evidence_packet() -> None:
    """Every identifier cited by the breast PRS note must be in the stored packet.

    The repository requires the queries, source IDs, access dates and raw payload
    paths behind a cited identifier to live under ``data/science-evidence/``.
    Binding the packet to the documentation here means a reference cannot be
    added to, or changed in, ``cancer.md`` without its provenance following it.
    """
    queries_path = _EVIDENCE_DIR / "queries.json"
    assert queries_path.is_file(), (
        f"the breast PRS references need their science-evidence packet at {queries_path}"
    )
    packet = json.loads(queries_path.read_text(encoding="utf-8"))

    services = {str(entry["service"]) for entry in packet["queries"]}
    assert {"Consensus", "Scite"} <= services, (
        "the packet must record the Consensus and Scite first tier, even when a "
        f"service was unavailable; recorded services were {sorted(services)}"
    )
    for entry in packet["queries"]:
        assert entry.get("status"), f"packet entry for {entry['service']} must record a status"
        if entry["status"] != "success":
            # An unavailable or quota-blocked tier still has to record why, or a
            # skipped service is indistinguishable from one that was never run.
            assert str(entry.get("reason", "")).strip(), (
                f"{entry['service']} recorded status {entry['status']!r} without a reason"
            )
            continue

        # An entry may either retain its own sanitized payload under raw/, or
        # reference a canonical repository artifact by pinned digest. The second
        # form is preferred when the artifact already drives the build: a copy
        # can drift from what is actually consumed, a verified digest cannot.
        artifact = entry.get("repository_artifact")
        if artifact:
            # Same containment rule as raw_payload below: an absolute path would
            # discard the REPO_ROOT prefix and `..` could escape the checkout, so
            # a digest-pinned file outside the repository would otherwise be
            # accepted as canonical repository evidence.
            assert not Path(artifact).is_absolute(), (
                f"repository_artifact must be repository-relative: {artifact}"
            )
            artifact_path = (REPO_ROOT / artifact).resolve()
            assert artifact_path.is_relative_to(REPO_ROOT.resolve()), (
                f"repository_artifact must stay inside the repository: {artifact}"
            )
            assert artifact_path.is_file(), f"missing referenced artifact: {artifact}"
            expected = entry.get("artifact_sha256")
            assert expected, f"{artifact} must be pinned by an artifact_sha256"
            actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            assert actual == expected, (
                f"{artifact} digest drifted: packet records {expected}, file is {actual}"
            )
            continue

        # A lookup may legitimately retain nothing -- a negative result from a
        # live publisher page, say, where a scrape would add third-party content
        # the packet does not need. It must then say so explicitly and give the
        # reason, so "nothing retained" is a recorded decision and not an
        # omission that reads as evidence.
        withheld = entry.get("no_payload_retained")
        if withheld:
            assert isinstance(withheld, str) and withheld.strip(), (
                f"{entry['service']} must explain why no payload is retained"
            )
            continue

        raw = entry.get("raw_payload")
        assert raw, (
            f"successful {entry['service']} call must record a raw payload path, "
            "a digest-pinned repository_artifact, or an explained no_payload_retained"
        )
        # The recorded path must name evidence inside this packet. Without this,
        # an absolute path would discard the REPO_ROOT prefix and any unrelated
        # repository file would satisfy the guard, so the packet could claim a
        # sanitized payload its raw/ directory does not hold.
        assert not Path(raw).is_absolute(), f"raw payload path must be relative: {raw}"
        resolved = (REPO_ROOT / raw).resolve()
        assert resolved.is_relative_to(_EVIDENCE_DIR.resolve()), (
            f"raw payload must live inside {_EVIDENCE_DIR.name}: {raw}"
        )
        assert resolved.is_file(), f"missing recorded raw payload: {raw}"
        # Existence is not usefulness: a truncated, emptied or malformed payload
        # would otherwise let the packet report complete evidence over unusable
        # files. Nothing else in the repository parses these.
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        assert payload.get("accessed") == packet["accessed"], (
            f"{raw} must record the packet's access date {packet['accessed']}"
        )
        assert payload.get("status") == "success", (
            f"{raw} must record the successful outcome it is cited for"
        )
        # Bind the payload to the entry that cites it. The bibliographic payloads
        # share an access date, a success status and a records list, so swapping
        # two raw_payload paths would otherwise leave both entries satisfied
        # while each pointed at the wrong service's evidence.
        assert payload.get("service") == entry["service"], (
            f"{raw} records service {payload.get('service')!r} but is cited by the "
            f"{entry['service']!r} entry"
        )
        # Bind the retained records to what the entry actually requested.
        # Matching the service name alone would still accept another PMID's or
        # DOI's record sitting under the right service label.
        params = entry.get("params") or {}
        wanted = [
            str(value).upper()
            for source in (entry.get("dois") or [], params.get("pmids") or [])
            for value in source
        ]
        if wanted:
            blob = json.dumps(payload).upper()
            missing_ids = sorted(w for w in wanted if w not in blob)
            assert not missing_ids, (
                f"{raw} does not contain the identifiers its entry requested: {missing_ids}"
            )
        # A payload that declares how many results came back must retain that
        # many, or the "complete" ranking is a truncation wearing its name.
        returned = payload.get("results_returned")
        if returned is not None:
            ranking = payload.get("complete_result_ranking") or []
            assert len(ranking) == returned, (
                f"{raw} declares results_returned={returned} but retains "
                f"{len(ranking)} entries in complete_result_ranking"
            )
        # Validate *every* recognized collection, not the first one in insertion
        # order. Picking one would let the Consensus payload satisfy the guard
        # from its three-entry `results_retained` subset while the 20-result
        # `complete_result_ranking` it claims to retain was emptied.
        collections = {
            key: value
            for key, value in payload.items()
            if key in {"records", "results_retained", "complete_result_ranking"}
        }
        assert collections, f"{raw} must retain a record collection, not just metadata"
        for key, value in collections.items():
            assert isinstance(value, list) and value, (
                f"{raw} declares {key} but it is empty or not a list"
            )

    # Versions/builds, licences and retention basis are required packet content,
    # so guard them too. Without this, deleting `source_versions_and_licenses`
    # or blanking its entries leaves the packet passing while recording none of
    # the provenance an auditor needs.
    provenance = packet["source_versions_and_licenses"]
    entries = {key: value for key, value in provenance.items() if not key.startswith("_")}
    assert entries, "the packet must record versions and licences for its sources and services"

    # Tie the block's keys to the packet's own citations and queried services.
    # Checking only global non-emptiness would let the Ensembl source record or
    # the Scite service record be deleted while any one entry survived, so the
    # per-source metadata could vanish undetected.
    recorded_ids = {
        match.group(0).upper() for key in entries for match in _PROVENANCE_ID_RE.finditer(key)
    }
    for citation in packet["citations"]:
        for match in _PROVENANCE_ID_RE.finditer(str(citation)):
            assert match.group(0).upper() in recorded_ids, (
                f"{match.group(0)} is cited by the packet but has no entry in "
                "source_versions_and_licenses"
            )
    for entry in packet["queries"]:
        service = str(entry["service"])
        assert any(service.split(" (")[0] in key for key in entries), (
            f"the {service} query has no version/licence entry in "
            f"source_versions_and_licenses; entries are {sorted(entries)}"
        )

    for name, record in entries.items():
        for field in ("version_or_build", "license_or_terms", "retention_basis"):
            value = record.get(field)
            assert isinstance(value, str) and value.strip(), (
                f"{name} must record a nonempty {field}"
            )
        # A field that does not exist has to say so, and say what follows from
        # that, rather than being quietly filled with a plausible-looking value.
        for missing in record.get("unavailable_fields", []):
            assert "(" in missing and ")" in missing, (
                f"{name} lists unavailable field {missing!r} without the parenthesised "
                "reason it is unavailable"
            )

    doc_text = _DOC_PATH.read_text(encoding="utf-8")
    cited = {
        match.group(0).upper()
        for number in (2, 3)
        for match in _PROVENANCE_ID_RE.finditer(_reference_entry(doc_text, number))
    }
    assert cited, "the breast PRS references must cite at least one approved identifier"

    # Tokenise both sides with the same identifier grammar and compare as sets.
    # A substring test would accept a truncated identifier -- "PMID:2585570" is a
    # substring of "PMID:25855707" -- and so would leave a broken reference
    # undetected, which is exactly the drift this guard exists to catch.
    # Each citation must carry its own access date, not merely an identifier.
    # Extracting identifiers alone would let an entry silently lose or change
    # its `(accessed YYYY-MM-DD)` suffix while the packet still looked complete.
    for citation in packet["citations"]:
        _assert_real_access_date(str(citation), f"packet citation {citation!r}")
        assert f"(accessed {packet['accessed']})" in str(citation), (
            f"packet citation must use the packet's access date {packet['accessed']}: {citation}"
        )
    recorded = {
        match.group(0).upper()
        for citation in packet["citations"]
        for match in _PROVENANCE_ID_RE.finditer(str(citation))
    }
    assert recorded, "the evidence packet must record at least one approved identifier"
    missing = sorted(cited - recorded)
    assert not missing, (
        "every identifier cited by the breast PRS references must appear verbatim in "
        f"the evidence packet's citations; missing: {missing} (packet records {sorted(recorded)})"
    )

    # The two files must also agree on *when* the sources were checked. Asserting
    # only that some access date is present lets the documentation and the packet
    # tell different stories about the same lookup.
    packet_date = packet["accessed"]
    doc_dates = {
        match.group(0).lower()
        for number in (2, 3)
        for match in _ACCESS_DATE_RE.finditer(_reference_entry(doc_text, number))
    }
    assert doc_dates == {f"(accessed {packet_date})"}, (
        "the breast PRS references must carry the same access date the evidence "
        f"packet records ({packet_date}); documentation has {sorted(doc_dates)}"
    )
