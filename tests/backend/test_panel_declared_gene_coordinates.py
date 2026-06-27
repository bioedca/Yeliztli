"""Categorical panel rsID-to-declared-gene coordinate guard (#1098).

The panel rsID coordinate fixture already locks every curated dbSNP-style panel
rsID to an offline Ensembl GRCh37 coordinate. This guard adds the cross-panel
gene side: rows that declare a gene must either have an rsID coordinate that
overlaps the declared gene's GRCh37 interval, or carry explicit row-level
allowlist metadata for proxy, tag-SNP, promoter/UTR, upstream/downstream, or
intergenic rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

PANELS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"
RSID_COORDINATE_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "panel_rsid_coordinates.json"
)
GENE_COORDINATE_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "panel_gene_coordinates_grch37.json"
)

_CONTEXT_FIELDS = ("id", "name", "pathway_name", "trait", "trait_name", "category", "title")
_COORDINATE_EXCLUDED_PANEL_FILES = {
    "haplogroup_bundle.json": (
        "Haplogroup tree markers mix dbSNP rsIDs, synthetic array probe IDs, and "
        "phylogenetic Y/mt marker naming; tree-marker identity is audited separately."
    )
}


@dataclass(frozen=True)
class PanelGeneRow:
    panel: str
    path: str
    rsid: str
    declared_gene: str
    variant_name: str | None
    context: str

    @property
    def key(self) -> str:
        return f"{self.panel}|{self.path}|{self.rsid}|{self.declared_gene}"


@cache
def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _panel_files() -> list[Path]:
    files = [
        path
        for path in sorted(PANELS_DIR.glob("*.json"))
        if path.name not in _COORDINATE_EXCLUDED_PANEL_FILES
    ]
    assert files, f"no panel JSONs found under {PANELS_DIR}"
    return files


def _context_label(context: dict[str, str]) -> str:
    parts: list[str] = []
    for field in _CONTEXT_FIELDS:
        value = context.get(field)
        if value and value not in parts:
            parts.append(value)
    return " / ".join(parts) or "<root>"


def _iter_gene_rows(
    obj: object, panel: str, context: dict[str, str], path: str
) -> list[PanelGeneRow]:
    rows: list[PanelGeneRow] = []
    if isinstance(obj, dict):
        next_context = dict(context)
        for field in _CONTEXT_FIELDS:
            value = obj.get(field)
            if isinstance(value, (str, int, float)):
                next_context[field] = str(value)

        rsid = obj.get("rsid")
        gene = obj.get("gene")
        if isinstance(rsid, str) and isinstance(gene, str):
            rsid = rsid.strip()
            gene = gene.strip()
            if rsid and gene:
                variant_name = obj.get("variant_name")
                rows.append(
                    PanelGeneRow(
                        panel=panel,
                        path=path,
                        rsid=rsid,
                        declared_gene=gene,
                        variant_name=variant_name if isinstance(variant_name, str) else None,
                        context=_context_label(next_context),
                    )
                )

        for key, value in obj.items():
            rows.extend(_iter_gene_rows(value, panel, next_context, f"{path}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            rows.extend(_iter_gene_rows(value, panel, context, f"{path}[{index}]"))
    return rows


@lru_cache(maxsize=1)
def _panel_gene_rows() -> tuple[PanelGeneRow, ...]:
    rows: list[PanelGeneRow] = []
    for path in _panel_files():
        rows.extend(_iter_gene_rows(_load_json(path), path.name, {}, "$"))
    return tuple(rows)


@lru_cache(maxsize=1)
def _gene_fixture() -> dict[str, Any]:
    return _load_json(GENE_COORDINATE_FIXTURE)


@lru_cache(maxsize=1)
def _rsid_coordinate_fixture() -> dict[str, Any]:
    return _load_json(RSID_COORDINATE_FIXTURE)


def _declared_gene_symbols(
    declared_gene: str, aliases: dict[str, str], composites: dict[str, list[str]]
) -> list[str]:
    if declared_gene in composites:
        return list(composites[declared_gene])

    symbols: list[str] = []
    for part in declared_gene.split("/"):
        part = part.strip()
        if not part:
            continue
        symbols.append(aliases.get(part, part))
    return symbols


def _overlaps(rsid_coord: dict[str, Any], gene_coord: dict[str, Any]) -> bool:
    return (
        str(rsid_coord["chrom"]) == str(gene_coord["chrom"])
        and int(rsid_coord["start"]) <= int(gene_coord["end"])
        and int(rsid_coord["end"]) >= int(gene_coord["start"])
    )


def _format_gene_loci(symbols: list[str], genes: dict[str, Any]) -> str:
    loci = []
    for symbol in symbols:
        rec = genes[symbol]
        loci.append(f"{symbol}={rec['chrom']}:{rec['start']}-{rec['end']}")
    return ", ".join(loci)


def _format_rsid_coord(rec: dict[str, Any]) -> str:
    return f"{rec['chrom']}:{rec['start']}" if rec["start"] == rec["end"] else rec["location"]


class TestPanelDeclaredGeneCoordinates:
    def test_gene_coordinate_fixture_shape_is_locked(self) -> None:
        """The offline gene-coordinate fixture is auditable and complete enough."""
        fixture = _gene_fixture()
        provenance = fixture.get("_provenance")
        assert isinstance(provenance, dict)
        assert provenance.get("source") == "Ensembl GRCh37 REST /lookup/symbol/homo_sapiens"
        assert provenance.get("assembly") == "GRCh37"
        assert provenance.get("generator", "").startswith("issue #1098")
        assert isinstance(provenance.get("accessed"), str) and provenance["accessed"]

        aliases = fixture.get("aliases")
        assert isinstance(aliases, dict)
        assert aliases.get("MCT1") == "SLC16A1"

        composites = fixture.get("composites")
        assert isinstance(composites, dict)
        assert composites.get("IL2/IL21") == ["IL2", "IL21"]
        for declared_gene, symbols in composites.items():
            assert isinstance(declared_gene, str) and declared_gene
            assert isinstance(symbols, list) and symbols, declared_gene
            assert all(isinstance(symbol, str) and symbol for symbol in symbols), declared_gene

        genes = fixture.get("genes")
        assert isinstance(genes, dict)
        assert len(genes) >= 100, "declared-gene fixture coverage regressed"
        for declared_gene, symbols in composites.items():
            for symbol in symbols:
                assert symbol in genes, f"{declared_gene} -> {symbol}"
        for symbol, rec in genes.items():
            assert rec.get("assembly") == "GRCh37", symbol
            assert isinstance(rec.get("chrom"), str) and rec["chrom"], symbol
            assert isinstance(rec.get("start"), int) and rec["start"] > 0, symbol
            assert isinstance(rec.get("end"), int) and rec["end"] >= rec["start"], symbol
            assert rec.get("strand") in (1, -1), symbol
            assert isinstance(rec.get("ensembl_id"), str) and rec["ensembl_id"], symbol
            assert rec.get("source", "").endswith(f"/{symbol}"), symbol

        allowed = fixture.get("allowed_non_overlaps")
        assert isinstance(allowed, dict)
        for key, rec in allowed.items():
            panel, row_path, rsid, declared_gene = key.split("|", 3)
            assert rec.get("panel") == panel, key
            assert rec.get("path") == row_path, key
            assert rec.get("rsid") == rsid, key
            assert rec.get("declared_gene") == declared_gene, key
            assert rec.get("kind") in {
                "proxy",
                "tag_snp",
                "promoter",
                "utr",
                "upstream",
                "downstream",
                "intergenic",
                "locus_marker",
            }, key
            assert isinstance(rec.get("reason"), str) and rec["reason"].strip(), key

    def test_every_declared_gene_has_a_coordinate_entry(self) -> None:
        """Every categorical panel declared gene resolves to the offline gene fixture."""
        fixture = _gene_fixture()
        genes = fixture["genes"]
        aliases = fixture["aliases"]
        composites = fixture["composites"]

        missing: list[str] = []
        for row in _panel_gene_rows():
            for symbol in _declared_gene_symbols(row.declared_gene, aliases, composites):
                if symbol not in genes:
                    missing.append(
                        f"{row.panel}: {row.context}: {row.rsid} declares "
                        f"{row.declared_gene!r} -> missing {symbol!r}"
                    )

        assert not missing, (
            "panel rows declare genes absent from panel_gene_coordinates_grch37.json:\n"
            + "\n".join(missing)
        )

    def test_declared_gene_overlaps_rsid_coordinate_or_is_allowed(self) -> None:
        """Rows cannot claim genes on the wrong locus without explicit metadata."""
        gene_fixture = _gene_fixture()
        genes = gene_fixture["genes"]
        aliases = gene_fixture["aliases"]
        composites = gene_fixture["composites"]
        allowed = gene_fixture["allowed_non_overlaps"]
        rsid_coords = _rsid_coordinate_fixture()["rsids"]

        offenders: list[str] = []
        for row in _panel_gene_rows():
            rsid_coord = rsid_coords[row.rsid]
            symbols = _declared_gene_symbols(row.declared_gene, aliases, composites)
            overlapping = [symbol for symbol in symbols if _overlaps(rsid_coord, genes[symbol])]
            if overlapping or row.key in allowed:
                continue

            offenders.append(
                f"{row.panel}: {row.path}: {row.context}: {row.rsid} "
                f"({_format_rsid_coord(rsid_coord)}) declares {row.declared_gene!r}"
                f"{f' / {row.variant_name}' if row.variant_name else ''}; expected overlap with "
                f"{_format_gene_loci(symbols, genes)} or explicit allowlist key {row.key!r}"
            )

        assert not offenders, (
            "panel rsID coordinates do not overlap their declared genes and lack an "
            "explicit proxy/intergenic/tag allowlist entry:\n" + "\n".join(offenders)
        )

    def test_non_overlap_allowlist_entries_are_current(self) -> None:
        """Each allowlist entry maps to a live non-overlapping row and cannot linger."""
        gene_fixture = _gene_fixture()
        genes = gene_fixture["genes"]
        aliases = gene_fixture["aliases"]
        composites = gene_fixture["composites"]
        allowed = gene_fixture["allowed_non_overlaps"]
        rsid_coords = _rsid_coordinate_fixture()["rsids"]
        rows = {row.key: row for row in _panel_gene_rows()}

        stale: list[str] = []
        now_overlapping: list[str] = []
        for key in allowed:
            row = rows.get(key)
            if row is None:
                stale.append(key)
                continue
            symbols = _declared_gene_symbols(row.declared_gene, aliases, composites)
            rsid_coord = rsid_coords[row.rsid]
            overlapping = [symbol for symbol in symbols if _overlaps(rsid_coord, genes[symbol])]
            if overlapping:
                now_overlapping.append(f"{key}: now overlaps {', '.join(overlapping)}")

        assert not stale, (
            "non-overlap allowlist entries no longer map to panel rows:\n" + "\n".join(stale)
        )
        assert not now_overlapping, (
            "non-overlap allowlist entries now overlap and should be removed:\n"
            + "\n".join(now_overlapping)
        )

    def test_historical_wrong_gene_rows_are_guarded(self) -> None:
        """The #1070/#1071 motivating rows are covered by the systemic guard."""
        gene_fixture = _gene_fixture()
        genes = gene_fixture["genes"]
        aliases = gene_fixture["aliases"]
        composites = gene_fixture["composites"]
        allowed = gene_fixture["allowed_non_overlaps"]
        rsid_coords = _rsid_coordinate_fixture()["rsids"]
        rows = {row.key: row for row in _panel_gene_rows()}

        intragenic_rows = [
            ("methylation_panel.json", "rs1677693", "DHFR"),
            ("traits_panel.json", "rs2576037", "KATNAL2"),
        ]
        for panel, rsid, declared_gene in intragenic_rows:
            matches = [
                row
                for row in rows.values()
                if row.panel == panel and row.rsid == rsid and row.declared_gene == declared_gene
            ]
            assert matches, f"{panel}|{rsid}|{declared_gene}"
            for row in matches:
                symbols = _declared_gene_symbols(row.declared_gene, aliases, composites)
                assert any(
                    _overlaps(rsid_coords[row.rsid], genes[symbol]) for symbol in symbols
                ), row.key

        rasa1_rows = [
            row
            for row in rows.values()
            if row.panel == "traits_panel.json"
            and row.rsid == "rs1477268"
            and row.declared_gene == "RASA1"
        ]
        assert rasa1_rows
        assert all(row.key in allowed for row in rasa1_rows)
