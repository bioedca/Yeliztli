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
import subprocess
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

# Tiers whose payloads carry the retained records; none may claim the
# no-payload exemption. Scite is the service that performs the packet's
# correction/retraction screening, so its payload is checked unconditionally
# rather than on a marker the payload supplies about itself.
_EVIDENCE_TIER_SERVICES = frozenset({"Consensus", "Scite", "PubMed connector"})
_SCREENING_SERVICE = "Scite"


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


def _scalar_values(node: object) -> set[str]:
    """Every scalar in a nested structure, as upper-case strings.

    Identifier checks compare against this set rather than substring-matching
    serialized JSON, so ``25855707`` is not satisfied by ``258557070``.
    """
    if isinstance(node, dict):
        return {value for child in node.values() for value in _scalar_values(child)}
    if isinstance(node, list):
        return {value for child in node for value in _scalar_values(child)}
    if isinstance(node, bool) or node is None:
        return set()
    return {str(node).upper()}


def _assert_iso_date(value: object, where: str) -> None:
    """Require a bare ``YYYY-MM-DD`` field to be a real calendar date."""
    assert isinstance(value, str) and value, f"{where} must record an access date"
    try:
        date.fromisoformat(value)
    except ValueError:  # pragma: no cover - only reached on a malformed date
        raise AssertionError(f"{where} records an impossible access date: {value}") from None


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
    # Naming the right path is not enough: after the audit is regenerated the URL
    # can still resolve to a blob carrying the *old* counts. Read the linked blob
    # and require its audit to be the one the documentation reports.
    #
    # CI checks out at depth 1 (`actions/checkout` with no `fetch-depth`), so the
    # linked commit is normally absent there. Requiring it would redden every CI
    # run, so probe first and compare only when the object is present: a full
    # checkout still catches a stale link, and CI never fails for lacking history
    # it was never given.
    if (
        subprocess.run(
            ["git", "cat-file", "-e", f"{immutable.group('sha')}^{{commit}}"],
            capture_output=True,
            cwd=REPO_ROOT,
        ).returncode
        != 0
    ):
        return
    linked = subprocess.run(
        ["git", "show", f"{immutable.group('sha')}:{panel_rel}"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert linked.returncode == 0, (
        f"reference [3] links commit {immutable.group('sha')[:10]}, which this checkout "
        "has but cannot read the panel from"
    )
    linked_panel = json.loads(linked.stdout)
    linked_breast = next(w for w in linked_panel["weight_sets"] if w["trait"] == "breast_cancer")
    linked_audit = linked_breast["model_provenance"]["current_allele_audit"]
    assert linked_audit == audit, (
        "reference [3]'s immutable link resolves to a panel whose allele audit differs "
        "from the bundled one; the link is stale and must be re-pointed at the "
        "regenerated panel"
    )
    assert linked_panel["version"] == panel_version, (
        f"reference [3]'s immutable link resolves to panel version "
        f"{linked_panel['version']!r}, not the bundled {panel_version!r}"
    )


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
    missing_tiers = sorted(_EVIDENCE_TIER_SERVICES - services)
    assert not missing_tiers, (
        "the packet must record the first tier and the narrowest specialist, even "
        f"when a service was unavailable; missing {missing_tiers} (recorded "
        f"{sorted(services)})"
    )
    for entry in packet["queries"]:
        assert entry.get("status"), f"packet entry for {entry['service']} must record a status"
        # The entry's own access date must be real and agree with the packet's,
        # or an entry can silently claim a different retrieval boundary.
        _assert_iso_date(entry.get("accessed"), f"the {entry['service']} query entry")
        assert entry["accessed"] == packet["accessed"], (
            f"the {entry['service']} query entry records access date "
            f"{entry['accessed']!r}, not the packet's {packet['accessed']!r}"
        )
        if entry["status"] != "success":
            # An unavailable or quota-blocked tier still has to record why, or a
            # skipped service is indistinguishable from one that was never run.
            assert str(entry.get("reason", "")).strip(), (
                f"{entry['service']} recorded status {entry['status']!r} without a reason"
            )
            # A recorded outage also has to say what was done about it, or the
            # packet documents a gap without documenting its disposition.
            assert str(entry.get("fallback", "")).strip(), (
                f"{entry['service']} recorded status {entry['status']!r} without a "
                "fallback disposition"
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
            # Validating only the entry's own digest would accept any repository
            # file whose digest was pasted alongside it. The panel is the
            # authority for what the build actually consumes, so the referenced
            # artifact must appear in its reproducibility record with the same
            # digest.
            # Resolve the pins from the breast model by trait. The loader treats
            # weight-set order as insignificant, so indexing [0] would silently
            # read another model's provenance after a valid panel reorder.
            panel_pins = _breast_weight_set()["model_provenance"]["reproducibility"][
                "checked_in_snapshot_sha256"
            ]
            pinned = panel_pins.get(Path(artifact).name)
            assert pinned, (
                f"{artifact} is not pinned by the panel's checked_in_snapshot_sha256; "
                f"the panel pins {sorted(panel_pins)}"
            )
            assert pinned == expected, (
                f"{artifact} digest {expected} disagrees with the panel's pin {pinned}"
            )
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
            # The exemption exists for incidental lookups such as a publisher
            # page. It must never cover a bibliographic tier, whose payload is
            # what carries the records and the retraction screening -- otherwise
            # a single added string disables every check below.
            assert entry["service"] not in _EVIDENCE_TIER_SERVICES, (
                f"{entry['service']} is a bibliographic tier and must retain its "
                "payload; no_payload_retained cannot exempt it"
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
            # Search the returned records only. Both payloads echo the requested
            # identifiers in their top-level `params`, so a whole-document search
            # is satisfied even when every returned record has been replaced.
            returned_records = [
                value
                for key, value in payload.items()
                if key in {"records", "results_retained", "complete_result_ranking"}
            ]
            present = _scalar_values(returned_records)
            # A PMID alone does not pin the record: it can keep the requested PMID
            # while losing or changing the DOI the documentation cites beside it.
            if params.get("pmids"):
                doi_ids = {
                    m.group(0).split(":", 1)[1].upper()
                    for citation in packet["citations"]
                    for m in _PROVENANCE_ID_RE.finditer(str(citation))
                    if m.group(0).upper().startswith("DOI:")
                    and str(params["pmids"][0]) in str(citation)
                }
                wanted = list(wanted) + sorted(doi_ids)
            missing_ids = sorted(w for w in wanted if w not in present)
            assert not missing_ids, (
                f"{raw} returns no record for the identifiers its entry requested: {missing_ids}"
            )
        # An entry that identifies its evidence by search rather than by
        # identifier must still be bound to it. The Consensus entry has no DOIs
        # or PMIDs, so without this a successful response from a *different*
        # search would satisfy every other check and its ranking would be
        # attributed to a query that never produced it.
        for field in ("query", "filters"):
            if field not in entry:
                continue
            # One-sided: whenever the entry declares a search field, the payload
            # must carry it. A two-sided check would skip the comparison when the
            # payload simply drops the field, re-admitting a response that cannot
            # be attributed to the recorded search.
            assert field in payload, (
                f"{raw} is missing {field}, which its entry declares as "
                f"{entry[field]!r}; the stored results cannot be attributed to the "
                "recorded search"
            )
            assert entry[field] == payload[field], (
                f"{raw} records {field}={payload[field]!r} but its entry recorded "
                f"{entry[field]!r}; the stored results cannot be attributed to the "
                "recorded search"
            )
        # The packet's prose claims a specific paper at rank 1. Bind it to the
        # retained ranking, or a refreshed ranking could put a different paper
        # there while the entry still described the old one.
        ranking = payload.get("complete_result_ranking")
        retained = payload.get("results_retained")
        if ranking and retained:
            top = next((r for r in ranking if r.get("rank") == 1), None)
            claimed = next((r for r in retained if r.get("rank") == 1), None)
            if top and claimed:
                assert top.get("title") == claimed.get("title"), (
                    f"{raw} annotates rank 1 as {claimed.get('title')!r} but the retained "
                    f"ranking has {top.get('title')!r} there"
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

        # The packet states, as its correction/retraction screening, that both
        # sources carry zero editorial notices. Bind that claim to the data: a
        # payload refreshed with a real retraction -- or one that simply drops
        # the field -- must fail here rather than leave the README and
        # queries.json asserting a clean screening that no longer holds. This is
        # the one guard whose failure mode is a scientific claim, not a metadata
        # inconsistency.
        if entry["service"] == _SCREENING_SERVICE:
            assert payload.get("correction_retraction_screening"), (
                f"{raw} is the packet's correction/retraction screening and must "
                "record its outcome"
            )
            assert payload.get("records"), f"{raw} must retain the screened records"
            for record in payload.get("records", []):
                notices = record.get("editorial_notices")
                assert notices is not None, (
                    f"{raw} claims a correction/retraction screening but its record for "
                    f"{record.get('doi') or record.get('identifiers')} has no "
                    "editorial_notices field"
                )
                assert notices == [], (
                    f"{raw} records editorial notices {notices!r} for "
                    f"{record.get('doi') or record.get('identifiers')}, but the packet "
                    "claims zero notices; the screening must be re-done and the "
                    "documentation updated before this can be cited"
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

    # The packet cites the panel's own license_basis as the governing record for
    # the model source, so the two must agree. Requiring only nonempty text would
    # let the packet contradict the very field it defers to.
    breast = _breast_weight_set()
    panel_license = str(breast["license_basis"])
    model_entry = next(
        (record for key, record in entries.items() if str(breast["source_pmid"]) in key),
        None,
    )
    assert model_entry is not None, (
        f"the packet must carry a licence entry for the model source PMID {breast['source_pmid']}"
    )
    panel_terms = panel_license.rsplit(",", 1)[-1].strip()
    assert panel_terms and panel_terms in str(model_entry["license_or_terms"]), (
        f"the packet records licence {model_entry['license_or_terms']!r} for the model "
        f"source, which does not carry the panel's license_basis terms {panel_terms!r}"
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
    # Derive the reference set from the note itself. A hard-coded (2, 3) would
    # silently ignore a future [4], letting a new source enter the documentation
    # with no packet entry, no identifier coverage and no access date.
    note_text = _breast_prs_note(doc_text)
    referenced = sorted({int(number) for number in re.findall(r"\[([1-9][0-9]*)\]", note_text)})
    assert referenced, "the breast PRS note must cite at least one numbered reference"
    reference_texts = {number: _reference_entry(doc_text, number) for number in referenced}
    cited = {
        match.group(0).upper()
        for text in reference_texts.values()
        for match in _PROVENANCE_ID_RE.finditer(text)
    }
    assert cited, "the breast PRS references must cite at least one approved identifier"
    for number, text in reference_texts.items():
        assert _PROVENANCE_ID_RE.search(text), (
            f"breast PRS reference [{number}] carries no PMID:, DOI:, ChEMBL: or NCT: "
            "identifier; a global check would let it ride on the other references"
        )

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

    # The README's claim-mapping table is what tells an auditor which reference
    # rests on which source. Loading only queries.json left it unguarded, so the
    # table could be deleted, or a reference mapped to the wrong source, while
    # the packet still looked complete.
    readme = (_EVIDENCE_DIR / "README.md").read_text(encoding="utf-8")

    # The README quotes Scite's citation tallies. Bind them to the retained
    # payload, or an edited number -- or a refreshed payload -- leaves the
    # documentation asserting counts the evidence does not carry.
    scite_payload = json.loads(
        (_EVIDENCE_DIR / "raw" / "scite-doi-lookup-2026-08-03.json").read_text(encoding="utf-8")
    )
    for record in scite_payload["records"]:
        tally = record["citation_tally"]
        quoted = re.search(
            rf"{re.escape(record['doi'])} — (?P<total>[0-9,]+) total, "
            rf"(?P<supporting>[0-9,]+) supporting, (?P<contrasting>[0-9,]+) contrasting",
            readme,
        )
        if quoted:
            for field in ("total", "supporting", "contrasting"):
                assert int(quoted.group(field).replace(",", "")) == tally[field], (
                    f"the README quotes {field}={quoted.group(field)} for {record['doi']}, "
                    f"but the retained payload records {tally[field]}"
                )
    mapping_rows = [
        line for line in readme.splitlines() if line.startswith("|") and "reference `[" in line
    ]
    assert len(mapping_rows) == len(reference_texts), (
        f"the packet's claim mapping must cover all {len(reference_texts)} references the "
        f"note cites; it has {len(mapping_rows)} rows"
    )
    for number, text in reference_texts.items():
        row = next((r for r in mapping_rows if f"reference `[{number}]`" in r), None)
        assert row, f"the packet's claim mapping has no row for reference [{number}]"
        row_ids = {m.group(0).upper() for m in _PROVENANCE_ID_RE.finditer(row)}
        ref_ids = {m.group(0).upper() for m in _PROVENANCE_ID_RE.finditer(text)}
        # The row may legitimately be richer than the reference (the reference
        # spells its DOI as a link URL), but it must account for every identifier
        # the reference carries, so a row cannot be remapped to another source.
        assert ref_ids <= row_ids, (
            f"the claim-mapping row for reference [{number}] cites {sorted(row_ids)}, "
            f"which does not account for the reference's identifiers {sorted(ref_ids)}"
        )

    # The two files must also agree on *when* the sources were checked. Asserting
    # only that some access date is present lets the documentation and the packet
    # tell different stories about the same lookup.
    packet_date = packet["accessed"]
    for number, text in reference_texts.items():
        _assert_real_access_date(text, f"breast PRS reference [{number}]")
    doc_dates = {
        match.group(0).lower()
        for text in reference_texts.values()
        for match in _ACCESS_DATE_RE.finditer(text)
    }
    assert doc_dates == {f"(accessed {packet_date})"}, (
        "the breast PRS references must carry the same access date the evidence "
        f"packet records ({packet_date}); documentation has {sorted(doc_dates)}"
    )
