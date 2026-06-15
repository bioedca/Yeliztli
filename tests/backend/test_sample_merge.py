"""Tests for the sample-merge service core (Step 65 / MRG-02; Plan §10.2, §10.5).

These tests exercise :func:`backend.services.sample_merge.merge_samples`
end-to-end against a real on-disk ``DBRegistry`` (the ``merge_registry``
fixture defined below), driving the four §10.2
concordance buckets, the three §10.3 strategies, the deterministic
``file_hash`` recipe from §10.5 step 5, validation failures (membership /
status / staleness), and the §10.4 (a) ``(chrom, pos)`` PK divergence on
the freshly-created merged sample DB.

The Huey enqueue at the tail of :func:`merge_samples` is no-op'd via a
``monkeypatch`` on ``backend.tasks.huey_tasks.create_annotation_job`` so
the test never actually fires the annotation pipeline (covered by
``test_annotation_engine_merged_pk.py`` in Step 78). The merge service's
``except Exception`` around the enqueue would otherwise swallow whatever
error a fully-isolated test environment surfaces; explicit no-op'ing
keeps the seam visible.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import DBRegistry, get_registry, reset_registry
from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, create_sample_tables
from backend.db.tables import (
    annotation_state,
    individuals,
    jobs,
    merge_provenance,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.services import sample_merge as sample_merge_module
from backend.services.sample_merge import (
    InvalidMergeRequestError,
    MergeStrategy,
    StaleSourceError,
    _compute_file_hash,
    _estimate_duration_seconds,
    _resolve_winner,
    merge_samples,
    preview_merge,
)

# ── Test-scoped registry that the singleton-using ``staleness`` service sees ──
#
# ``is_sample_stale`` (and through it ``merge_samples``'s stale-source guard)
# calls ``backend.db.connection.get_registry()`` — the module-level singleton.
# The conftest ``db_registry`` fixture only builds an isolated registry; it
# does not redirect the singleton. We mimic the ``staleness_env`` setup from
# ``tests/backend/test_staleness.py``: patch ``get_settings`` so the singleton
# materialises against ``tmp_data_dir``, and yield it directly.


@pytest.fixture
def merge_registry(tmp_data_dir: Path):
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)
    ref_engine.dispose()

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        registry = get_registry()
        try:
            yield registry
        finally:
            registry.dispose_all()
            reset_registry()


# ── Fixture helpers ──────────────────────────────────────────────────


def _create_individual(registry: DBRegistry, display_name: str) -> int:
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            individuals.insert().values(
                display_name=display_name,
                notes="",
                updated_at=datetime.now(UTC),
            )
        )
    return int(result.inserted_primary_key[0])


def _create_source_sample(
    registry: DBRegistry,
    *,
    individual_id: int | None,
    name: str,
    file_format: str,
    file_hash: str,
    variants: list[dict],
    bundle_version: str = "v2.0.0",
    annotation_status: str = "complete",
) -> int:
    """Create one source sample (reference row + per-sample DB + jobs row).

    ``bundle_version`` lands in the new sample's ``annotation_state`` so the
    Step-11 staleness service treats it as fresh by default. Tests covering
    the stale-source branch pass ``bundle_version="v1.0.0"`` to drop it
    below the installed major.
    """
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name=name,
                db_path="",
                file_format=file_format,
                file_hash=file_hash,
                individual_id=individual_id,
                created_at=now,
                updated_at=now,
            )
        )
        sample_id = int(result.inserted_primary_key[0])
        db_path = f"samples/sample_{sample_id}.db"
        conn.execute(samples.update().where(samples.c.id == sample_id).values(db_path=db_path))
        conn.execute(
            jobs.insert().values(
                job_id=f"job-{sample_id}",
                sample_id=sample_id,
                job_type="annotation",
                status=annotation_status,
                progress_pct=100.0 if annotation_status == "complete" else 0.0,
                message="",
                created_at=now,
                updated_at=now,
            )
        )

    sample_db_path = registry.settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(engine, is_merged_sample=False)
    with engine.begin() as conn:
        if variants:
            conn.execute(raw_variants.insert(), variants)
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version",
                value=bundle_version,
                updated_at=now,
            )
        )
    return sample_id


def _seed_installed_vep_bundle(registry: DBRegistry, version: str = "v2.0.0") -> None:
    """Seed ``database_versions['vep_bundle']`` so the staleness service can compare."""
    from backend.db.tables import database_versions

    with registry.reference_engine.begin() as conn:
        conn.execute(
            sa.delete(database_versions).where(database_versions.c.db_name == "vep_bundle")
        )
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version=version,
                downloaded_at=datetime.now(UTC),
            )
        )


def _noop_annotation_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the Huey enqueue used at the tail of merge_samples with a no-op."""
    import backend.tasks.huey_tasks as huey_tasks

    monkeypatch.setattr(huey_tasks, "create_annotation_job", lambda _sid: "noop-job")
    monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_args, **_kw: None)


def _read_merge_rows(registry: DBRegistry, sample_id: int) -> list[sa.Row]:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    sample_db_path = registry.settings.data_dir / row.db_path
    engine = registry.get_sample_engine(sample_db_path)
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(raw_variants).order_by(raw_variants.c.chrom, raw_variants.c.pos)
            )
        )


def _read_merge_provenance(registry: DBRegistry, sample_id: int) -> sa.Row:
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    assert row is not None
    engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
    with engine.connect() as conn:
        prov = conn.execute(sa.select(merge_provenance)).fetchone()
    assert prov is not None
    return prov


# ── Pre-canned variant batches the buckets are constructed from ──────


def _v(rsid: str, chrom: str, pos: int, genotype: str) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}


# Two source samples whose loci exercise every §10.2 bucket.
#
#   coord (1, 100)  same rsid, same call → match (source='both')
#   coord (1, 200)  same rsid, S1 called, S2 no-call → filled_nocall (S1 wins)
#   coord (1, 300)  same rsid, S2 called, S1 no-call → filled_nocall (S2 wins)
#   coord (1, 400)  same rsid, different alleles → discordant (strategy decides)
#   coord (2, 500)  rsid only in S1 → unique (S1)
#   coord (2, 600)  rsid only in S2 → unique (S2)
#   coord (3, 700)  different rsids, both callers agree on genotype → match + collapsed_rsid

S1_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs200", "1", 200, "CT"),
    _v("rs300", "1", 300, "--"),
    _v("rs400", "1", 400, "AA"),
    _v("rs500", "2", 500, "GG"),
    _v("rs700_s1", "3", 700, "CT"),
]

S2_VARIANTS = [
    _v("rs100", "1", 100, "AG"),
    _v("rs200", "1", 200, "--"),
    _v("rs300", "1", 300, "GG"),
    _v("rs400", "1", 400, "GG"),
    _v("rs600", "2", 600, "AT"),
    _v("rs700_s2", "3", 700, "CT"),
]


# ── Test classes ─────────────────────────────────────────────────────


class TestResolveWinnerVendorVsPosition:
    """`_resolve_winner` decides by VENDOR token, not source position (#648).

    The contract (docstring §10.2): "source order in the request is irrelevant
    for strategy semantics". These cases pin that by reversing the vendor↔position
    correlation that every other fixture bakes in — so a position-based regression
    (e.g. ``return "S1" if PREFER_23ANDME else "S2"``) fails here.
    """

    def test_prefer_23andme_picks_the_23andme_side_regardless_of_position(self) -> None:
        # 23andMe as S2 → must return S2 (a position stub would say S1).
        assert _resolve_winner(MergeStrategy.PREFER_23ANDME, "ancestrydna", "23andme") == "S2"
        # 23andMe as S1 → S1 (the aligned case).
        assert _resolve_winner(MergeStrategy.PREFER_23ANDME, "23andme", "ancestrydna") == "S1"

    def test_prefer_ancestrydna_picks_the_ancestrydna_side_regardless_of_position(self) -> None:
        # AncestryDNA as S1 → must return S1 (a position stub would say S2).
        assert _resolve_winner(MergeStrategy.PREFER_ANCESTRYDNA, "ancestrydna", "23andme") == "S1"
        # AncestryDNA as S2 → S2 (the aligned case).
        assert _resolve_winner(MergeStrategy.PREFER_ANCESTRYDNA, "23andme", "ancestrydna") == "S2"

    def test_tiebreak_s1_when_both_or_neither_match_target(self) -> None:
        # Both sides the target vendor, or neither → S1 by convention (§10.2 step 2).
        assert _resolve_winner(MergeStrategy.PREFER_23ANDME, "23andme", "23andme") == "S1"
        assert _resolve_winner(MergeStrategy.PREFER_23ANDME, "ancestrydna", "ancestrydna") == "S1"

    def test_flag_only_returns_empty(self) -> None:
        assert _resolve_winner(MergeStrategy.FLAG_ONLY, "23andme", "ancestrydna") == ""


class TestHappyPath:
    """Plan §10.2 / §10.5: end-to-end against the dual-fixture buckets."""

    @pytest.fixture
    def merged_setup(
        self,
        merge_registry: DBRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[DBRegistry, int, int, int]:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Jane Doe")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="jane_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="jane_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=S2_VARIANTS,
        )
        return merge_registry, individual_id, s1_id, s2_id

    def test_flag_only_strategy_writes_sentinel_at_discordant(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        assert new_id not in (s1_id, s2_id)

        rows = _read_merge_rows(registry, new_id)
        by_coord = {(r.chrom, r.pos): r for r in rows}

        # Seven distinct loci across the union.
        assert len(rows) == 7

        # Match: identical call at (1, 100).
        match = by_coord[("1", 100)]
        assert match.source == "both"
        assert match.concordance == "match"
        assert match.genotype == "AG"
        assert match.discordant_alt_genotype == ""
        assert match.alt_rsid == ""

        # filled_nocall (S1 wins) at (1, 200).
        fn_s1 = by_coord[("1", 200)]
        assert fn_s1.source == "S1"
        assert fn_s1.concordance == "filled_nocall"
        assert fn_s1.genotype == "CT"
        assert fn_s1.discordant_alt_genotype == ""

        # filled_nocall (S2 wins) at (1, 300).
        fn_s2 = by_coord[("1", 300)]
        assert fn_s2.source == "S2"
        assert fn_s2.concordance == "filled_nocall"
        assert fn_s2.genotype == "GG"

        # Discordant at (1, 400) — flag_only emits ??.
        disc = by_coord[("1", 400)]
        assert disc.source == "both"
        assert disc.concordance == "discordant"
        assert disc.genotype == "??"
        assert disc.discordant_alt_genotype == "S1=AA;S2=GG"

        # Unique to S1 at (2, 500).
        unique_s1 = by_coord[("2", 500)]
        assert unique_s1.source == "S1"
        assert unique_s1.concordance == "unique"
        assert unique_s1.genotype == "GG"
        assert unique_s1.rsid == "rs500"

        # Unique to S2 at (2, 600).
        unique_s2 = by_coord[("2", 600)]
        assert unique_s2.source == "S2"
        assert unique_s2.concordance == "unique"
        assert unique_s2.genotype == "AT"
        assert unique_s2.rsid == "rs600"

        # Different-rsid-same-coordinate at (3, 700) → collapsed; S1's rsid
        # wins by §10.2 step 2 tiebreaker (bundle not seeded, so neither hit).
        collapsed = by_coord[("3", 700)]
        assert collapsed.source == "both"
        assert collapsed.concordance == "match"
        assert collapsed.genotype == "CT"
        assert collapsed.rsid == "rs700_s1"
        assert collapsed.alt_rsid == "rs700_s2"

    def test_concordance_summary_counts_match_buckets(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        prov = _read_merge_provenance(registry, new_id)
        summary = json.loads(prov.concordance_summary)
        # match (1,100) + match-via-collapse (3,700) = 2
        assert summary == {
            "match": 2,
            "filled_nocall": 2,
            "discordant": 1,
            "unique_S1": 1,
            "unique_S2": 1,
            "collapsed_rsid": 1,
        }
        # Row-count invariant locked by Plan §10.4 (c): the primary partition
        # sums to total merged loci (collapsed_rsid is an additive marker).
        total = (
            summary["match"]
            + summary["filled_nocall"]
            + summary["discordant"]
            + summary["unique_S1"]
            + summary["unique_S2"]
        )
        rows = _read_merge_rows(registry, new_id)
        assert total == len(rows) == 7

    def test_prefer_23andme_keeps_s1_call_at_discordant(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.PREFER_23ANDME,
            display_name="Jane Doe (prefer 23andMe)",
        )
        rows = {(r.chrom, r.pos): r for r in _read_merge_rows(registry, new_id)}
        disc = rows[("1", 400)]
        assert disc.concordance == "discordant"
        assert disc.genotype == "AA"  # S1 (23andMe) wins
        assert disc.discordant_alt_genotype == "S2=GG"

    def test_prefer_ancestrydna_keeps_s2_call_at_discordant(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.PREFER_ANCESTRYDNA,
            display_name="Jane Doe (prefer AncestryDNA)",
        )
        rows = {(r.chrom, r.pos): r for r in _read_merge_rows(registry, new_id)}
        disc = rows[("1", 400)]
        assert disc.concordance == "discordant"
        assert disc.genotype == "GG"  # S2 (AncestryDNA) wins
        assert disc.discordant_alt_genotype == "S1=AA"

    # ── Vendor resolution decoupled from source position (#648) ──────────
    #
    # Every other fixture seeds 23andMe as S1 + AncestryDNA as S2, so "prefer
    # vendor X wins" is indistinguishable from "position S1/S2 wins". This fixture
    # REVERSES that — AncestryDNA is S1, 23andMe is S2 — so a discordant locus
    # discriminates vendor-based resolution (the contract: source order is
    # irrelevant) from a position-based regression. At (1, 400) the AncestryDNA
    # (S1) call is "GG" and the 23andMe (S2) call is "AA".

    @pytest.fixture
    def reversed_vendor_setup(
        self,
        merge_registry: DBRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[DBRegistry, int, int, int]:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Reversed Vendors")
        # S1 = AncestryDNA (its calls), S2 = 23andMe (its calls) — vendor token
        # is now anti-correlated with source position.
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="rev_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_rev_s1",
            variants=S2_VARIANTS,  # the AncestryDNA calls (rs400="GG")
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="rev_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_rev_s2",
            variants=S1_VARIANTS,  # the 23andMe calls (rs400="AA")
        )
        return merge_registry, individual_id, s1_id, s2_id

    def test_prefer_23andme_wins_by_vendor_not_position(
        self, reversed_vendor_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        """prefer_23andme keeps the 23andMe call even when 23andMe is S2 (#648).

        The 23andMe file is the *second* source here, so a position-based
        regression (S1 wins for prefer_23andme) would keep the AncestryDNA "GG"
        and silently corrupt the merged genotype. Vendor-based resolution keeps
        the 23andMe "AA" (the S2 side). This merge is reachable end-to-end via the
        commit route, which passes source_sample_ids verbatim with no ordering.
        """
        registry, individual_id, s1_id, s2_id = reversed_vendor_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.PREFER_23ANDME,
            display_name="Reversed (prefer 23andMe)",
        )
        disc = {(r.chrom, r.pos): r for r in _read_merge_rows(registry, new_id)}[("1", 400)]
        assert disc.concordance == "discordant"
        assert disc.genotype == "AA"  # the 23andMe call (S2) wins by vendor
        assert disc.discordant_alt_genotype == "S1=GG"  # AncestryDNA (S1) is the loser

    def test_prefer_ancestrydna_wins_by_vendor_not_position(
        self, reversed_vendor_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        """Mirror of the above: prefer_ancestrydna keeps the AncestryDNA call even
        when AncestryDNA is S1 (a position regression for prefer_ancestrydna would
        wrongly take S2 = the 23andMe "AA")."""
        registry, individual_id, s1_id, s2_id = reversed_vendor_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.PREFER_ANCESTRYDNA,
            display_name="Reversed (prefer AncestryDNA)",
        )
        disc = {(r.chrom, r.pos): r for r in _read_merge_rows(registry, new_id)}[("1", 400)]
        assert disc.concordance == "discordant"
        assert disc.genotype == "GG"  # the AncestryDNA call (S1) wins by vendor
        assert disc.discordant_alt_genotype == "S2=AA"  # 23andMe (S2) is the loser

    def test_new_sample_row_carries_individual_and_format(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(samples).where(samples.c.id == new_id)).fetchone()
        assert row is not None
        assert row.individual_id == individual_id
        assert row.file_format == "merged_v1"
        assert row.db_path == f"samples/sample_{new_id}.db"
        assert (registry.settings.data_dir / row.db_path).exists()

    def test_merged_db_uses_chrom_pos_pk(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == new_id)
            ).fetchone()
        assert row is not None
        engine = registry.get_sample_engine(registry.settings.data_dir / row.db_path)
        pk = sa.inspect(engine).get_pk_constraint("raw_variants")
        assert pk["constrained_columns"] == ["chrom", "pos"]

    def test_merge_provenance_payload_shape(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        registry, individual_id, s1_id, s2_id = merged_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        prov = _read_merge_provenance(registry, new_id)
        assert prov.id == 1
        assert prov.strategy == "flag_only"
        assert json.loads(prov.source_sample_ids) == [s1_id, s2_id]
        assert json.loads(prov.source_file_hashes) == ["hash_s1", "hash_s2"]


class TestFileHashRecipe:
    """Plan §10.5 step 5: ``SHA-256(S1 ‖ S2 ‖ strategy ‖ SAMPLE_SCHEMA_VERSION)``."""

    def test_compute_file_hash_is_deterministic(self) -> None:
        first = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        second = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        assert first == second

    def test_compute_file_hash_is_order_sensitive(self) -> None:
        forward = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        reverse = _compute_file_hash("b", "a", MergeStrategy.FLAG_ONLY)
        assert forward != reverse

    def test_compute_file_hash_diverges_per_strategy(self) -> None:
        flag = _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY)
        prefer_a = _compute_file_hash("a", "b", MergeStrategy.PREFER_23ANDME)
        prefer_b = _compute_file_hash("a", "b", MergeStrategy.PREFER_ANCESTRYDNA)
        assert len({flag, prefer_a, prefer_b}) == 3

    def test_compute_file_hash_includes_schema_version(self) -> None:
        # Recreates the SHA-256 payload contract so a future bump trips the
        # test before it silently produces colliding hashes across versions.
        expected = hashlib.sha256(f"a|b|flag_only|{SAMPLE_SCHEMA_VERSION}".encode()).hexdigest()
        assert _compute_file_hash("a", "b", MergeStrategy.FLAG_ONLY) == expected


class TestValidation:
    """Plan §10.5 step 1: membership / status / shape validation surface."""

    def _setup(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[DBRegistry, int]:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Owner")
        return merge_registry, individual_id

    def test_wrong_source_count_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        with pytest.raises(InvalidMergeRequestError, match="exactly 2"):
            merge_samples(
                registry,
                source_sample_ids=[1],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_duplicate_source_ids_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        with pytest.raises(InvalidMergeRequestError, match="distinct"):
            merge_samples(
                registry,
                source_sample_ids=[7, 7],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_missing_display_name_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        with pytest.raises(InvalidMergeRequestError, match="display_name"):
            merge_samples(
                registry,
                source_sample_ids=[1, 2],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="   ",
            )

    def test_sample_not_found_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        s1_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        with pytest.raises(InvalidMergeRequestError, match="not found"):
            merge_samples(
                registry,
                source_sample_ids=[s1_id, 9999],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_sample_linked_elsewhere_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        other_id = _create_individual(registry, "Other")
        s1_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            registry,
            individual_id=other_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
        )
        with pytest.raises(InvalidMergeRequestError, match="not linked to individual"):
            merge_samples(
                registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

    def test_source_annotation_not_complete_raises(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, individual_id = self._setup(merge_registry, monkeypatch)
        s1_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
            annotation_status="running",
        )
        with pytest.raises(InvalidMergeRequestError, match="not complete"):
            merge_samples(
                registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )


class TestStaleSource:
    """Plan §10.5 step 1 → §7.4: stale source → HTTP 423 payload."""

    def test_stale_source_raises_with_detail(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Stale Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
            bundle_version="v1.0.0",
        )
        with pytest.raises(StaleSourceError) as exc_info:
            merge_samples(
                merge_registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )
        err = exc_info.value
        assert err.stale_sample_ids == [s2_id]
        assert err.detail["error"] == "stale_source_sample"
        assert err.detail["stale_sample_ids"] == [s2_id]
        assert "Re-annotate" in err.detail["message"]


class TestNoProductionSideEffects:
    """A failed validation never creates a samples row or a per-sample DB."""

    def test_invalid_request_creates_no_artefacts(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Owner")
        with pytest.raises(InvalidMergeRequestError):
            merge_samples(
                merge_registry,
                source_sample_ids=[1, 2],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="x",
            )

        # No samples row, no sample_*.db on disk.
        with merge_registry.reference_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar()
        assert count == 0
        samples_dir = merge_registry.settings.data_dir / "samples"
        assert not any(samples_dir.glob("sample_*.db"))


class TestVepBundleTiebreaker:
    """Plan §10.2 step 2: bundle-membership tiebreaker at rsid collapses.

    Exercises ``_apply_semantics`` directly so the test doesn't need a real
    ~600 MB VEP bundle on disk — the merge service's ``_rsids_in_vep_bundle``
    is replaced inline via the ``rsids_in_bundle`` parameter that the helper
    consumes.
    """

    def test_bundle_hit_wins_against_non_hit(self) -> None:
        from backend.services.sample_merge import _apply_semantics

        s1 = {("1", 100): {"rsid": "rs_in_bundle", "genotype": "AG"}}
        s2 = {("1", 100): {"rsid": "rs_missing", "genotype": "AG"}}
        rows, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle={"rs_in_bundle"},
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.collapsed_rsid == 1
        assert rows[0].rsid == "rs_in_bundle"
        assert rows[0].alt_rsid == "rs_missing"

    def test_s2_hit_wins_over_s1(self) -> None:
        from backend.services.sample_merge import _apply_semantics

        s1 = {("1", 100): {"rsid": "rs_s1_only", "genotype": "AG"}}
        s2 = {("1", 100): {"rsid": "rs_bundle", "genotype": "AG"}}
        rows, _ = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle={"rs_bundle"},
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert rows[0].rsid == "rs_bundle"
        assert rows[0].alt_rsid == "rs_s1_only"

    def test_neither_in_bundle_falls_back_to_s1(self) -> None:
        from backend.services.sample_merge import _apply_semantics

        s1 = {("1", 100): {"rsid": "rs_a", "genotype": "AG"}}
        s2 = {("1", 100): {"rsid": "rs_b", "genotype": "AG"}}
        rows, _ = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert rows[0].rsid == "rs_a"
        assert rows[0].alt_rsid == "rs_b"


class TestAnnotationEnqueue:
    """Plan §10.5 step 8: merge_samples enqueues the standard annotation job."""

    def test_enqueue_is_invoked_with_new_sample_id(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        calls: dict[str, list[int]] = {"create": [], "run": []}
        import backend.tasks.huey_tasks as huey_tasks

        def fake_create(sample_id: int) -> str:
            calls["create"].append(sample_id)
            return f"job-{sample_id}"

        def fake_run(sample_id: int, job_id: str) -> None:  # noqa: ARG001
            calls["run"].append(sample_id)

        monkeypatch.setattr(huey_tasks, "create_annotation_job", fake_create)
        monkeypatch.setattr(huey_tasks, "run_annotation_task", fake_run)

        individual_id = _create_individual(merge_registry, "Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
        )
        new_id = merge_samples(
            merge_registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Jane Doe (merged)",
        )
        assert calls["create"] == [new_id]
        assert calls["run"] == [new_id]

    def test_enqueue_failure_is_swallowed(
        self,
        merge_registry: DBRegistry,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failure to enqueue annotation must not roll back the merge."""
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        import backend.tasks.huey_tasks as huey_tasks

        def raise_value_error(_sid: int) -> str:
            raise ValueError("annotation already running")

        monkeypatch.setattr(huey_tasks, "create_annotation_job", raise_value_error)
        monkeypatch.setattr(huey_tasks, "run_annotation_task", lambda *_a, **_k: None)

        individual_id = _create_individual(merge_registry, "Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
        )
        with caplog.at_level("WARNING", logger=sample_merge_module.__name__):
            new_id = merge_samples(
                merge_registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
                display_name="Jane Doe (merged)",
            )

        # The merged sample still exists on disk.
        with merge_registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == new_id)
            ).fetchone()
        assert row is not None
        assert (merge_registry.settings.data_dir / row.db_path).exists()
        assert any(
            "merge_annotation_enqueue_failed" in record.message for record in caplog.records
        )


class TestPreviewMerge:
    """Plan §10.6 / Step 67: ``preview_merge`` dry-run helper.

    Exhaustive surface (every Plan §10.5 step-1 failure mode + missing
    ``annotation_state`` fallback) lives in
    ``tests/backend/test_sample_merge_preview.py`` (Step 73 / MRG-03a).
    This class only locks the helper-split contract introduced in this
    step: the dry-run returns the same concordance counts the commit path
    writes into ``merge_provenance.concordance_summary``, packages
    ``est_duration_seconds`` per the §10.6 contract, and never writes a
    samples row or per-sample DB.
    """

    def test_estimate_duration_floor_is_baseline(self) -> None:
        # Zero rows → just the baseline (annotation-queue overhead).
        assert _estimate_duration_seconds(0) == 5

    def test_estimate_duration_scales_with_row_count(self) -> None:
        # ~25k rows/sec amortised against the Step 85 perf budget.
        assert _estimate_duration_seconds(25_000) == 6
        assert _estimate_duration_seconds(700_000) == 33

    def test_preview_returns_summary_and_estimate_without_writing(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Preview Subject")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="jane_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="jane_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=S2_VARIANTS,
        )

        # Snapshot the reference DB's samples count + the on-disk per-
        # sample DB files BEFORE the dry-run so we can assert nothing new
        # lands during preview (the contract Plan §10.6 declares).
        with merge_registry.reference_engine.connect() as conn:
            pre_count = conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar()
        pre_db_files = sorted((merge_registry.settings.data_dir / "samples").glob("sample_*.db"))

        result = preview_merge(
            merge_registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
        )

        # Same bucket counts the commit path locks in
        # ``TestHappyPath::test_concordance_summary_counts_match_buckets``.
        assert result["concordance_summary"] == {
            "match": 2,
            "filled_nocall": 2,
            "discordant": 1,
            "unique_S1": 1,
            "unique_S2": 1,
            "collapsed_rsid": 1,
        }
        # 7 rows → baseline 5 + 7//25_000 = 5 + 0 = 5.
        assert result["est_duration_seconds"] == 5

        # No samples row added; no per-sample DB file created.
        with merge_registry.reference_engine.connect() as conn:
            post_count = conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar()
        assert post_count == pre_count
        post_db_files = sorted((merge_registry.settings.data_dir / "samples").glob("sample_*.db"))
        assert post_db_files == pre_db_files

    def test_preview_propagates_invalid_request(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Owner")
        with pytest.raises(InvalidMergeRequestError, match="exactly 2"):
            preview_merge(
                merge_registry,
                source_sample_ids=[1],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
            )

    def test_preview_propagates_stale_source(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Stale Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="a.txt",
            file_format="23andme_v5",
            file_hash="h1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="b.txt",
            file_format="ancestrydna_v2.0",
            file_hash="h2",
            variants=S2_VARIANTS,
            bundle_version="v1.0.0",
        )
        with pytest.raises(StaleSourceError) as exc_info:
            preview_merge(
                merge_registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=MergeStrategy.FLAG_ONLY,
            )
        assert exc_info.value.stale_sample_ids == [s2_id]
        assert exc_info.value.detail["error"] == "stale_source_sample"


class TestEmptySourcesEdgeCase:
    """Defensive shape: zero overlapping coordinates → empty merge with provenance."""

    def test_empty_sources_create_merged_sample_with_zero_rows(
        self, merge_registry: DBRegistry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Empty Owner")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="empty1.txt",
            file_format="23andme_v5",
            file_hash="he1",
            variants=[],
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="empty2.txt",
            file_format="ancestrydna_v2.0",
            file_hash="he2",
            variants=[],
        )
        new_id = merge_samples(
            merge_registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Empty Merge",
        )
        assert _read_merge_rows(merge_registry, new_id) == []
        prov = _read_merge_provenance(merge_registry, new_id)
        summary = json.loads(prov.concordance_summary)
        assert summary == {
            "match": 0,
            "filled_nocall": 0,
            "discordant": 0,
            "unique_S1": 0,
            "unique_S2": 0,
            "collapsed_rsid": 0,
        }


# ── Step 76 / MRG-08 — Merge semantics against the dual-upload fixture ───
#
# The fixture at ``tests/fixtures/sample_dual_upload_individual/`` (created
# in Step 75) pairs ``23andme.txt`` (S1) with ``ancestrydna.txt`` (S2) for
# one simulated individual and ships ``expected_concordance.json`` as
# bio-validator's hand-curated gold standard. The tests below run the full
# parse → merge round-trip via :func:`merge_samples` for all three Plan
# §10.3 strategies and assert:
#
#   • ``concordance_summary`` bucket counts match the gold standard within
#     ±1% (Plan §15.1 MRG-08 assertion; effectively exact for the small
#     19-locus fixture — `max(1, ceil(0.01 * count))` floors at 1).
#   • Per-locus outcomes (``rsid`` / ``genotype`` / ``source`` /
#     ``concordance`` / ``discordant_alt_genotype`` / ``alt_rsid``) match
#     each locus's expected outcome in ``expected_concordance.json``
#     including the strategy-specific ``strategy_outcomes`` for every
#     discordant locus.
#
# ``TestAlleleOrderNormalization`` and ``TestIndelDiscordance`` round out
# the MRG-08 list with direct ``_apply_semantics`` assertions for the
# allele-order rule (Plan §10.2 step 3 bullet 1's "AG=GA per parse-time
# sorted-pair canonicalization") and indel-vs-indel discordance (``II``
# vs ``DI``) without needing to materialise a per-sample DB.

_DUAL_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "sample_dual_upload_individual"
)


def _load_dual_fixture_variants() -> tuple[list[dict], list[dict], dict]:
    """Parse the dual-upload fixture and return (S1 variants, S2 variants, gold).

    Each variant dict is shaped for ``raw_variants.insert()`` — the same
    contract :func:`_create_source_sample` consumes. Calls the vendor-
    specific parsers directly (matching Step 75's local sanity check)
    rather than ``dispatcher.parse``: the AncestryDNA leg's documentation
    comment cross-references ``23andme.txt`` and trips the dispatcher's
    ``_23ANDME_SUBSTRING`` substring detector, which would mis-route the
    file to the 23andMe parser. Bypassing the dispatcher here keeps the
    test focused on merge semantics; dispatcher detection on the broader
    fixture corpus is locked by ``tests/backend/test_dispatcher.py``.
    """
    from backend.ingestion.parser_23andme import parse_23andme
    from backend.ingestion.parser_ancestrydna import parse_ancestrydna

    s1_result = parse_23andme(_DUAL_FIXTURE_DIR / "23andme.txt")
    s2_result = parse_ancestrydna(_DUAL_FIXTURE_DIR / "ancestrydna.txt")
    s1_variants = [
        {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
        for v in s1_result.variants
    ]
    s2_variants = [
        {"rsid": v.rsid, "chrom": v.chrom, "pos": v.pos, "genotype": v.genotype}
        for v in s2_result.variants
    ]
    gold = json.loads(
        (_DUAL_FIXTURE_DIR / "expected_concordance.json").read_text(encoding="utf-8")
    )
    return s1_variants, s2_variants, gold


def _within_one_percent(actual: int, expected: int) -> bool:
    """Plan §15.1 MRG-08: bucket counts within ±1% of the gold standard.

    For the 19-locus fixture every bucket is small (≤5), so the ±1% band
    rounds down to a 1-locus tolerance — preserved here so the same
    assertion can scale to a larger fixture without rewriting.
    """
    if expected == 0:
        return actual == 0
    tolerance = max(1, (expected + 99) // 100)
    return abs(actual - expected) <= tolerance


class TestDualUploadFixtureMergeSemantics:
    """Plan §15.1 MRG-08 — three strategies × four concordance buckets."""

    @pytest.fixture
    def fixture_setup(
        self,
        merge_registry: DBRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[DBRegistry, int, int, int, dict]:
        s1_variants, s2_variants, gold = _load_dual_fixture_variants()
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Dual Upload Subject")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="dual_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_dual_s1",
            variants=s1_variants,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="dual_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_dual_s2",
            variants=s2_variants,
        )
        return merge_registry, individual_id, s1_id, s2_id, gold

    @pytest.mark.parametrize(
        "strategy",
        [
            MergeStrategy.FLAG_ONLY,
            MergeStrategy.PREFER_23ANDME,
            MergeStrategy.PREFER_ANCESTRYDNA,
        ],
    )
    def test_concordance_summary_matches_gold_within_one_percent(
        self,
        fixture_setup: tuple[DBRegistry, int, int, int, dict],
        strategy: MergeStrategy,
    ) -> None:
        # Strategy only affects which call wins at a discordant locus — the
        # partition itself is strategy-invariant (Plan §10.3, locked by
        # ``test_sample_merge_preview`` as well). Assert here against every
        # strategy so a future regression that leaks strategy into bucketing
        # surfaces immediately.
        registry, individual_id, s1_id, s2_id, gold = fixture_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=strategy,
            display_name=f"Dual Upload ({strategy.value})",
        )
        prov = _read_merge_provenance(registry, new_id)
        summary = json.loads(prov.concordance_summary)
        expected = gold["concordance_summary"]
        for key, expected_count in expected.items():
            actual_count = summary[key]
            assert _within_one_percent(actual_count, expected_count), (
                f"strategy={strategy.value} bucket={key}: "
                f"actual={actual_count} expected={expected_count} (±1%)"
            )

        # Row-count invariant (Plan §10.4 (c)): primary partition sums to
        # total merged loci. collapsed_rsid is additive, not part of the sum.
        primary_total = (
            summary["match"]
            + summary["filled_nocall"]
            + summary["discordant"]
            + summary["unique_S1"]
            + summary["unique_S2"]
        )
        rows = _read_merge_rows(registry, new_id)
        assert primary_total == len(rows) == gold["total_merged_loci"]

    @pytest.mark.parametrize(
        "strategy",
        [
            MergeStrategy.FLAG_ONLY,
            MergeStrategy.PREFER_23ANDME,
            MergeStrategy.PREFER_ANCESTRYDNA,
        ],
    )
    def test_per_locus_outcomes_match_gold(
        self,
        fixture_setup: tuple[DBRegistry, int, int, int, dict],
        strategy: MergeStrategy,
    ) -> None:
        registry, individual_id, s1_id, s2_id, gold = fixture_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=strategy,
            display_name=f"Dual Upload ({strategy.value})",
        )
        rows_by_coord = {(r.chrom, int(r.pos)): r for r in _read_merge_rows(registry, new_id)}
        # Every locus the gold JSON declares must materialise as exactly one
        # merged row at its coordinate (Plan §10.4 (a) — `(chrom, pos)` PK on
        # merged-sample raw_variants → one row per locus regardless of
        # strategy).
        assert len(rows_by_coord) == gold["total_merged_loci"], (
            f"strategy={strategy.value}: merged row count "
            f"{len(rows_by_coord)} != gold total_merged_loci "
            f"{gold['total_merged_loci']}"
        )

        for locus in gold["loci"]:
            coord = (str(locus["chrom"]), int(locus["pos"]))
            row = rows_by_coord.get(coord)
            assert row is not None, (
                f"strategy={strategy.value} locus={coord}: missing from merged raw_variants"
            )

            concordance = locus["concordance"]
            if concordance == "discordant":
                outcome = locus["strategy_outcomes"][strategy.value]
                assert row.concordance == "discordant", (
                    f"strategy={strategy.value} locus={coord}: "
                    f"concordance={row.concordance!r} expected 'discordant'"
                )
                assert row.genotype == outcome["genotype"], (
                    f"strategy={strategy.value} locus={coord}: "
                    f"genotype={row.genotype!r} expected {outcome['genotype']!r}"
                )
                assert row.discordant_alt_genotype == outcome["discordant_alt_genotype"], (
                    f"strategy={strategy.value} locus={coord}: "
                    f"discordant_alt_genotype={row.discordant_alt_genotype!r} "
                    f"expected {outcome['discordant_alt_genotype']!r}"
                )
                assert row.source == outcome["source"]
            else:
                # Match / filled_nocall / unique — strategy-invariant per
                # Plan §10.3 (strategy only fires inside the discordant
                # branch). Outcome encoded at the locus's top level.
                assert row.concordance == concordance, (
                    f"strategy={strategy.value} locus={coord}: "
                    f"concordance={row.concordance!r} expected {concordance!r}"
                )
                assert row.source == locus["source"]
                # No strategy can move a non-discordant locus into the
                # discordant_alt_genotype column.
                assert row.discordant_alt_genotype == ""

            # rsid-collapse contract (Plan §10.2 step 2): at a different-
            # rsid-same-coordinate collapse the winner's rsid is in `rsid`
            # and the loser is in `alt_rsid`. Bundle membership decides the
            # winner when both / neither are present; under the test
            # harness neither rsid is in the bundle → S1 wins by convention
            # (`_resolve_winner`'s tiebreaker).
            if locus["collapsed_rsid"]:
                assert row.rsid == locus["s1_rsid"], (
                    f"strategy={strategy.value} locus={coord}: rsid "
                    f"{row.rsid!r} expected S1's {locus['s1_rsid']!r} (no "
                    "VEP bundle in harness → S1 tiebreaker)"
                )
                assert row.alt_rsid == locus["s2_rsid"]
            else:
                # Non-collapsed: rsid is whichever side had it (or both —
                # same value). Discordant loci have no top-level `source`
                # (strategy decides — see `strategy_outcomes.source` above);
                # at every discordant locus in the gold standard both sides
                # share the same rsid (the rsid-collapse case is always
                # tagged `collapsed_rsid: true` and handled above), so the
                # winning row's rsid equals both sides' rsids.
                top_source = locus.get("source")
                if top_source is None:
                    # Discordant non-collapsed: both sides shared the rsid.
                    assert row.rsid == locus["s1_rsid"] == locus["s2_rsid"]
                elif top_source == "S1":
                    assert row.rsid == locus["s1_rsid"]
                elif top_source == "S2":
                    assert row.rsid == locus["s2_rsid"]
                else:
                    # source == 'both' — both sides shared the rsid.
                    assert row.rsid == locus["s1_rsid"] == locus["s2_rsid"]
                assert row.alt_rsid == ""

    def test_flag_only_writes_paired_alt_genotype_at_every_discordant(
        self,
        fixture_setup: tuple[DBRegistry, int, int, int, dict],
    ) -> None:
        """Plan §10.3: ``flag_only`` writes ``S1=...;S2=...`` at every discordant locus.

        Locks the canonical encoding contract independently from the per-
        locus check above so a future regression that drops one side of the
        paired encoding (e.g., emitting only S2's call) surfaces with a
        focused failure.
        """
        registry, individual_id, s1_id, s2_id, gold = fixture_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Dual Upload (flag_only)",
        )
        rows_by_coord = {(r.chrom, int(r.pos)): r for r in _read_merge_rows(registry, new_id)}
        for locus in gold["loci"]:
            if locus["concordance"] != "discordant":
                continue
            coord = (str(locus["chrom"]), int(locus["pos"]))
            row = rows_by_coord[coord]
            assert row.genotype == "??"
            assert row.source == "both"
            assert row.discordant_alt_genotype.startswith("S1=")
            assert ";S2=" in row.discordant_alt_genotype
            # The paired encoding's S1 / S2 values match the source-sample
            # genotypes (parser canonicalized; same byte string both sides).
            assert row.discordant_alt_genotype == (
                f"S1={locus['s1_genotype']};S2={locus['s2_genotype']}"
            )

    @pytest.mark.parametrize(
        ("strategy", "loser_side"),
        [
            (MergeStrategy.PREFER_23ANDME, "S2"),
            (MergeStrategy.PREFER_ANCESTRYDNA, "S1"),
        ],
    )
    def test_prefer_strategies_park_losing_call_in_alt(
        self,
        fixture_setup: tuple[DBRegistry, int, int, int, dict],
        strategy: MergeStrategy,
        loser_side: str,
    ) -> None:
        """Plan §10.3: ``prefer_*`` lands the losing call in ``discordant_alt_genotype``.

        Single-key encoding (``"S1=..."`` or ``"S2=..."``) — never paired —
        identifying the loser by source tag. Mirrors the
        ``strategy_outcomes`` block in ``expected_concordance.json``.
        """
        registry, individual_id, s1_id, s2_id, gold = fixture_setup
        new_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=strategy,
            display_name=f"Dual Upload ({strategy.value})",
        )
        rows_by_coord = {(r.chrom, int(r.pos)): r for r in _read_merge_rows(registry, new_id)}
        for locus in gold["loci"]:
            if locus["concordance"] != "discordant":
                continue
            coord = (str(locus["chrom"]), int(locus["pos"]))
            row = rows_by_coord[coord]
            outcome = locus["strategy_outcomes"][strategy.value]
            assert row.discordant_alt_genotype == outcome["discordant_alt_genotype"]
            assert row.discordant_alt_genotype.startswith(f"{loser_side}=")
            # Paired encoding never appears under prefer_* strategies.
            assert ";" not in row.discordant_alt_genotype


class TestAlleleOrderNormalization:
    """Plan §10.2 step 3 bullet 1 + MRG-08 — ``AG == GA``, ``DI == DI``, ``II == II`` match.

    Direct ``_apply_semantics`` calls so the assertions don't depend on
    materializing per-sample DBs; the parser canonicalizes at ingestion so
    a real merged DB never sees un-sorted pairs, but the merge service must
    still tolerate pre-Phase-1 sample DBs that may carry un-canonicalized
    genotypes (Plan §10.2 step 3 bullet 1's "Defensive in-merge re-sort
    remains" contract).
    """

    @staticmethod
    def _two_sided(s1_gt: str, s2_gt: str) -> tuple[dict, dict]:
        s1 = {("1", 100): {"rsid": "rs_locus", "genotype": s1_gt}}
        s2 = {("1", 100): {"rsid": "rs_locus", "genotype": s2_gt}}
        return s1, s2

    def test_canonical_pair_matches_itself(self) -> None:
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("AG", "AG")
        rows, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.match == 1
        assert summary.discordant == 0
        assert rows[0].concordance == "match"
        assert rows[0].genotype == "AG"

    def test_unsorted_pair_collapses_to_match(self) -> None:
        """Plan §10.2 step 3 bullet 1: ``AG == GA`` counts as concordant ``match``.

        A pre-Phase-1 sample DB could legitimately carry ``"GA"`` because
        the legacy 23andMe parser did not canonicalize. The merge service's
        defensive re-sort treats it as the same allele set.
        """
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("AG", "GA")
        rows, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.match == 1, "Plan §10.2 step 3 bullet 1: AG == GA must be concordant match"
        assert summary.discordant == 0
        assert rows[0].concordance == "match"

    def test_homozygous_indel_matches_itself(self) -> None:
        """``DI == DI`` and ``II == II`` count as concordant ``match``."""
        from backend.services.sample_merge import _apply_semantics

        for gt in ("DI", "II", "DD"):
            s1, s2 = self._two_sided(gt, gt)
            _, summary = _apply_semantics(
                s1,
                s2,
                strategy=MergeStrategy.FLAG_ONLY,
                rsids_in_bundle=set(),
                s1_vendor="23andme",
                s2_vendor="ancestrydna",
            )
            assert summary.match == 1, (
                f"{gt} vs {gt}: expected match, got summary={summary.to_dict()}"
            )
            assert summary.discordant == 0

    def test_different_alleles_are_discordant(self) -> None:
        """``AG != AT`` counts as discordant — different allele sets."""
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("AG", "AT")
        rows, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.discordant == 1
        assert summary.match == 0
        assert rows[0].concordance == "discordant"


class TestIndelDiscordance:
    """Plan §15.1 MRG-08 — indel-vs-indel discordance (``II`` vs ``DI``) covered.

    Direct ``_apply_semantics`` calls so the test doesn't depend on the
    dual-upload fixture (which has no indel rows by construction; indels
    aren't part of bio-validator's hand-curated concordance gold standard).
    """

    @staticmethod
    def _two_sided(s1_gt: str, s2_gt: str) -> tuple[dict, dict]:
        s1 = {("1", 100): {"rsid": "rs_indel", "genotype": s1_gt}}
        s2 = {("1", 100): {"rsid": "rs_indel", "genotype": s2_gt}}
        return s1, s2

    def test_homozygous_insertion_vs_heterozygous_indel_is_discordant(self) -> None:
        """``II`` vs ``DI`` — different indel call states → discordant."""
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("II", "DI")
        rows, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.discordant == 1, (
            f"II vs DI: expected discordant, got summary={summary.to_dict()}"
        )
        assert summary.match == 0
        assert rows[0].concordance == "discordant"
        assert rows[0].genotype == "??"
        assert rows[0].discordant_alt_genotype == "S1=II;S2=DI"

    def test_homozygous_deletion_vs_heterozygous_indel_is_discordant(self) -> None:
        """``DD`` vs ``DI`` — symmetric to II vs DI."""
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("DD", "DI")
        _, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.discordant == 1
        assert summary.match == 0

    def test_homozygous_indels_are_discordant_with_each_other(self) -> None:
        """``II`` vs ``DD`` — opposite homozygous indel calls → discordant."""
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("II", "DD")
        _, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.discordant == 1
        assert summary.match == 0

    def test_indel_call_vs_real_no_call_is_filled_nocall(self) -> None:
        """``II`` (indel call) vs ``--`` (real no-call) → ``filled_nocall`` (S1 wins).

        Indel codes are CALLS at the merge boundary, distinct from the
        ``--``/``??``/``00`` no-call sentinels. The merge service must
        differentiate so an indel call doesn't get silently collapsed when
        the other source actually lacks a call. (Trait modules separately
        skip indels via ``is_no_call`` because they can't score them; that's
        a downstream concern, not a merge-time one.)
        """
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("II", "--")
        rows, summary = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.FLAG_ONLY,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert summary.filled_nocall == 1, (
            f"II vs --: expected filled_nocall (S1's II is a real call), "
            f"got summary={summary.to_dict()}"
        )
        assert rows[0].source == "S1"
        assert rows[0].genotype == "II"
        assert rows[0].concordance == "filled_nocall"

    def test_prefer_23andme_keeps_s1_indel_at_discordant(self) -> None:
        """At an indel discordance, ``prefer_23andme`` keeps S1 and parks S2."""
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("II", "DI")
        rows, _ = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.PREFER_23ANDME,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert rows[0].genotype == "II"
        assert rows[0].discordant_alt_genotype == "S2=DI"

    def test_prefer_ancestrydna_keeps_s2_indel_at_discordant(self) -> None:
        """Symmetric — ``prefer_ancestrydna`` keeps S2 and parks S1."""
        from backend.services.sample_merge import _apply_semantics

        s1, s2 = self._two_sided("II", "DI")
        rows, _ = _apply_semantics(
            s1,
            s2,
            strategy=MergeStrategy.PREFER_ANCESTRYDNA,
            rsids_in_bundle=set(),
            s1_vendor="23andme",
            s2_vendor="ancestrydna",
        )
        assert rows[0].genotype == "DI"
        assert rows[0].discordant_alt_genotype == "S1=II"


# ── Step 77 / MRG-08a — Re-merge + hash invariants ──────────────────────
#
# Plan §10.5 step 5 locks ``samples.file_hash`` to
# ``SHA-256(S1.file_hash ‖ S2.file_hash ‖ strategy ‖ SAMPLE_SCHEMA_VERSION)``.
# ``TestFileHashRecipe`` already locks the recipe at the unit level
# (direct ``_compute_file_hash`` calls). The class below locks the same
# contract at the integration level by driving the live ``merge_samples``
# round-trip twice per assertion: regenerating a merged sample (same
# individual, same sources in the same order, same strategy) preserves
# provenance traceability and produces an identical ``samples.file_hash``;
# changing the strategy produces a distinct hash; swapping the source
# order produces a distinct hash (locks Plan §10.5's
# "order-sensitive on purpose" hashing contract).
#
# Each re-merge legitimately allocates a fresh ``samples`` row (Plan
# §10.5: "the new merged sample has a distinct identity in `samples`")
# and a fresh per-sample DB — only the deterministic ``file_hash`` should
# carry across. The "preserves provenance traceability" leg asserts that
# both per-sample DBs' ``merge_provenance`` rows hold the same strategy,
# the same ``source_sample_ids`` order, the same ``source_file_hashes``
# order, and the same ``concordance_summary``.


class TestReMergeHashInvariants:
    """Plan §15.1 MRG-08a — re-merge round-trip locks ``file_hash`` invariants."""

    @pytest.fixture
    def merged_setup(
        self,
        merge_registry: DBRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[DBRegistry, int, int, int]:
        """Two source samples linked to one individual — the canonical re-merge harness.

        Mirrors :class:`TestHappyPath`'s ``merged_setup`` so the assertions
        in this class consume the same ``S1_VARIANTS`` / ``S2_VARIANTS``
        canonical bucket coverage. The merge call's enqueue is stubbed
        with :func:`_noop_annotation_enqueue` so each re-merge in the
        same test exercises the deterministic write path without firing
        the annotation pipeline.
        """
        _seed_installed_vep_bundle(merge_registry, "v2.0.0")
        _noop_annotation_enqueue(monkeypatch)
        individual_id = _create_individual(merge_registry, "Re-merge Subject")
        s1_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="re_merge_23andme.txt",
            file_format="23andme_v5",
            file_hash="hash_s1",
            variants=S1_VARIANTS,
        )
        s2_id = _create_source_sample(
            merge_registry,
            individual_id=individual_id,
            name="re_merge_ancestrydna.txt",
            file_format="ancestrydna_v2.0",
            file_hash="hash_s2",
            variants=S2_VARIANTS,
        )
        return merge_registry, individual_id, s1_id, s2_id

    @staticmethod
    def _read_sample_file_hash(registry: DBRegistry, sample_id: int) -> str:
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(samples.c.file_hash).where(samples.c.id == sample_id)
            ).fetchone()
        assert row is not None
        return row.file_hash

    def test_same_sources_same_strategy_yield_identical_file_hash(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        """Plan §10.5 step 5: deterministic on ``(S1, S2, strategy, schema_version)``.

        Calling :func:`merge_samples` twice with identical arguments must
        produce two ``samples`` rows whose ``file_hash`` matches byte-for-
        byte AND whose ``merge_provenance`` rows carry the same strategy,
        source-id order, source-hash order, and concordance summary.
        """
        registry, individual_id, s1_id, s2_id = merged_setup
        first_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Re-merge attempt 1",
        )
        second_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Re-merge attempt 2",
        )
        # Each re-merge legitimately allocates a fresh samples row (Plan
        # §10.5: "the new merged sample has a distinct identity in
        # `samples`") — the determinism contract is about file_hash, not
        # the row identity.
        assert first_id != second_id

        first_hash = self._read_sample_file_hash(registry, first_id)
        second_hash = self._read_sample_file_hash(registry, second_id)
        assert first_hash == second_hash
        # Sanity: same inputs to the recipe must produce the same hash
        # the unit-level _compute_file_hash test locked.
        assert first_hash == _compute_file_hash("hash_s1", "hash_s2", MergeStrategy.FLAG_ONLY)

        # Provenance traceability — both per-sample DBs carry identical
        # merge_provenance rows.
        first_prov = _read_merge_provenance(registry, first_id)
        second_prov = _read_merge_provenance(registry, second_id)
        assert first_prov.strategy == second_prov.strategy == "flag_only"
        assert (
            json.loads(first_prov.source_sample_ids)
            == json.loads(second_prov.source_sample_ids)
            == [s1_id, s2_id]
        )
        assert (
            json.loads(first_prov.source_file_hashes)
            == json.loads(second_prov.source_file_hashes)
            == ["hash_s1", "hash_s2"]
        )
        assert json.loads(first_prov.concordance_summary) == json.loads(
            second_prov.concordance_summary
        )

    def test_changing_strategy_changes_file_hash(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        """Plan §10.5 step 5: ``strategy`` is part of the SHA-256 payload.

        Re-merging the same sources under each of the three Plan §10.3
        strategies must yield three distinct ``samples.file_hash``
        values so the wizard's "regenerate under a different strategy"
        flow doesn't collide with the existing merged sample's identity.
        """
        registry, individual_id, s1_id, s2_id = merged_setup
        hashes: dict[MergeStrategy, str] = {}
        for strategy in (
            MergeStrategy.FLAG_ONLY,
            MergeStrategy.PREFER_23ANDME,
            MergeStrategy.PREFER_ANCESTRYDNA,
        ):
            new_id = merge_samples(
                registry,
                source_sample_ids=[s1_id, s2_id],
                individual_id=individual_id,
                strategy=strategy,
                display_name=f"Re-merge {strategy.value}",
            )
            hashes[strategy] = self._read_sample_file_hash(registry, new_id)

        # All three hashes distinct — the recipe's strategy field is
        # load-bearing.
        assert len(set(hashes.values())) == 3, (
            f"strategy changes must produce distinct hashes; got {hashes}"
        )

    def test_swapping_source_order_changes_file_hash(
        self, merged_setup: tuple[DBRegistry, int, int, int]
    ) -> None:
        """Plan §10.5 step 5: concatenation order is order-sensitive on purpose.

        ``_compute_file_hash`` concatenates ``S1.file_hash`` and
        ``S2.file_hash`` left-to-right. Swapping the user-supplied
        ``source_sample_ids`` order therefore changes the SHA-256 input
        and produces a distinct hash. This locks the rsid-collapse
        tiebreaker invariant from §10.2 step 2 (S1 wins when neither
        rsid is in the bundle) — merging ``[A, B]`` and ``[B, A]`` yield
        different merged content, so they must yield different identities.
        """
        registry, individual_id, s1_id, s2_id = merged_setup
        forward_id = merge_samples(
            registry,
            source_sample_ids=[s1_id, s2_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Forward order",
        )
        reverse_id = merge_samples(
            registry,
            source_sample_ids=[s2_id, s1_id],
            individual_id=individual_id,
            strategy=MergeStrategy.FLAG_ONLY,
            display_name="Reverse order",
        )
        forward_hash = self._read_sample_file_hash(registry, forward_id)
        reverse_hash = self._read_sample_file_hash(registry, reverse_id)
        assert forward_hash != reverse_hash, (
            "swapping source order must produce a distinct file_hash "
            "(Plan §10.5: 'order-sensitive on purpose')"
        )
        # Each direction's provenance reflects the order it was called
        # with — locks the contract that the recorded source_sample_ids
        # mirrors the request, not a canonicalized sort.
        forward_prov = _read_merge_provenance(registry, forward_id)
        reverse_prov = _read_merge_provenance(registry, reverse_id)
        assert json.loads(forward_prov.source_sample_ids) == [s1_id, s2_id]
        assert json.loads(reverse_prov.source_sample_ids) == [s2_id, s1_id]
        # And source_file_hashes mirrors the order: the swap produces a
        # different SHA-256 input string, which is what changes the hash.
        assert json.loads(forward_prov.source_file_hashes) == ["hash_s1", "hash_s2"]
        assert json.loads(reverse_prov.source_file_hashes) == ["hash_s2", "hash_s1"]
