"""Test-fixture seeds never feed production code (#2360).

The seeds under ``tests/fixtures/seed_csvs/`` carry real gene, disease and
variant names, but several of their values are behavior projections rather than
verified claims. ``gene_phenotype_seed.csv``'s inheritance patterns, for one, were
never independently checked. That is acceptable only because nothing a user sees
is built from them: production reads the MONDO/HPO, OMIM and other downloaded
sources. This keeps it that way.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_NAMES = ("seed_csvs", "mini_reference.db", "gene_phenotype_seed")


def _references(package: Path) -> list[str]:
    hits = []
    for path in sorted(package.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        hits.extend(
            f"{path.relative_to(package)}: {name}" for name in _FIXTURE_NAMES if name in text
        )
    return hits


def test_production_code_never_reads_a_test_seed() -> None:
    backend = _ROOT / "backend"
    assert any(backend.rglob("*.py")), "found no backend modules; is the scan broken?"
    assert _references(backend) == []


def test_the_scan_sees_a_reference(tmp_path: Path) -> None:
    (tmp_path / "loader.py").write_text(
        'SEED = "tests/fixtures/seed_csvs/gene_phenotype_seed.csv"\n', encoding="utf-8"
    )
    assert _references(tmp_path) == ["loader.py: seed_csvs", "loader.py: gene_phenotype_seed"]
