"""Bridge from ingested PGS Catalog scores to the generic PRS engine (SW-B4).

SW-B1 ingests PGS Catalog GRCh37-harmonized scoring files into a standalone
``pgs_scores.db``. This module turns those rows into a
:class:`~backend.analysis.prs.PRSWeightSet` the generic engine can score, and
implements the SW-B4 score-selection policy:

  * **Prefer multi-ancestry / PRS-CSx scores.** Cross-ancestry methods transfer
    far better than single-ancestry (EUR-trained) scores, whose accuracy decays
    with genetic distance from the training population (Kachuri et al.,
    *Nat Rev Genet* 2023; Ding et al., *Nature* 2023).
  * **Select per the sample's inferred ancestry.** When several scores exist for
    a trait, pick the one whose development ancestry best covers the sample.

The candidate scores per trait live in
``backend/data/panels/pgs_score_registry.json``. Only ``bundle_ok`` (CC0/CC-BY)
scores are shipped; the rest are user-fetch and skipped unless present.

Genome-wide scores can carry 10^5–10^6 weights and may lack rsIDs (matched
positionally by the engine). Building a weight set is therefore a streamed read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog
from sqlalchemy.pool import NullPool

from backend.analysis.ancestry import ancestry_covered
from backend.analysis.prs import PRSSNPWeight, PRSWeightSet
from backend.annotation.pgs_catalog import (
    load_score_weights,
    pgs_score_metadata,
)

logger = structlog.get_logger(__name__)

_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "pgs_score_registry.json"
)

# Standalone PGS scores DB filename (SW-B1 ingestion target / SW-B5 bundle).
PGS_SCORES_DB_FILENAME = "pgs_scores.db"


def get_pgs_scores_engine(data_dir: Path | None = None) -> sa.Engine | None:
    """Return a read engine for ``pgs_scores.db``, or None when it is absent.

    The score DB is an optional bundle: when it is not installed (CI, fresh
    installs that have not fetched it) the consuming module degrades gracefully
    and emits nothing rather than erroring.
    """
    if data_dir is None:
        from backend.config import get_settings

        data_dir = get_settings().data_dir
    db_path = Path(data_dir) / PGS_SCORES_DB_FILENAME
    if not db_path.exists():
        logger.info("pgs_scores_db_absent", path=str(db_path))
        return None
    return sa.create_engine(f"sqlite:///{db_path}", poolclass=NullPool)


@dataclass
class PgsScoreSpec:
    """A registry entry: one candidate PGS Catalog score for a trait."""

    pgs_id: str
    module: str
    name: str
    trait_label: str
    method: str
    multi_ancestry: bool
    ancestries: list[str]
    source_study: str
    source_pmid: str
    sample_size: int
    license: str
    source_url: str
    bundle_ok: bool = True
    monogenic_genes: list[str] = field(default_factory=list)


# ── Registry loading ──────────────────────────────────────────────────────


def load_pgs_registry(path: Path | None = None) -> dict[str, list[PgsScoreSpec]]:
    """Load the curated trait → candidate-scores registry."""
    p = path or _REGISTRY_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    out: dict[str, list[PgsScoreSpec]] = {}
    for trait, entries in data.get("scores", {}).items():
        out[trait] = [
            PgsScoreSpec(
                pgs_id=e["pgs_id"],
                module=e["module"],
                name=e["name"],
                trait_label=e["trait_label"],
                method=e["method"],
                multi_ancestry=bool(e["multi_ancestry"]),
                ancestries=list(e["ancestries"]),
                source_study=e["source_study"],
                source_pmid=str(e["source_pmid"]),
                sample_size=int(e["sample_size"]),
                license=e["license"],
                source_url=e["source_url"],
                bundle_ok=bool(e.get("bundle_ok", True)),
                monogenic_genes=list(e.get("monogenic_genes", [])),
            )
            for e in entries
        ]
    return out


# ── Score selection (SW-B4) ───────────────────────────────────────────────


def _covers(spec: PgsScoreSpec, inferred_ancestry: str | None) -> bool:
    """Whether a score's development ancestries include the inferred ancestry.

    Thin wrapper over the shared :func:`ancestry_covered` (which applies the
    app↔catalog ancestry alias, e.g. CSA≡SAS), so score-selection coverage here
    and the warning decision in ``prs.check_ancestry_mismatch`` use one rule and
    cannot diverge (issue #339).
    """
    return ancestry_covered(inferred_ancestry, spec.ancestries)


def select_pgs_for_ancestry(
    specs: list[PgsScoreSpec],
    inferred_ancestry: str | None,
    *,
    bundle_only: bool = True,
) -> PgsScoreSpec | None:
    """Pick the best candidate score for the inferred ancestry.

    Policy (best first):
      1. A multi-ancestry score that **covers** the inferred ancestry.
      2. A single-ancestry score that **matches** the inferred ancestry.
      3. Any multi-ancestry score (better cross-ancestry transfer than EUR-only).
      4. The first remaining candidate (a mismatch warning is surfaced downstream
         by :func:`backend.analysis.prs.check_ancestry_mismatch`).

    Args:
        specs: Candidate scores for one trait.
        inferred_ancestry: The sample's inferred top ancestry, or None.
        bundle_only: Consider only ``bundle_ok`` (shipped) scores. User-fetch
            scores are excluded here; a BYO path enables them separately.

    Returns:
        The chosen spec, or None when no candidate is eligible.
    """
    candidates = [s for s in specs if s.bundle_ok] if bundle_only else list(specs)
    if not candidates:
        return None

    multi_covering = [s for s in candidates if s.multi_ancestry and _covers(s, inferred_ancestry)]
    if multi_covering:
        return multi_covering[0]

    single_matching = [
        s for s in candidates if not s.multi_ancestry and _covers(s, inferred_ancestry)
    ]
    if single_matching:
        return single_matching[0]

    multi_any = [s for s in candidates if s.multi_ancestry]
    if multi_any:
        return multi_any[0]

    return candidates[0]


def _resolve_source_ancestry(spec: PgsScoreSpec, inferred_ancestry: str | None) -> str:
    """Source-ancestry label that yields correct ancestry-mismatch behaviour.

    When the score covers the inferred ancestry (a multi-ancestry score including
    it, or a single-ancestry score matching it), report the inferred ancestry so
    :func:`check_ancestry_mismatch` raises no warning. Otherwise report the
    score's primary development ancestry so a mismatch is flagged.
    """
    if inferred_ancestry and _covers(spec, inferred_ancestry):
        return inferred_ancestry
    return spec.ancestries[0] if spec.ancestries else "EUR"


# ── Weight-set construction ───────────────────────────────────────────────


def build_weight_set_from_pgs(
    pgs_engine: sa.Engine,
    spec: PgsScoreSpec,
    trait: str,
    *,
    inferred_ancestry: str | None = None,
) -> PRSWeightSet | None:
    """Build a :class:`PRSWeightSet` for ``spec`` from ``pgs_scores.db``.

    Returns None when the score is absent from the DB (graceful degradation when
    the bundle is not installed — CI, fresh installs). SNPs keep their GRCh37
    coordinates so rsID-less genome-wide scores are matched positionally.
    """
    with pgs_engine.connect() as conn:
        meta = conn.execute(
            sa.select(pgs_score_metadata).where(pgs_score_metadata.c.pgs_id == spec.pgs_id)
        ).fetchone()
    if meta is None:
        logger.info("pgs_score_absent", pgs_id=spec.pgs_id, trait=trait)
        return None

    rows = load_score_weights(pgs_engine, spec.pgs_id)
    if not rows:
        logger.info("pgs_score_empty", pgs_id=spec.pgs_id, trait=trait)
        return None

    weights = [
        PRSSNPWeight(
            rsid=(r["rsid"] or ""),
            effect_allele=r["effect_allele"],
            weight=r["effect_weight"],
            other_allele=r["other_allele"],
            chrom=r["chrom"],
            pos=r["pos"],
        )
        for r in rows
    ]

    return PRSWeightSet(
        name=spec.name,
        trait=trait,
        module=spec.module,
        source_ancestry=_resolve_source_ancestry(spec, inferred_ancestry),
        multi_ancestry=spec.multi_ancestry,
        development_ancestries=list(spec.ancestries),
        source_study=spec.source_study,
        source_pmid=spec.source_pmid,
        sample_size=spec.sample_size,
        weights=weights,
        reference_mean=0.0,
        reference_std=1.0,
        # No baked-in reference distribution: percentile is withheld unless the
        # SW-B2 ancestry-continuous calibration supplies one at run time (#7).
        calibrated=False,
        pgs_id=spec.pgs_id,
        pgs_license=spec.license,
        development_method=spec.method,
        genome_build=meta.genome_build,
        variants_number=meta.variants_number,
        source_url=spec.source_url,
        monogenic_genes=list(spec.monogenic_genes),
    )


def build_trait_weight_set(
    pgs_engine: sa.Engine,
    trait: str,
    inferred_ancestry: str | None,
    *,
    registry: dict[str, list[PgsScoreSpec]] | None = None,
    bundle_only: bool = True,
) -> PRSWeightSet | None:
    """Select + build the best weight set for a trait and inferred ancestry.

    Returns None when the trait is unknown, no candidate is eligible, or the
    selected score is not present in ``pgs_scores.db``.
    """
    reg = registry or load_pgs_registry()
    specs = reg.get(trait)
    if not specs:
        return None
    spec = select_pgs_for_ancestry(specs, inferred_ancestry, bundle_only=bundle_only)
    if spec is None:
        return None
    return build_weight_set_from_pgs(pgs_engine, spec, trait, inferred_ancestry=inferred_ancestry)
