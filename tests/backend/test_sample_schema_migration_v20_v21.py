"""Tests for the v20 → v21 legacy CYP2C9/phenytoin alert quarantine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db.connection import DBRegistry
from backend.db.sample_schema import (
    CYP2C9_PHENYTOIN_BUNDLED_GUIDANCE_VERSION,
    CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
    CYP2C9_PHENYTOIN_REANALYSIS_REASON,
    CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
    SAMPLE_SCHEMA_VERSION,
    create_sample_tables,
    ensure_sample_schema_current,
)
from backend.db.tables import (
    annotation_state,
    database_versions,
    findings,
    reannotation_prompts,
    reference_metadata,
    sample_metadata_table,
    samples,
)
from backend.db.update_manager import (
    create_version_staleness_prompt,
    publish_cyp2c9_phenytoin_reanalysis_prompt,
)


def _finding(
    *,
    module: str = "pharmacogenomics",
    category: str = "prescribing_alert",
    gene_symbol: str = "CYP2C9",
    drug: str = "phenytoin",
) -> dict[str, object]:
    return {
        "module": module,
        "category": category,
        "gene_symbol": gene_symbol,
        "drug": drug,
        "evidence_level": 4,
        "finding_text": f"{gene_symbol}/{drug} legacy finding",
    }


def _diff_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "gene_symbol": "CYP2C9",
        "drug": "phenytoin",
        "finding_text": "legacy phenytoin recommendation",
    }
    entry.update(overrides)
    return entry


def test_v21_quarantines_only_cyp2c9_phenytoin_alert_and_diff_identity(
    sample_engine: sa.Engine,
) -> None:
    near_misses = [
        _finding(drug="warfarin"),
        _finding(gene_symbol="CYP2D6"),
        _finding(category="phenotype_summary"),
        _finding(module="medication_review"),
    ]
    diff = {
        "schema_version": 1,
        "before_releases": {"cpic": "v1.58.0"},
        "after_releases": {"cpic": "v1.58.0"},
        "release_deltas": [],
        "generated_at": "2026-07-16T00:00:00Z",
        "dismissed": False,
        "changed": [_diff_entry(), _diff_entry(drug="warfarin")],
        "added": [_diff_entry(drug="PHENYTOIN"), _diff_entry(gene_symbol="CYP2D6")],
        "removed": [_diff_entry(), _diff_entry(category="phenotype_summary")],
        "counts": {"changed": 2, "added": 2, "removed": 2},
        "future_metadata": {"preserve": True},
    }
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert(),
            [
                _finding(),
                _finding(drug="PHENYTOIN"),
                *near_misses,
            ],
        )
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": json.dumps(diff)},
        )
        conn.execute(sa.text("PRAGMA user_version = 20"))

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        remaining = conn.execute(
            sa.select(
                findings.c.module,
                findings.c.category,
                findings.c.gene_symbol,
                findings.c.drug,
            ).order_by(findings.c.id)
        ).fetchall()
        stored_diff = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "last_finding_diff_json"
                )
            ).scalar_one()
        )
        reanalysis_marker = json.loads(
            conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                )
            ).scalar_one()
        )
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()

    assert version == SAMPLE_SCHEMA_VERSION == 21
    assert remaining == [
        ("pharmacogenomics", "prescribing_alert", "CYP2C9", "warfarin"),
        ("pharmacogenomics", "prescribing_alert", "CYP2D6", "phenytoin"),
        ("pharmacogenomics", "phenotype_summary", "CYP2C9", "phenytoin"),
        ("medication_review", "prescribing_alert", "CYP2C9", "phenytoin"),
    ]
    assert stored_diff == {
        **{
            key: value
            for key, value in diff.items()
            if key not in {"changed", "added", "removed", "counts"}
        },
        "changed": [_diff_entry(drug="warfarin")],
        "added": [_diff_entry(gene_symbol="CYP2D6")],
        "removed": [_diff_entry(category="phenotype_summary")],
        "counts": {"changed": 1, "added": 1, "removed": 1},
    }
    assert reanalysis_marker == {
        "database": "cpic",
        "prompted": False,
        "reason": CYP2C9_PHENYTOIN_REANALYSIS_REASON,
        "sample_schema_version": 21,
    }
    assert ensure_sample_schema_current(sample_engine) is False


def test_registry_open_publishes_reanalysis_prompt_without_reference_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    sample_path = settings.data_dir / "samples" / "sample_1.db"
    sample_path.parent.mkdir(parents=True)

    sample_engine = sa.create_engine(f"sqlite:///{sample_path}")
    create_sample_tables(sample_engine)
    created_at = datetime.now(UTC)
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), _finding())
        conn.execute(
            sample_metadata_table.insert(),
            {
                "id": 1,
                "name": "Legacy sample",
                "file_format": "23andme_v5",
                "file_hash": "legacy-hash",
                "created_at": created_at,
            },
        )
        conn.execute(sa.text("PRAGMA user_version = 20"))
    sample_engine.dispose()

    registry = DBRegistry(settings)
    reference_metadata.create_all(registry.reference_engine)
    with registry.reference_engine.begin() as conn:
        conn.execute(
            database_versions.insert(),
            {"db_name": "cpic", "version": "v1.58.0"},
        )
        conn.execute(
            reannotation_prompts.insert(),
            {
                "sample_id": 1,
                "db_name": "reference_data",
                "db_version": "multiple",
                "prompt_type": "version_staleness",
                "stale_databases": json.dumps(
                    [
                        {
                            "db_name": "gnomad",
                            "recorded_version": "r2.1",
                            "current_version": "r4.1",
                        }
                    ]
                ),
                "dismissed": False,
            },
        )

    try:
        migrated_engine = registry.get_sample_engine(sample_path)
        with migrated_engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 0
            pending_marker = json.loads(
                conn.execute(
                    sa.select(annotation_state.c.value).where(
                        annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                    )
                ).scalar_one()
            )
        assert pending_marker["prompted"] is False

        with registry.reference_engine.begin() as conn:
            conn.execute(
                samples.insert(),
                {
                    "id": 1,
                    # A central rename can commit before the best-effort local
                    # metadata mirror; mutable display names are not identity.
                    "name": "Renamed central sample",
                    # Exercise the bounded legacy path fallback rather than an
                    # exact current-format registry lookup.
                    "db_path": "legacy/../samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "legacy-hash",
                    "created_at": created_at,
                },
            )

        # Force the exact lost-update ordering: DBRegistry has already read the
        # old gnomAD list, then a routine precheck publishes a newer VEP list
        # immediately before DBRegistry adds the correction.
        from backend.db import update_manager as update_manager_module

        original_publish_prompt = update_manager_module.publish_cyp2c9_phenytoin_reanalysis_prompt
        interleaved = False

        def publish_after_interleaved_precheck(
            engine: sa.Engine,
            *,
            sample_id: int,
            expected_db_path: str,
            expected_file_format: str | None,
            expected_file_hash: str | None,
            expected_created_at: datetime | None,
            sample_db_path: Path,
            expected_file_fingerprint: tuple[int, int, int, int],
        ) -> bool:
            nonlocal interleaved
            if not interleaved:
                interleaved = True
                create_version_staleness_prompt(
                    engine,
                    sample_id=sample_id,
                    stale_databases=[
                        {
                            "db_name": "vep_bundle",
                            "recorded_version": "v110",
                            "current_version": "v113",
                        }
                    ],
                )
            return original_publish_prompt(
                engine,
                sample_id=sample_id,
                expected_db_path=expected_db_path,
                expected_file_format=expected_file_format,
                expected_file_hash=expected_file_hash,
                expected_created_at=expected_created_at,
                sample_db_path=sample_db_path,
                expected_file_fingerprint=expected_file_fingerprint,
            )

        monkeypatch.setattr(
            update_manager_module,
            "publish_cyp2c9_phenytoin_reanalysis_prompt",
            publish_after_interleaved_precheck,
        )
        assert registry.get_sample_engine(sample_path) is migrated_engine
        assert interleaved is True
        with migrated_engine.connect() as conn:
            marker = json.loads(
                conn.execute(
                    sa.select(annotation_state.c.value).where(
                        annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                    )
                ).scalar_one()
            )
        with registry.reference_engine.connect() as conn:
            prompt = conn.execute(sa.select(reannotation_prompts)).mappings().one()

        # A later ordinary version precheck replaces resolved database entries
        # but cannot erase the still-active activity-score correction.
        create_version_staleness_prompt(
            registry.reference_engine,
            sample_id=1,
            stale_databases=[
                {
                    "db_name": "clinvar",
                    "recorded_version": "2026-01",
                    "current_version": "2026-07",
                }
            ],
            preserve_active_cyp2c9_phenytoin_correction=True,
        )
        with registry.reference_engine.connect() as conn:
            prompt_after_precheck = (
                conn.execute(
                    sa.select(reannotation_prompts).where(
                        reannotation_prompts.c.dismissed == sa.false()
                    )
                )
                .mappings()
                .one()
            )
        with registry.reference_engine.begin() as conn:
            conn.execute(reannotation_prompts.update().values(dismissed=True))

        registry.dispose_all()
        registry = DBRegistry(settings)
        registry.get_sample_engine(sample_path)
        with registry.reference_engine.connect() as conn:
            prompt_count_after_restart = conn.execute(
                sa.select(sa.func.count()).select_from(reannotation_prompts)
            ).scalar_one()
            active_prompt_count_after_restart = conn.execute(
                sa.select(sa.func.count())
                .select_from(reannotation_prompts)
                .where(reannotation_prompts.c.dismissed == sa.false())
            ).scalar_one()

        # Sample backups retain annotation_state but exclude runtime prompt rows.
        # A restored prompted marker must therefore republish when its matching
        # central record is absent, while the dismissed record above was honored.
        with registry.reference_engine.begin() as conn:
            conn.execute(sa.delete(reannotation_prompts))
            conn.execute(sa.delete(database_versions).where(database_versions.c.db_name == "cpic"))
        registry.dispose_all()
        registry = DBRegistry(settings)
        registry.get_sample_engine(sample_path)
        with registry.reference_engine.connect() as conn:
            republished_prompt = conn.execute(sa.select(reannotation_prompts)).mappings().one()
    finally:
        registry.dispose_all()

    assert marker == {
        "database": "cpic",
        "prompted": True,
        "reason": CYP2C9_PHENYTOIN_REANALYSIS_REASON,
        "sample_schema_version": 21,
    }
    assert prompt["sample_id"] == 1
    assert prompt["prompt_type"] == "version_staleness"
    assert prompt_count_after_restart == 1
    assert active_prompt_count_after_restart == 0
    assert republished_prompt["dismissed"] is False
    republished_stale = json.loads(republished_prompt["stale_databases"])
    initial_stale = json.loads(prompt["stale_databases"])
    precheck_stale = json.loads(prompt_after_precheck["stale_databases"])
    correction_identities = []
    for stale_databases in (republished_stale, initial_stale, precheck_stale):
        correction = next(item for item in stale_databases if item["db_name"] == "cpic")
        correction_identities.append(correction.pop("sample_identity_sha256"))
    assert len(set(correction_identities)) == 1
    assert len(correction_identities[0]) == 64
    assert republished_stale == [
        {
            "db_name": "cpic",
            "recorded_version": CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
            "current_version": CYP2C9_PHENYTOIN_BUNDLED_GUIDANCE_VERSION,
        }
    ]
    assert initial_stale == [
        {
            "db_name": "vep_bundle",
            "recorded_version": "v110",
            "current_version": "v113",
        },
        {
            "db_name": "cpic",
            "recorded_version": CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
            "current_version": "v1.58.0",
        },
    ]
    assert precheck_stale == [
        {
            "db_name": "clinvar",
            "recorded_version": "2026-01",
            "current_version": "2026-07",
        },
        {
            "db_name": "cpic",
            "recorded_version": CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
            "current_version": "v1.58.0",
        },
    ]


def test_annotation_marker_delete_race_retracts_only_identity_bound_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    sample_path = settings.data_dir / "samples" / "sample_1.db"
    sample_path.parent.mkdir(parents=True)
    created_at = datetime.now(UTC)
    external_sample_engine = sa.create_engine(f"sqlite:///{sample_path}")
    create_sample_tables(external_sample_engine)
    with external_sample_engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert(),
            {
                "id": 1,
                "name": "Race sample",
                "file_format": "23andme_v5",
                "file_hash": "race-hash",
                "created_at": created_at,
            },
        )
        conn.execute(
            annotation_state.insert(),
            {
                "key": CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
                "value": json.dumps(
                    {
                        "database": "cpic",
                        "prompted": False,
                        "reason": CYP2C9_PHENYTOIN_REANALYSIS_REASON,
                        "sample_schema_version": 21,
                    }
                ),
            },
        )

    registry = DBRegistry(settings)
    reference_metadata.create_all(registry.reference_engine)
    ordinary_staleness = [
        {
            "db_name": "gnomad",
            "recorded_version": "r2.1",
            "current_version": "r4.1",
        }
    ]
    with registry.reference_engine.begin() as conn:
        conn.execute(
            samples.insert(),
            {
                "id": 1,
                "name": "Race sample",
                "db_path": "samples/sample_1.db",
                "file_format": "23andme_v5",
                "file_hash": "race-hash",
                "created_at": created_at,
            },
        )
        conn.execute(
            reannotation_prompts.insert(),
            {
                "sample_id": 1,
                "db_name": "reference_data",
                "db_version": "multiple",
                "prompt_type": "version_staleness",
                "stale_databases": json.dumps(ordinary_staleness),
                "dismissed": False,
            },
        )

    from backend.db import update_manager as update_manager_module

    original_publish = update_manager_module.publish_cyp2c9_phenytoin_reanalysis_prompt

    def publish_then_complete_annotation(*args, **kwargs) -> bool:
        published = original_publish(*args, **kwargs)
        with external_sample_engine.begin() as conn:
            conn.execute(
                annotation_state.delete().where(
                    annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                )
            )
        return published

    monkeypatch.setattr(
        update_manager_module,
        "publish_cyp2c9_phenytoin_reanalysis_prompt",
        publish_then_complete_annotation,
    )

    key = str(sample_path)
    try:
        cached_engine = registry.get_sample_engine(sample_path)
        with cached_engine.connect() as conn:
            marker_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(annotation_state)
                .where(annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY)
            ).scalar_one()
        with registry.reference_engine.connect() as conn:
            prompts = conn.execute(sa.select(reannotation_prompts)).mappings().all()

        assert marker_count == 0
        assert len(prompts) == 1
        assert json.loads(prompts[0]["stale_databases"]) == ordinary_staleness
        assert key not in registry._sample_prompt_sync_complete

        # The next open observes the authoritative missing marker, confirms no
        # identity-bound correction remains, and may then cache completion.
        assert registry.get_sample_engine(sample_path) is cached_engine
        assert registry._sample_prompt_sync_complete.get(key) is cached_engine
    finally:
        registry.dispose_all()
        external_sample_engine.dispose()


def test_v21_tolerates_malformed_finding_diff_json(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        conn.execute(
            annotation_state.insert(),
            {"key": "last_finding_diff_json", "value": "{not-json"},
        )
        conn.execute(sa.text("PRAGMA user_version = 20"))

    assert ensure_sample_schema_current(sample_engine) is False
    with sample_engine.connect() as conn:
        value = conn.execute(
            sa.select(annotation_state.c.value).where(
                annotation_state.c.key == "last_finding_diff_json"
            )
        ).scalar_one()
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()
    assert value == "{not-json"
    assert version == 21


def test_prompt_publication_rejects_sample_deleted_after_registry_lookup(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    sample_path = settings.data_dir / "samples" / "sample_1.db"
    sample_path.parent.mkdir(parents=True)
    sample_engine = sa.create_engine(f"sqlite:///{sample_path}")
    create_sample_tables(sample_engine)
    created_at = datetime.now(UTC)
    with sample_engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert(),
            {
                "id": 1,
                "name": "Deleted sample",
                "file_format": "23andme_v5",
                "file_hash": "deleted-hash",
                "created_at": created_at,
            },
        )
    sample_engine.dispose()
    stat = sample_path.stat()
    expected_fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    registry = DBRegistry(settings)
    reference_metadata.create_all(registry.reference_engine)
    with registry.reference_engine.begin() as conn:
        conn.execute(
            samples.insert(),
            {
                "id": 1,
                "name": "Deleted sample",
                "db_path": "samples/sample_1.db",
                "file_format": "23andme_v5",
                "file_hash": "deleted-hash",
                "created_at": created_at,
            },
        )
    # Simulate deletion committing after DBRegistry's initial path lookup but
    # before the publisher acquires its reference-DB write lock.
    sample_path.unlink()
    with registry.reference_engine.begin() as conn:
        conn.execute(sa.delete(samples).where(samples.c.id == 1))

    try:
        published = publish_cyp2c9_phenytoin_reanalysis_prompt(
            registry.reference_engine,
            sample_id=1,
            expected_db_path="samples/sample_1.db",
            expected_file_format="23andme_v5",
            expected_file_hash="deleted-hash",
            expected_created_at=created_at,
            sample_db_path=sample_path,
            expected_file_fingerprint=expected_fingerprint,
        )
        with registry.reference_engine.connect() as conn:
            prompt_count = conn.execute(
                sa.select(sa.func.count()).select_from(reannotation_prompts)
            ).scalar_one()
    finally:
        registry.dispose_all()

    assert published is False
    assert prompt_count == 0


def test_prompt_publication_rejects_reused_path_with_old_local_identity(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    sample_path = settings.data_dir / "samples" / "sample_1.db"
    sample_path.parent.mkdir(parents=True)
    replacement_engine = sa.create_engine(f"sqlite:///{sample_path}")
    create_sample_tables(replacement_engine)
    replacement_engine.dispose()
    stat = sample_path.stat()
    replacement_fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    old_created_at = datetime(2026, 1, 1, tzinfo=UTC)
    replacement_created_at = datetime(2026, 7, 16, tzinfo=UTC)

    registry = DBRegistry(settings)
    reference_metadata.create_all(registry.reference_engine)
    with registry.reference_engine.begin() as conn:
        conn.execute(
            samples.insert(),
            {
                "id": 1,
                "name": "Replacement sample",
                "db_path": "samples/sample_1.db",
                "file_format": "ancestrydna_v2",
                "file_hash": "replacement-hash",
                "created_at": replacement_created_at,
            },
        )

    try:
        published = publish_cyp2c9_phenytoin_reanalysis_prompt(
            registry.reference_engine,
            sample_id=1,
            expected_db_path="samples/sample_1.db",
            expected_file_format="23andme_v5",
            expected_file_hash="deleted-hash",
            expected_created_at=old_created_at,
            sample_db_path=sample_path,
            # Models a replacement completed after an old cached engine read
            # its marker but before it captured the current path fingerprint.
            expected_file_fingerprint=replacement_fingerprint,
        )
        with registry.reference_engine.connect() as conn:
            prompt_count = conn.execute(
                sa.select(sa.func.count()).select_from(reannotation_prompts)
            ).scalar_one()
    finally:
        registry.dispose_all()

    assert published is False
    assert prompt_count == 0


def test_unbound_dismissal_cannot_acknowledge_reused_sample_id(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    sample_path = settings.data_dir / "samples" / "sample_1.db"
    sample_path.parent.mkdir(parents=True)
    sample_engine = sa.create_engine(f"sqlite:///{sample_path}")
    create_sample_tables(sample_engine)
    sample_engine.dispose()
    stat = sample_path.stat()
    fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    created_at = datetime(2026, 7, 16)

    registry = DBRegistry(settings)
    reference_metadata.create_all(registry.reference_engine)
    with registry.reference_engine.begin() as conn:
        conn.execute(
            samples.insert(),
            {
                "id": 1,
                "name": "Replacement sample",
                "db_path": "samples/sample_1.db",
                "file_format": "23andme_v5",
                "file_hash": "replacement-hash",
                "created_at": created_at,
            },
        )
        conn.execute(
            reannotation_prompts.insert(),
            {
                "sample_id": 1,
                "db_name": "reference_data",
                "db_version": "multiple",
                "prompt_type": "version_staleness",
                "stale_databases": json.dumps(
                    [
                        {
                            "db_name": "cpic",
                            "recorded_version": CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION,
                            "current_version": "v1.58.0",
                        }
                    ]
                ),
                "dismissed": True,
                "created_at": datetime(2026, 1, 1),
            },
        )

    try:
        published = publish_cyp2c9_phenytoin_reanalysis_prompt(
            registry.reference_engine,
            sample_id=1,
            expected_db_path="samples/sample_1.db",
            expected_file_format="23andme_v5",
            expected_file_hash="replacement-hash",
            expected_created_at=created_at,
            sample_db_path=sample_path,
            expected_file_fingerprint=fingerprint,
        )
        with registry.reference_engine.connect() as conn:
            prompt_rows = (
                conn.execute(sa.select(reannotation_prompts).order_by(reannotation_prompts.c.id))
                .mappings()
                .all()
            )
    finally:
        registry.dispose_all()

    assert published is True
    assert len(prompt_rows) == 2
    assert prompt_rows[0]["dismissed"] is True
    assert prompt_rows[1]["dismissed"] is False
    active_correction = json.loads(prompt_rows[1]["stale_databases"])[0]
    assert active_correction["recorded_version"] == CYP2C9_PHENYTOIN_LEGACY_GUIDANCE_VERSION
    assert len(active_correction["sample_identity_sha256"]) == 64


def test_disposed_engine_cannot_mark_reused_path_prompt_sync_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    sample_path = settings.data_dir / "samples" / "sample_1.db"
    sample_path.parent.mkdir(parents=True)
    registry = DBRegistry(settings)
    key = str(sample_path)

    def dispose_during_sync(_engine: sa.Engine, db_path: str | Path) -> bool:
        registry.dispose_sample_engine(db_path)
        return True

    monkeypatch.setattr(
        registry,
        "_sync_cyp2c9_phenytoin_reanalysis_prompt",
        dispose_during_sync,
    )
    old_engine = registry.get_sample_engine(sample_path)

    assert key not in registry._sample_engines
    assert registry._sample_prompt_sync_complete.get(key) is old_engine

    sync_calls = 0

    def record_replacement_sync(_engine: sa.Engine, _db_path: str | Path) -> bool:
        nonlocal sync_calls
        sync_calls += 1
        return True

    monkeypatch.setattr(
        registry,
        "_sync_cyp2c9_phenytoin_reanalysis_prompt",
        record_replacement_sync,
    )
    replacement_engine = registry.get_sample_engine(sample_path)
    try:
        assert replacement_engine is not old_engine
        assert sync_calls == 1
        assert registry._sample_prompt_sync_complete.get(key) is replacement_engine
    finally:
        registry.dispose_all()


def test_v21_marker_failure_rolls_back_legacy_finding_deletion(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'marker-failure.db'}")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(findings.insert(), _finding())
        conn.execute(
            sa.text(
                "CREATE TRIGGER reject_phenytoin_marker "
                "BEFORE INSERT ON annotation_state "
                f"WHEN NEW.key = '{CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY}' "
                "BEGIN SELECT RAISE(ABORT, 'marker rejected'); END"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 20"))

    with pytest.raises(sa.exc.DatabaseError, match="marker rejected"):
        ensure_sample_schema_current(engine)

    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 1
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == 20


def test_restore_creates_annotation_state_before_v21_content_migration(tmp_path: Path) -> None:
    from backend.api.routes.setup import _upgrade_restored_sample_db

    sample_path = tmp_path / "restored-sample.db"
    engine = sa.create_engine(f"sqlite:///{sample_path}")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(findings.insert(), _finding())
        conn.execute(sa.text("DROP TABLE annotation_state"))
        conn.execute(sa.text("PRAGMA user_version = 20"))
    engine.dispose()

    _upgrade_restored_sample_db(sample_path)

    restored = sa.create_engine(f"sqlite:///{sample_path}")
    try:
        with restored.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar_one() == 0
            marker = json.loads(
                conn.execute(
                    sa.select(annotation_state.c.value).where(
                        annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                    )
                ).scalar_one()
            )
    finally:
        restored.dispose()

    assert marker["prompted"] is False
    assert marker["reason"] == CYP2C9_PHENYTOIN_REANALYSIS_REASON


def test_v21_tolerates_partial_legacy_findings_table(tmp_path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE findings ("
                "id INTEGER PRIMARY KEY, module TEXT, category TEXT, "
                "evidence_level INTEGER, related_module TEXT)"
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 20"))

    assert ensure_sample_schema_current(engine) is True
    with engine.connect() as conn:
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == 21
