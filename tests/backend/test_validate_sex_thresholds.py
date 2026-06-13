"""Tests for ``scripts/validate_sex_thresholds.py`` (Step 52; Plan §9.4).

Runs the validation script against the committed synthetic fixtures under
``tests/fixtures/sex_inference_synthetic/`` (XX, XY, manual_review) and
asserts both the reported aggregate rates and the Plan §9.4 classification.

The script is local-only in production (the bio-validator runs it against a
private real export to attest thresholds — Step 53). CI runs it only against
the synthetic fixtures, which are hand-fabricated and never contain real
genotype rows.

Coverage:

- Programmatic ``build_report`` shape + classification across the three
  fixtures.
- ``classify()`` helper unit cases pinning the Plan §9.4 algorithm branches
  directly (XX evidence at/below the chrY noise floor, discordant X/Y
  manual_review, candidate-XY → confirmed/manual_review/unknown via chrY
  rate, fallback to ``unknown`` when chrX is uninformative).
- PAR pre-filter actually drops PAR1/PAR2 rows (asserted via the XX +
  XY fixtures, both of which carry chr-25 PAR1 hets that would flip the
  classification if leaked through).
- CLI surface: text and ``--json`` output, exit codes for missing file +
  bad threshold input, ``--xy-threshold`` / ``--par-noise`` overrides that
  re-classify the manual_review fixture into XY (lower confirm threshold)
  or unknown (raise PAR-noise above the fixture's chrY rate).
- Dispatcher passthrough: a 23andMe header fed to the script parses cleanly
  and runs through the same algorithm (no AncestryDNA-only branches).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "validate_sex_thresholds.py"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "sex_inference_synthetic"

# Ensure the script module is importable for the in-process unit cases.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Production §9.4 classifier + thresholds — the script above hand-duplicates these
# (it imports neither), so the parity tests below cross-check the two copies so a
# one-sided recalibration can't silently drift (#500).
from backend.services import sex_inference as _prod  # noqa: E402 — sys.path tweak above
from scripts.validate_sex_thresholds import (  # noqa: E402 — sys.path tweak above
    DEFAULT_MIN_X_NONPAR_TYPED,
    DEFAULT_MIN_Y_PROBES,
    DEFAULT_PAR_NOISE,
    DEFAULT_XY_CONFIRM,
    build_report,
    classify,
)

# ---------------------------------------------------------------------------
# Programmatic build_report() over the committed synthetic fixtures
# ---------------------------------------------------------------------------


def test_xx_fixture_classifies_as_xx_with_unopposed_x_het() -> None:
    report = build_report(FIXTURE_DIR / "xx_sample.txt")

    assert report.vendor == "ancestrydna"
    assert report.version == "v2.0"
    assert report.classification == "XX"

    # Non-PAR chrX tabulation: 60 het + 60 hom + 1 no-call (evaluable: ≥100 typed).
    assert report.x_nonpar_het == 60
    assert report.x_nonpar_hom == 60
    assert report.x_nonpar_nocall == 1
    assert report.x_nonpar_typed == 120
    assert report.x_nonpar_het_rate == pytest.approx(0.5)

    # Two PAR1 het rows (chr 25) must be pre-filtered out of the typed pool.
    assert report.x_par_count == 2
    assert report.x_total == report.x_par_count + report.x_nonpar_typed + report.x_nonpar_nocall

    # chrY rate is 0 over an evaluable denominator (≥50 probes); the X-het signal
    # is unopposed and yields XX.
    assert report.y_total == 60
    assert report.y_typed == 0
    assert report.y_rate == pytest.approx(0.0)


def test_xy_fixture_classifies_as_xy_with_chry_confirmation() -> None:
    report = build_report(FIXTURE_DIR / "xy_sample.txt")

    assert report.classification == "XY"
    assert report.x_nonpar_het == 0
    assert report.x_nonpar_hom == 120
    assert report.x_nonpar_typed == 120

    # One chr 25 PAR1 het exists and must be filtered (otherwise classification
    # would flip to XX dispositively).
    assert report.x_par_count == 1

    # 48 typed chrY calls out of 60 → 0.80 > 0.30 confirm threshold.
    assert report.y_total == 60
    assert report.y_typed == 48
    assert report.y_rate == pytest.approx(0.8)


def test_manual_review_fixture_classifies_as_manual_review() -> None:
    report = build_report(FIXTURE_DIR / "manual_review_sample.txt")

    assert report.classification == "manual_review"
    assert report.x_nonpar_het == 0
    assert report.x_nonpar_hom == 120
    assert report.x_nonpar_typed == 120

    # 12 typed chrY calls out of 60 → 0.20 (in the (0.10, 0.30] band).
    assert report.y_total == 60
    assert report.y_typed == 12
    assert report.y_rate == pytest.approx(0.2)


def test_manual_review_thresholds_round_trip_defaults() -> None:
    report = build_report(FIXTURE_DIR / "manual_review_sample.txt")
    assert report.xy_confirm_threshold == DEFAULT_XY_CONFIRM
    assert report.par_noise_threshold == DEFAULT_PAR_NOISE


# ---------------------------------------------------------------------------
# classify() unit cases — pin the Plan §9.4 branches directly
# ---------------------------------------------------------------------------


# Branch cases run at evaluable densities (≥100 typed non-PAR chrX, ≥50 chrY) so
# the issue-363 minimum-evidence gate passes and the §9.4 tree is exercised.
@pytest.mark.parametrize(
    "params, expected",
    [
        # Non-PAR chrX het supports XX when chrY is at/below the noise floor.
        (dict(x_nonpar_het=60, x_nonpar_typed=120, x_nonpar_hom=60, y_total=60, y_rate=0.0), "XX"),
        (
            dict(x_nonpar_het=60, x_nonpar_typed=120, x_nonpar_hom=60, y_total=60, y_rate=0.10),
            "XX",
        ),
        # Non-PAR chrX het + chrY above the noise floor is discordant.
        (
            dict(x_nonpar_het=1, x_nonpar_typed=120, x_nonpar_hom=119, y_total=60, y_rate=0.11),
            "manual_review",
        ),
        (
            dict(x_nonpar_het=1, x_nonpar_typed=120, x_nonpar_hom=119, y_total=60, y_rate=0.9),
            "manual_review",
        ),
        # Candidate XY — chrY rate above XY-confirm → XY.
        (
            dict(x_nonpar_het=0, x_nonpar_typed=120, x_nonpar_hom=120, y_total=60, y_rate=0.31),
            "XY",
        ),
        # Candidate XY — chrY rate in (PAR-noise, XY-confirm] → manual_review.
        (
            dict(x_nonpar_het=0, x_nonpar_typed=120, x_nonpar_hom=120, y_total=60, y_rate=0.30),
            "manual_review",
        ),
        (
            dict(x_nonpar_het=0, x_nonpar_typed=120, x_nonpar_hom=120, y_total=60, y_rate=0.11),
            "manual_review",
        ),
        # Candidate XY — chrY rate at/below PAR-noise → unknown (don't auto-assign).
        (
            dict(x_nonpar_het=0, x_nonpar_typed=120, x_nonpar_hom=120, y_total=60, y_rate=0.10),
            "unknown",
        ),
        (
            dict(x_nonpar_het=0, x_nonpar_typed=120, x_nonpar_hom=120, y_total=60, y_rate=0.00),
            "unknown",
        ),
        # Zero typed non-PAR chrX → unknown regardless of chrY rate.
        (
            dict(x_nonpar_het=0, x_nonpar_typed=0, x_nonpar_hom=0, y_total=60, y_rate=0.99),
            "unknown",
        ),
        # issue #363 minimum-evidence gate — too few non-PAR chrX probes → unknown
        # even with an otherwise-XX het signal and full chrY denominator.
        (
            dict(x_nonpar_het=1, x_nonpar_typed=1, x_nonpar_hom=0, y_total=60, y_rate=0.0),
            "unknown",
        ),
        (
            dict(x_nonpar_het=50, x_nonpar_typed=99, x_nonpar_hom=49, y_total=60, y_rate=0.0),
            "unknown",
        ),
        # issue #363 — evaluable chrX but no/thin chrY denominator → unknown
        # (a vacuous y_rate==0.0 from zero probes is not "chrY absent").
        (
            dict(x_nonpar_het=60, x_nonpar_typed=120, x_nonpar_hom=60, y_total=0, y_rate=0.0),
            "unknown",
        ),
        (
            dict(x_nonpar_het=0, x_nonpar_typed=120, x_nonpar_hom=120, y_total=49, y_rate=0.9),
            "unknown",
        ),
    ],
)
def test_classify_branches(params: dict, expected: str) -> None:
    assert (
        classify(
            xy_confirm=DEFAULT_XY_CONFIRM,
            par_noise=DEFAULT_PAR_NOISE,
            **params,
        )
        == expected
    )


# ---------------------------------------------------------------------------
# Parity with the PRODUCTION §9.4 classifier (#500)
#
# scripts/validate_sex_thresholds.py hand-duplicates production's _classify() and
# its four threshold constants instead of importing them. Each copy is tested only
# against itself, so a one-sided recalibration (e.g. bumping production
# MIN_Y_PROBES 50->60 while the script's DEFAULT_MIN_Y_PROBES stays 50) would leave
# both suites green while the attestation in docs/internal/sex_inference_threshold_validation.md
# certifies a threshold production no longer uses. These tests cross-check the two.
# ---------------------------------------------------------------------------


def test_threshold_constants_match_production() -> None:
    """Every script DEFAULT_* must equal the production constant it duplicates."""
    assert DEFAULT_XY_CONFIRM == _prod._THRESHOLD_XY_CONFIRM
    assert DEFAULT_PAR_NOISE == _prod._THRESHOLD_PAR_NOISE
    assert DEFAULT_MIN_X_NONPAR_TYPED == _prod.MIN_X_NONPAR_TYPED
    assert DEFAULT_MIN_Y_PROBES == _prod.MIN_Y_PROBES


def test_classifier_parity_grid() -> None:
    """The script's classify() and production's _classify() must agree on every
    grid point spanning all four §9.4 thresholds.

    The script's copy is invoked with the script's OWN default thresholds while
    production's _classify reads its own module constants, so this catches BOTH a
    logic divergence AND a one-sided constant drift (a sample near a drifted floor
    classifies differently between the two copies).
    """
    import itertools

    # Boundary-spanning values: x_nonpar_typed straddles MIN_X (100), y_total
    # straddles MIN_Y (50), y_rate straddles PAR_NOISE (0.10) and XY_CONFIRM (0.30).
    xs_typed = [0, 99, 100, 150]
    ys_total = [0, 49, 50, 100]
    hets = [0, 1, 3]
    y_rates = [0.0, 0.10, 0.101, 0.30, 0.301, 0.6]

    mismatches: list[str] = []
    n = 0
    for x_typed, y_tot, het, y_rate in itertools.product(xs_typed, ys_total, hets, y_rates):
        het_eff = min(het, x_typed)
        point = dict(
            x_nonpar_het=het_eff,
            x_nonpar_typed=x_typed,
            x_nonpar_hom=x_typed - het_eff,
            y_total=y_tot,
            y_rate=y_rate,
        )
        prod_call = _prod._classify(**point)
        script_call = classify(
            xy_confirm=DEFAULT_XY_CONFIRM,
            par_noise=DEFAULT_PAR_NOISE,
            min_x_nonpar_typed=DEFAULT_MIN_X_NONPAR_TYPED,
            min_y_probes=DEFAULT_MIN_Y_PROBES,
            **point,
        )
        n += 1
        if prod_call != script_call:
            mismatches.append(f"{point}: prod={prod_call} script={script_call}")

    assert n == 288  # full grid actually exercised (guards against a no-op shrink)
    assert not mismatches, (
        "production _classify and the validation script's classify() DIVERGED — the "
        f"§9.4 copies have drifted ({len(mismatches)}/{n} points):\n" + "\n".join(mismatches[:8])
    )


# ---------------------------------------------------------------------------
# CLI surface (subprocess) — text + JSON output, exit codes, threshold flags
# ---------------------------------------------------------------------------


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_text_output_includes_classification_and_rates() -> None:
    result = _run([str(FIXTURE_DIR / "xy_sample.txt")])
    assert result.returncode == 0
    out = result.stdout
    assert "classification            : XY" in out
    assert "non-no-call rate        : 0.800" in out
    assert "non-PAR het rate        : 0.000" in out


def test_cli_json_output_round_trips_through_build_report() -> None:
    result = _run([str(FIXTURE_DIR / "manual_review_sample.txt"), "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "manual_review"
    assert payload["vendor"] == "ancestrydna"
    assert payload["x_nonpar_typed"] == 120
    assert payload["y_rate"] == pytest.approx(0.2)
    # The evidence floors used are recorded in the attestation-grade report.
    assert payload["min_x_nonpar_typed"] == 100
    assert payload["min_y_probes"] == 50


def test_cli_lower_xy_threshold_promotes_manual_review_to_xy() -> None:
    result = _run(
        [
            str(FIXTURE_DIR / "manual_review_sample.txt"),
            "--xy-threshold",
            "0.15",
            "--par-noise",
            "0.05",
            "--json",
        ]
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "XY"
    assert payload["xy_confirm_threshold"] == pytest.approx(0.15)
    assert payload["par_noise_threshold"] == pytest.approx(0.05)


def test_cli_higher_par_noise_demotes_manual_review_to_unknown() -> None:
    result = _run(
        [
            str(FIXTURE_DIR / "manual_review_sample.txt"),
            "--par-noise",
            "0.25",
            "--xy-threshold",
            "0.30",
            "--json",
        ]
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "unknown"


def test_cli_raising_min_x_demotes_confident_call_to_unknown() -> None:
    """issue #363 — re-calibrating the chrX evidence floor above the fixture's
    typed count demotes a confident XY to ``unknown``."""
    result = _run(
        [
            str(FIXTURE_DIR / "xy_sample.txt"),
            "--min-x-nonpar-typed",
            "200",
            "--json",
        ]
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "unknown"
    assert payload["min_x_nonpar_typed"] == 200


def test_cli_raising_min_y_demotes_confident_call_to_unknown() -> None:
    """issue #363 — raising the chrY denominator floor above the fixture's
    probe count demotes a confident XX to ``unknown``."""
    result = _run(
        [
            str(FIXTURE_DIR / "xx_sample.txt"),
            "--min-y-probes",
            "100",
            "--json",
        ]
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "unknown"
    assert payload["min_y_probes"] == 100


def test_cli_rejects_negative_min_evidence() -> None:
    result = _run([str(FIXTURE_DIR / "xy_sample.txt"), "--min-y-probes", "-1"])
    assert result.returncode == 2
    assert "--min-y-probes must be >= 0" in result.stderr


def test_cli_missing_file_exits_nonzero(tmp_path: Path) -> None:
    result = _run([str(tmp_path / "nope.txt")])
    assert result.returncode == 2
    assert "file not found" in result.stderr


def test_cli_rejects_inverted_thresholds() -> None:
    result = _run(
        [
            str(FIXTURE_DIR / "xy_sample.txt"),
            "--par-noise",
            "0.5",
            "--xy-threshold",
            "0.3",
        ]
    )
    assert result.returncode == 2
    assert "--par-noise must be <= --xy-threshold" in result.stderr


def test_cli_rejects_threshold_out_of_range() -> None:
    result = _run(
        [
            str(FIXTURE_DIR / "xy_sample.txt"),
            "--xy-threshold",
            "1.5",
        ]
    )
    assert result.returncode == 2
    assert "--xy-threshold must be in" in result.stderr


def test_cli_parses_23andme_input_through_dispatcher(tmp_path: Path) -> None:
    """Smoke that the dispatcher passthrough covers 23andMe inputs too.

    The script is vendor-agnostic — Step 53's bio-validator may run it against
    a 23andMe export. We don't need a full 23andMe sex-inference fixture for
    Step 52; the existing committed v5 fixture exercises the dispatcher path.
    """
    sample = REPO_ROOT / "tests" / "fixtures" / "sample_23andme_v5.txt"
    if not sample.exists():
        pytest.skip("sample_23andme_v5.txt fixture not present")
    result = _run([str(sample), "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["vendor"] == "23andme"
    assert payload["classification"] in {"XX", "XY", "manual_review", "unknown"}
