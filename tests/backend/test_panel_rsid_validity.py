"""Panel rsID validity guard (#645).

A systematic check found two panel rsIDs that are stale or invalid in dbSNP,
with no CI guard to catch them:

  - rs28940299 (cancer VHL ``expected_clinvar_rsids``) — dbSNP merged it into
    rs5030806, which is *itself* merged into rs104893829 (the current live
    record). Verified: dbSNP refsnp v2 ``merged_into`` chain + Ensembl GRCh37.
  - rs6269442 (traits cognitive_ability PRS weight) — no dbSNP refsnp record and
    absent from Ensembl GRCh37/38: not a valid rsID at all.

This guard is the fast, offline, per-PR regression lock: every panel rsID is
well-formed (``rs`` + digits), none is a known-retired/invalid ID, and every
dbSNP-style rsID in coordinate-bearing panels has a committed Ensembl GRCh37
coordinate snapshot. Haplogroup tree-marker identity is audited separately
because those panels mix dbSNP rsIDs with synthetic probe and phylogenetic
marker names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PANELS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"
COORDINATE_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "panel_rsid_coordinates.json"
)

# rsIDs dbSNP has retired (merged) or that are not valid dbSNP records at all,
# verified against dbSNP refsnp v2 + Ensembl GRCh37 (#645). They must never
# reappear in any panel; use the current live ID instead.
_RETIRED_OR_INVALID_RSIDS = {
    "rs28940299": "dbSNP-merged into rs5030806 → rs104893829; use the current rs104893829",
    "rs5030806": "dbSNP-merged into rs104893829; use the current rs104893829",
    "rs6269442": "no dbSNP refsnp record / absent from Ensembl GRCh37 — invalid rsID",
    "rs267608087": "dbSNP-merged into rs748452299; use the current rs748452299",
}

_RS_FIELDS = ("rsid",)  # dict keys whose string value is a single rsID
_RS_LIST_FIELDS = ("expected_clinvar_rsids",)  # dict keys whose value is a list of rsIDs
_RSID_RE = re.compile(r"^rs\d+$")
_STANDARD_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}
_DISALLOWED_VARIANT_CLASSES = {
    "cnv",
    "copy_number_variation",
    "microsatellite",
    "tandem_repeat",
    "vntr",
}
_COORDINATE_EXCLUDED_PANEL_FILES = {
    "haplogroup_bundle.json": (
        "Haplogroup tree markers mix dbSNP rsIDs, synthetic array probe IDs, and "
        "phylogenetic Y/mt marker naming; tree-marker identity is audited separately "
        "(see issue #805)."
    )
}


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


def _well_formed_panel_rsids() -> set[str]:
    """All dbSNP-style panel rsIDs that should resolve in the coordinate fixture."""
    return {
        rsid
        for fname, rsids in _panel_rsids().items()
        if fname not in _COORDINATE_EXCLUDED_PANEL_FILES
        for rsid in rsids
        if _RSID_RE.match(rsid)
    }


def _coordinate_fixture() -> dict:
    return json.loads(COORDINATE_FIXTURE.read_text(encoding="utf-8"))


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

    def test_coordinate_fixture_shape_is_locked(self) -> None:
        """The offline rsID coordinate fixture has provenance and stable fields (#742)."""
        fixture = _coordinate_fixture()
        provenance = fixture.get("_provenance")
        assert isinstance(provenance, dict)
        assert provenance.get("source") == "Ensembl GRCh37 REST /variation/human/{rsid}"
        assert provenance.get("assembly") == "GRCh37"
        assert provenance.get("generator") == "scripts/build_panel_rsid_coordinates.py"
        assert provenance.get("excluded_panel_files") == _COORDINATE_EXCLUDED_PANEL_FILES
        assert isinstance(provenance.get("accessed"), str) and provenance["accessed"]

        coords = fixture.get("rsids")
        assert isinstance(coords, dict)
        assert len(coords) >= 100, "coordinate fixture coverage regressed"
        for rsid, rec in coords.items():
            assert _RSID_RE.match(rsid), f"fixture key is not a well-formed rsID: {rsid!r}"
            assert rec.get("assembly") == "GRCh37", rsid
            assert rec.get("chrom") in _STANDARD_CHROMS, rsid
            assert isinstance(rec.get("start"), int) and rec["start"] > 0, rsid
            assert isinstance(rec.get("end"), int) and rec["end"] > 0, rsid
            expected_location = (
                f"{rec['chrom']}:{rec['start']}"
                if rec["start"] == rec["end"]
                else f"{rec['chrom']}:{rec['start']}-{rec['end']}"
            )
            assert rec.get("location") == expected_location, rsid
            assert rec.get("strand") in (1, -1), rsid
            assert isinstance(rec.get("allele_string"), str) and rec["allele_string"], rsid
            assert isinstance(rec.get("variant_class"), str), rsid
            assert rec.get("source", "").endswith(f"/{rsid}"), rsid

    def test_every_panel_rsid_resolves_to_a_coordinate(self) -> None:
        """Every curated dbSNP-style panel rsID has an offline GRCh37 coordinate (#742)."""
        panel_rsids = _well_formed_panel_rsids()
        fixture_rsids = set(_coordinate_fixture()["rsids"])

        missing = sorted(panel_rsids - fixture_rsids, key=lambda rsid: int(rsid[2:]))
        stale = sorted(fixture_rsids - panel_rsids, key=lambda rsid: int(rsid[2:]))
        assert not missing, "panel rsIDs missing from coordinate fixture: " + ", ".join(missing)
        assert not stale, "coordinate fixture has rsIDs absent from panels: " + ", ".join(stale)

    def test_coordinate_fixture_excludes_repeat_or_structural_markers(self) -> None:
        """Panel rsIDs must resolve to array-typeable SNV/short-indel style records."""
        offenders = []
        for rsid, rec in _coordinate_fixture()["rsids"].items():
            variant_class = rec.get("variant_class", "").casefold()
            if variant_class in _DISALLOWED_VARIANT_CLASSES:
                offenders.append(f"{rsid}: {rec.get('variant_class')}")

        assert not offenders, (
            "panel rsIDs resolve to repeat/structural classes that are not ordinary "
            "array-typeable loci: " + ", ".join(offenders)
        )
