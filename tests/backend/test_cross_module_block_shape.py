"""A malformed ``cross_module`` block is treated as absent, not crashed on (#2350).

The Skin, Allergy and Gene Health generators guarded a block against ``None``
and then read ``block["module"]`` and ``block["note"]``, so ``{}`` or a missing
key raised ``KeyError`` mid-scoring, while Traits built a card pointing at an
empty module. Every panel loader now passes its blocks through
``cross_module_block``, so downstream code only ever sees a usable block or
``None``, and the malformed one is named in a warning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from backend.analysis import allergy, gene_health, skin, sleep, traits
from backend.analysis.cross_module_links import cross_module_block
from backend.db.tables import raw_variants, reference_metadata, sample_metadata_obj

_PANELS = Path(__file__).resolve().parents[2] / "backend" / "data" / "panels"

_LOADERS = {
    "allergy": allergy.load_allergy_panel,
    "gene_health": gene_health.load_gene_health_panel,
    "skin": skin.load_skin_panel,
    "sleep": sleep.load_sleep_panel,
    "traits": traits.load_traits_panel,
}

_MALFORMED = [
    {},
    {"module": "nutrigenomics"},
    {"note": "See Nutrigenomics."},
    {"module": "", "note": "See Nutrigenomics."},
    {"module": "nutrigenomics", "note": "   "},
    {"module": 3, "note": "See Nutrigenomics."},
    "nutrigenomics",
    ["nutrigenomics"],
]


def _malformed_warnings(logs: list[dict]) -> list[tuple[str, str]]:
    return [
        (log["panel"], log["rsid"])
        for log in logs
        if log.get("event") == "cross_module_block_malformed"
    ]


@pytest.mark.parametrize("block", _MALFORMED, ids=repr)
def test_a_malformed_block_is_absent_and_named(block: object) -> None:
    with capture_logs() as logs:
        assert cross_module_block(block, panel="skin", rsid="rs1") is None
    assert _malformed_warnings(logs) == [("skin", "rs1")]


def test_a_well_formed_block_is_kept_as_is() -> None:
    block = {"module": "nutrigenomics", "note": "See Nutrigenomics.", "target_genes": ["VDR"]}
    with capture_logs() as logs:
        assert cross_module_block(block, panel="skin", rsid="rs1") is block
    assert _malformed_warnings(logs) == []


def test_no_block_is_simply_absent() -> None:
    with capture_logs() as logs:
        assert cross_module_block(None, panel="skin", rsid="rs1") is None
    assert _malformed_warnings(logs) == []


def _panel_with_block(module: str, tmp_path: Path, rsid: str | None, block: object) -> Path:
    raw = json.loads((_PANELS / f"{module}_panel.json").read_text(encoding="utf-8"))
    snps = [snp for pathway in raw["pathways"] for snp in pathway["snps"]]
    target = next(snp for snp in snps if snp["rsid"] == rsid) if rsid else snps[0]
    target["cross_module"] = block
    path = tmp_path / f"{module}_panel.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize("module", sorted(_LOADERS))
def test_every_loader_drops_a_malformed_block(module: str, tmp_path: Path) -> None:
    path = _panel_with_block(module, tmp_path, None, {})
    rsid = next(
        snp["rsid"]
        for pathway in json.loads(path.read_text(encoding="utf-8"))["pathways"]
        for snp in pathway["snps"]
    )
    with capture_logs() as logs:
        panel = _LOADERS[module](path)
    loaded = next(snp for pathway in panel.pathways for snp in pathway.snps if snp.rsid == rsid)
    assert loaded.cross_module is None
    assert (module, rsid) in _malformed_warnings(logs)


def _score_vdr_het(panel: skin.SkinPanel, tmp_path: Path) -> skin.SkinResult:
    sample = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
    sample_metadata_obj.create_all(sample)
    reference = sa.create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    reference_metadata.create_all(reference)
    with sample.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [{"rsid": "rs2228570", "chrom": "12", "pos": 48272895, "genotype": "GA"}],
        )
    return skin.score_skin_pathways(panel, sample, reference)


def test_skin_scores_a_carrier_at_a_malformed_block(tmp_path: Path) -> None:
    """The issue's reproduction: ``{}`` on VDR FokI and a non-Standard call."""
    panel = skin.load_skin_panel(_panel_with_block("skin", tmp_path, "rs2228570", {}))
    result = _score_vdr_het(panel, tmp_path)
    called = next(
        snp for pr in result.pathway_results for snp in pr.called_snps if snp.rsid == "rs2228570"
    )
    assert called.category == skin.MODERATE
    assert "rs2228570" not in {card.rsid for card in result.cross_module_findings}


def test_the_same_carrier_gets_its_card_from_the_shipped_panel(tmp_path: Path) -> None:
    """Positive control: the card above is missing because of the block alone."""
    result = _score_vdr_het(skin.load_skin_panel(), tmp_path)
    assert "rs2228570" in {card.rsid for card in result.cross_module_findings}
