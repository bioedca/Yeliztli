"""Regression guard for the ``store_*_findings`` clear-on-empty bug class (#348).

Nine ``store_*_findings()`` functions previously ran ``if not rows: return 0``
*before* the ``sa.delete(findings)`` that clears the module's prior rows, inside
a separate ``with sample_engine.begin()`` block. So an empty rerun (a replaced
sample that yields no current reportable findings) left the previous run's
findings in place — a stale, user-facing data-integrity bug, the same class as
cancer #252 and the PRS stale paths (#149/#150/#244/#245).

The fix moves the delete ahead of the empty-rows early return, inside the
transaction, mirroring the verified reference pattern in ``prs.py``. SQLAlchemy
2.0 ``Engine.begin()`` commits the block on normal exit — including the early
``return`` — so the delete is persisted (context7-verified).

Each case seeds a stale finding, stores an *empty* result (no variants / no
pathway_results → ``rows == []``, hitting the early-return path), and asserts the
module's rows are cleared. Every case fails against the pre-fix ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.allergy import AllergyResult, store_allergy_findings
from backend.analysis.carrier_status import (
    CarrierAnalysisResult,
    store_carrier_findings,
)
from backend.analysis.fitness import FitnessResult, store_fitness_findings
from backend.analysis.gene_health import GeneHealthResult, store_gene_health_findings
from backend.analysis.methylation import MethylationResult, store_methylation_findings
from backend.analysis.nutrigenomics import (
    NutrigenomicsResult,
    store_nutrigenomics_findings,
)
from backend.analysis.rare_variant_finder import (
    RareVariantFinderResult,
    store_rare_variant_findings,
)
from backend.analysis.skin import SkinResult, store_skin_findings
from backend.analysis.sleep import SleepResult, store_sleep_findings
from backend.db.tables import findings, sample_metadata_obj

# (label, findings.module value, store_fn, empty-result factory). Every Result
# dataclass defaults all of its fields (empty lists / None), so the no-arg
# constructor is a genuine "no findings" result whose `rows` list is empty.
_CASES = [
    ("carrier", "carrier", store_carrier_findings, CarrierAnalysisResult),
    ("fitness", "fitness", store_fitness_findings, FitnessResult),
    ("gene_health", "gene_health", store_gene_health_findings, GeneHealthResult),
    ("methylation", "methylation", store_methylation_findings, MethylationResult),
    ("nutrigenomics", "nutrigenomics", store_nutrigenomics_findings, NutrigenomicsResult),
    ("skin", "skin", store_skin_findings, SkinResult),
    ("sleep", "sleep", store_sleep_findings, SleepResult),
    ("allergy", "allergy", store_allergy_findings, AllergyResult),
    ("rare_variants", "rare_variants", store_rare_variant_findings, RareVariantFinderResult),
]


@pytest.fixture()
def sample_engine(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
    sample_metadata_obj.create_all(engine)
    return engine


@pytest.mark.parametrize(
    ("label", "module", "store_fn", "empty_factory"),
    _CASES,
    ids=[case[0] for case in _CASES],
)
def test_empty_rerun_clears_stale_findings(
    sample_engine: sa.Engine,
    label: str,
    module: str,
    store_fn,
    empty_factory,
) -> None:
    # A finding persisted by a prior run of this module.
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [{"module": module, "finding_text": f"stale {label} finding from a prior sample"}],
        )

    # Rerun with an empty result: must clear the module's findings, not return early.
    count = store_fn(empty_factory(), sample_engine)
    assert count == 0

    with sample_engine.connect() as conn:
        remaining = conn.execute(
            sa.select(sa.func.count()).select_from(findings).where(findings.c.module == module)
        ).scalar()
    assert remaining == 0, f"{label}: stale findings not cleared on empty rerun"


def test_other_modules_findings_are_untouched(sample_engine: sa.Engine) -> None:
    """An empty rerun of one module must clear only that module's rows."""
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {"module": "carrier", "finding_text": "stale carrier finding"},
                {"module": "cancer", "finding_text": "unrelated cancer finding"},
            ],
        )

    store_carrier_findings(CarrierAnalysisResult(), sample_engine)

    with sample_engine.connect() as conn:
        by_module = dict(
            conn.execute(
                sa.select(findings.c.module, sa.func.count()).group_by(findings.c.module)
            ).all()
        )
    assert by_module == {"cancer": 1}
