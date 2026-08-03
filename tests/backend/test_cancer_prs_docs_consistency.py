"""Docs↔cancer-PRS status consistency guard (#2028).

The cancer module page used to advertise breast PRS as computed after the
canonical PRS77 model had become fail-closed at runtime.  Keep the page's active
trait list tied to the weight-set gates, and require a distinct explanation for
the disabled breast model so readers do not mistake it for ordinary ancestry
calibration withholding.
"""

from __future__ import annotations

import json
import re
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
_ACCESS_DATE_RE = re.compile(r"\(accessed [0-9]{4}-[0-9]{2}-[0-9]{2}\)", re.IGNORECASE)


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


def _breast_allele_audit() -> dict[str, object]:
    breast = next(
        weight_set for weight_set in _weight_sets() if weight_set["trait"] == "breast_cancer"
    )
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
    breast = next(
        weight_set for weight_set in _weight_sets() if weight_set["trait"] == "breast_cancer"
    )
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
    assert _ACCESS_DATE_RE.search(reference_text), (
        "breast PRS reference must include an '(accessed YYYY-MM-DD)' date"
    )

    assert "[3]" in note_text, "breast PRS note must link to the current allele audit"
    audit_reference = _reference_entry(doc_text, 3)
    assert "model_provenance.current_allele_audit" in audit_reference, (
        "breast PRS allele counts must cite their bundled audit provenance"
    )
    assert "DOI:10.1093/database/bay119" in audit_reference, (
        "breast PRS allele audit must cite the Ensembl Variation resource"
    )
    assert _ACCESS_DATE_RE.search(audit_reference), (
        "breast PRS allele-audit reference must include an '(accessed YYYY-MM-DD)' date"
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
    assert {"Consensus", "Scite"} <= services, (
        "the packet must record the Consensus and Scite first tier, even when a "
        f"service was unavailable; recorded services were {sorted(services)}"
    )
    for entry in packet["queries"]:
        assert entry.get("status"), f"packet entry for {entry['service']} must record a status"
        if entry["status"] == "success":
            raw = entry.get("raw_payload")
            assert raw, f"successful {entry['service']} call must record a raw payload path"
            assert (REPO_ROOT / raw).is_file(), f"missing recorded raw payload: {raw}"

    doc_text = _DOC_PATH.read_text(encoding="utf-8")
    cited = {
        match.group(0).upper()
        for number in (2, 3)
        for match in _PROVENANCE_ID_RE.finditer(_reference_entry(doc_text, number))
    }
    assert cited, "the breast PRS references must cite at least one approved identifier"
    recorded = " ".join(str(item) for item in packet["citations"]).upper()
    missing = sorted(identifier for identifier in cited if identifier not in recorded)
    assert not missing, (
        "every identifier cited by the breast PRS references must appear in the "
        f"evidence packet's citations; missing: {missing}"
    )
