"""Offline regression coverage for tracked science-evidence references (#2481)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_science_evidence_paths import EVIDENCE_ROOT, check_references

ROOT = Path(__file__).resolve().parents[2]
PACKET = EVIDENCE_ROOT + "2026-01-02-synthetic-guard"
SCRIPT = ROOT / "scripts/check_science_evidence_paths.py"


def _stage(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


def test_repository_evidence_references_are_tracked() -> None:
    count, failures = check_references(ROOT)
    assert count > 0, "the real repository scan must not be vacuous"
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("suffix", ["", "/raw/missing.json"])
def test_missing_packet_or_file_is_rejected_even_with_untracked_copy(
    repo: Path, suffix: str
) -> None:
    target = PACKET + suffix
    _stage(repo, "fixture.json", json.dumps({"raw_evidence": target}))
    if suffix:
        _stage(repo, PACKET + "/README.md", "Synthetic packet with no payload.\n")
        (repo / target).parent.mkdir(parents=True)
        (repo / target).write_text("{}")
    else:
        (repo / target).mkdir(parents=True)
        (repo / target / "README.md").write_text("Untracked packet.\n")
    count, failures = check_references(repo)
    assert count == 1
    assert failures == [f"fixture.json:1: untracked evidence {target}"]


def test_tracked_file_and_directory_references_pass_with_prose_punctuation(repo: Path) -> None:
    _stage(repo, PACKET + "/raw/payload.json", "{}")
    _stage(
        repo,
        "docs/reference.md",
        f"Packet {PACKET}/.\nPacket {PACKET}.\n[Raw]({PACKET}/raw/payload.json)\n",
    )
    assert check_references(repo) == (3, [])


def test_directory_prefix_sibling_cannot_satisfy_reference(repo: Path) -> None:
    _stage(repo, PACKET + "-other/README.md", "Synthetic sibling.\n")
    _stage(repo, "reference.txt", PACKET)
    assert check_references(repo) == (1, [f"reference.txt:1: untracked evidence {PACKET}"])


def test_local_edits_cannot_hide_an_indexed_broken_reference(repo: Path) -> None:
    _stage(repo, "reference.txt", PACKET)
    (repo / "reference.txt").write_text("Locally removed but not staged.\n")
    _stage(repo, PACKET + "/README.md", "Synthetic packet.\n")
    subprocess.run(
        ["git", "rm", "--cached", PACKET + "/README.md"], cwd=repo, check=True, capture_output=True
    )
    assert (repo / PACKET / "README.md").is_file()
    assert check_references(repo) == (1, [f"reference.txt:1: untracked evidence {PACKET}"])


def test_examples_are_exempt_only_for_exact_source_and_target(repo: Path) -> None:
    example = EVIDENCE_ROOT + "2026-07-17-dpyd-c557ag"
    source = ".github/ISSUE_TEMPLATE/scientific_validity.yml"
    _stage(repo, source, f"placeholder: {example}/.\nactual: {example}/missing.json\n")
    _stage(repo, "real-fixture.json", json.dumps({"source": example}))
    count, failures = check_references(repo)
    assert count == 2
    assert failures == [
        f"{source}:2: untracked evidence {example}/missing.json",
        f"real-fixture.json:1: untracked evidence {example}",
    ]


def test_only_declared_synthetic_ignore_probes_are_exempt(repo: Path) -> None:
    example = EVIDENCE_ROOT + "2026-01-01-slug/"
    source = "tests/backend/test_agent_artifact_paths_ignored.py"
    _stage(repo, source, f"{example}README.md\n{example}raw/payload.json\n{example}new.json\n")
    _stage(repo, "real-fixture.json", json.dumps({"source": example + "README.md"}))
    count, failures = check_references(repo)
    assert count == 2
    assert failures == [
        f"real-fixture.json:1: untracked evidence {example}README.md",
        f"{source}:3: untracked evidence {example}new.json",
    ]


def test_data_payloads_and_undated_templates_are_outside_literal_reference_scope(
    repo: Path,
) -> None:
    _stage(repo, "data/science-evidence/README.md", PACKET + "/not-a-reference.json")
    _stage(repo, "example.md", EVIDENCE_ROOT + "YYYY-MM-DD-concern/raw.json")
    assert check_references(repo) == (0, [])


def test_traversal_cannot_resolve_as_a_packet(repo: Path) -> None:
    _stage(repo, PACKET + "/README.md", "Synthetic packet.\n")
    _stage(repo, "reference.txt", PACKET + "/../")
    count, failures = check_references(repo)
    assert count == 1 and len(failures) == 1
    assert "reference.txt:1: untracked evidence" in failures[0]


def test_cli_returns_failure_for_missing_payload_then_success_after_staging(repo: Path) -> None:
    _stage(repo, "reference.txt", PACKET + "/raw.json")
    failed = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=repo, capture_output=True, text=True
    )
    assert failed.returncode == 1 and PACKET + "/raw.json" in failed.stderr
    _stage(repo, PACKET + "/raw.json", "{}")
    passed = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=repo, capture_output=True, text=True
    )
    assert passed.returncode == 0
    assert "Checked 1 dated evidence references" in passed.stdout


def test_cli_fails_closed_outside_a_git_repository(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "Evidence-path validation failed" in result.stderr


@pytest.mark.parametrize(
    ("filename", "original_date", "packet"),
    [
        ("clinvar_seed_snapshot.json", "2026-07-16", "2026-07-16-clinvar-seed-1968"),
        (
            "panel_gene_coordinates_grch37.json",
            "2026-06-27",
            "2026-06-27-issue-1098-rsid-gene-guard",
        ),
    ],
)
def test_unrecoverable_historical_evidence_is_explicit(
    filename: str, original_date: str, packet: str
) -> None:
    provenance = json.loads((ROOT / "tests/fixtures" / filename).read_text())["_provenance"]
    assert provenance["accessed"] == original_date
    audit = provenance["historical_evidence"]
    assert audit["status"] == "unavailable"
    assert audit["original_packet"] == packet
    assert audit["audited"] == "2026-11-09"
    assert "not independently verified" in audit["limitation"]
    assert provenance.get("raw_evidence_dir") is None


def test_ci_checks_references_even_when_backend_path_filter_does_not_match() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    lint = workflow["jobs"]["lint"]
    assert "if" not in lint
    steps = [
        step
        for step in lint["steps"]
        if step.get("run") == "python scripts/check_science_evidence_paths.py"
    ]
    assert len(steps) == 1
    assert "if" not in steps[0]
    assert not steps[0].get("continue-on-error", False)
