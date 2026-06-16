"""Panel rsID validity guard (#645).

A systematic check found two panel rsIDs that are stale or invalid in dbSNP,
with no CI guard to catch them:

  - rs28940299 (cancer VHL ``expected_clinvar_rsids``) — dbSNP merged it into
    rs5030806, which is *itself* merged into rs104893829 (the current live
    record). Verified: dbSNP refsnp v2 ``merged_into`` chain + Ensembl GRCh37.
  - rs6269442 (traits cognitive_ability PRS weight) — no dbSNP refsnp record and
    absent from Ensembl GRCh37/38: not a valid rsID at all.

This guard is the fast, offline, per-PR regression lock: every panel rsID is
well-formed (``rs`` + digits), and none is a known-retired/invalid ID. The
broader systemic check — resolving *every* panel rsID against dbSNP/Ensembl to
catch a newly-retired but well-formed ID — is tracked separately (it needs a
network-backed nightly verifier or a committed resolution snapshot, with the
documented carve-outs for haplogroup Y-SNP naming and ClinVar pathogenic indels
absent from Ensembl-GRCh37).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PANELS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"

# rsIDs dbSNP has retired (merged) or that are not valid dbSNP records at all,
# verified against dbSNP refsnp v2 + Ensembl GRCh37 (#645). They must never
# reappear in any panel; use the current live ID instead.
_RETIRED_OR_INVALID_RSIDS = {
    "rs28940299": "dbSNP-merged into rs5030806 → rs104893829; use the current rs104893829",
    "rs5030806": "dbSNP-merged into rs104893829; use the current rs104893829",
    "rs6269442": "no dbSNP refsnp record / absent from Ensembl GRCh37 — invalid rsID",
}

_RS_FIELDS = ("rsid",)  # dict keys whose string value is a single rsID
_RS_LIST_FIELDS = ("expected_clinvar_rsids",)  # dict keys whose value is a list of rsIDs
_RSID_RE = re.compile(r"^rs\d+$")


def _collect_rsids(obj: object) -> list[str]:
    """Recursively gather every rsID from the rsID-bearing fields of a panel.

    Targets only ``rsid`` (string) and ``expected_clinvar_rsids`` (list) so we
    never sweep up incidental ``rs``-containing prose from descriptions/notes.
    """
    found: list[str] = []
    if isinstance(obj, dict):
        for field in _RS_FIELDS:
            v = obj.get(field)
            if isinstance(v, str):
                found.append(v)
        for field in _RS_LIST_FIELDS:
            v = obj.get(field)
            if isinstance(v, list):
                found.extend(x for x in v if isinstance(x, str))
        for v in obj.values():
            found.extend(_collect_rsids(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_collect_rsids(v))
    return found


def _panel_files() -> list[Path]:
    files = sorted(PANELS_DIR.glob("*.json"))
    assert files, f"no panel JSONs found under {PANELS_DIR}"
    return files


def _panel_rsids() -> dict[str, list[str]]:
    """Map each panel filename to the rsIDs in its rsID-bearing fields."""
    out: dict[str, list[str]] = {}
    for path in _panel_files():
        out[path.name] = _collect_rsids(json.loads(path.read_text(encoding="utf-8")))
    return out


class TestPanelRsidValidity:
    def test_no_retired_or_invalid_rsids(self) -> None:
        """No panel rsID field may contain a dbSNP-retired or invalid rsID (#645)."""
        offenders: list[str] = []
        for fname, rsids in _panel_rsids().items():
            for rsid in rsids:
                if rsid in _RETIRED_OR_INVALID_RSIDS:
                    offenders.append(f"{fname}: {rsid} — {_RETIRED_OR_INVALID_RSIDS[rsid]}")
        assert not offenders, (
            "retired/invalid panel rsIDs (use the current dbSNP ID):\n" + "\n".join(offenders)
        )

    def test_rs_prefixed_ids_are_well_formed(self) -> None:
        """Any ``rs``-prefixed token in an rsID-bearing field must be ``rs`` +
        digits — catches typos / malformed dbSNP IDs.

        Carve-out: the haplogroup bundle's mt/Y markers use intentional
        non-dbSNP naming (synthetic ``i<pos>`` probe IDs, Y-SNP names); those
        do not start with ``rs`` and so are not subject to the dbSNP-rsID format
        rule here (their validity is a separate concern — see #645's note on the
        Y-SNP carve-out)."""
        malformed: list[str] = []
        for fname, rsids in _panel_rsids().items():
            for rsid in rsids:
                if rsid.startswith("rs") and not _RSID_RE.match(rsid):
                    malformed.append(f"{fname}: {rsid!r}")
        assert not malformed, "malformed dbSNP rsIDs (expected rs<digits>):\n" + "\n".join(
            malformed
        )

    @pytest.mark.parametrize("retired", sorted(_RETIRED_OR_INVALID_RSIDS))
    def test_denylist_entries_are_documented(self, retired: str) -> None:
        """Each denylisted rsID carries a provenance reason (keeps the guard auditable)."""
        assert _RETIRED_OR_INVALID_RSIDS[retired].strip()
