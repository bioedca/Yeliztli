"""Database connection management for Yeliztli.

Provides the DBRegistry singleton that manages connections to all SQLite
databases (reference + per-sample). Reference DB connections are long-lived
and read-only. Sample DB connections are created per-request.

Usage::

    from backend.db.connection import get_registry

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        result = conn.execute(select(clinvar_variants).where(...))
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.config import Settings, get_settings
from backend.db.sqlite_engine import SQLiteSynchronousMode, make_sqlite_engine

logger = structlog.get_logger(__name__)


class DBRegistry:
    """Singleton managing SQLite engine connections for all databases.

    Reference DB engines are created once at startup. Sample DB engines
    are created on demand and cached.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sample_engines: dict[str, sa.Engine] = {}
        self._sample_prompt_sync_complete: dict[str, sa.Engine] = {}

        # Reference DB (shared, long-lived)
        self.reference_engine = self._create_engine(
            settings.reference_db_path,
            wal=settings.wal_mode,
            synchronous=self._wal_synchronous,
        )

        # Large reference DBs (opened lazily on first access)
        self._vep_engine: sa.Engine | None = None
        self._vep_fingerprint: tuple[int, int, int, int] | None = None
        self._gnomad_engine: sa.Engine | None = None
        self._dbnsfp_engine: sa.Engine | None = None
        self._alphamissense_engine: sa.Engine | None = None
        self._gtex_eqtl_engine: sa.Engine | None = None
        self._spliceai_engine: sa.Engine | None = None
        self._encode_ccres_engine: sa.Engine | None = None
        self._encode_ccres_fingerprint: tuple[int, int, int, int] | None = None

    @property
    def _wal_synchronous(self) -> SQLiteSynchronousMode | None:
        """Use SQLite's WAL-recommended performance mode for reference engines only."""
        return "NORMAL" if self._settings.wal_mode else None

    @property
    def settings(self) -> Settings:
        """Public accessor for the registry's Settings instance."""
        return self._settings

    @staticmethod
    def _create_engine(
        db_path: Path,
        *,
        wal: bool = True,
        synchronous: SQLiteSynchronousMode | None = None,
        read_optimized: bool = False,
    ) -> sa.Engine:
        """Create a SQLAlchemy engine for a SQLite database.

        Args:
            db_path: Path to the SQLite file.
            wal: Whether to enable WAL journal mode.
            synchronous: Optional SQLite synchronous mode. Reference and
                rebuildable WAL engines opt into ``NORMAL``; per-sample engines
                leave this unset to keep SQLite's default ``FULL`` durability.
            read_optimized: Whether to apply aggressive read-performance
                PRAGMAs (larger cache, mmap, temp_store in memory).
                Use for large read-only reference databases.

        Returns:
            Configured SQLAlchemy Engine.
        """
        # Delegate to the shared factory so DBRegistry's engines and the
        # standalone reference/build engines in update_manager /
        # database_registry apply an identical connect-time PRAGMA block
        # (busy_timeout in particular) and cannot drift.
        return make_sqlite_engine(
            db_path,
            wal=wal,
            synchronous=synchronous,
            read_optimized=read_optimized,
        )

    @staticmethod
    def _file_fingerprint(db_path: Path) -> tuple[int, int, int, int] | None:
        try:
            stat = db_path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _sync_cyp2c9_phenytoin_reanalysis_prompt(
        self,
        sample_engine: sa.Engine,
        sample_db_path: str | Path,
    ) -> bool:
        """Publish the v21 sample marker as one durable user-facing prompt.

        The marker and unsafe-finding deletion commit atomically in the sample
        DB. Prompt publication crosses into ``reference.db``, so the marker is
        retained as ``prompted=false`` until this best-effort synchronizer
        succeeds; retries upsert the same undismissed prompt.

        Returns ``True`` when no retry is needed and ``False`` when publication
        was deferred by transient or incomplete reference state.
        """
        from backend.db.sample_schema import (
            CYP2C9_PHENYTOIN_REANALYSIS_REASON,
            CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY,
        )
        from backend.db.tables import annotation_state, sample_metadata_table, samples

        target_path = Path(sample_db_path).resolve()
        target_fingerprint = self._file_fingerprint(target_path)
        if target_fingerprint is None:
            return False

        sample_columns = (
            samples.c.id,
            samples.c.db_path,
            samples.c.file_format,
            samples.c.file_hash,
            samples.c.created_at,
        )
        path_candidates = {str(target_path), str(sample_db_path)}
        try:
            relative_path = target_path.relative_to(self._settings.data_dir.resolve())
        except ValueError:
            pass
        else:
            path_candidates.update(
                {
                    str(relative_path),
                    relative_path.as_posix(),
                    f"./{relative_path.as_posix()}",
                }
            )

        def matches_target_path(row) -> bool:
            registry_path = Path(row.db_path)
            if not registry_path.is_absolute():
                registry_path = self._settings.data_dir / registry_path
            return registry_path.resolve() == target_path

        try:
            with self.reference_engine.connect() as conn:
                sample_rows = conn.execute(
                    sa.select(*sample_columns).where(
                        samples.c.db_path.in_(sorted(path_candidates))
                    )
                ).fetchall()
                sample_row = next(
                    (row for row in sample_rows if matches_target_path(row)),
                    None,
                )
                if sample_row is None:
                    # Legacy manifests can contain odd relative/absolute path
                    # spellings. Keep the compatibility fallback bounded by
                    # filename and row count instead of scanning the registry.
                    legacy_rows = conn.execute(
                        sa.select(*sample_columns)
                        .where(
                            samples.c.db_path.endswith(
                                target_path.name,
                                autoescape=True,
                            )
                        )
                        .limit(128)
                    ).fetchall()
                    sample_row = next(
                        (row for row in legacy_rows if matches_target_path(row)),
                        None,
                    )
        except sa.exc.OperationalError as exc:
            logger.warning(
                "cyp2c9_phenytoin_reanalysis_prompt_deferred",
                reason="reference_db_unreadable",
                error=str(exc),
            )
            return False

        if sample_row is None:
            logger.warning(
                "cyp2c9_phenytoin_reanalysis_prompt_deferred",
                reason="sample_registry_row_missing",
                sample_db_path=str(target_path),
            )
            return False
        sample_id = sample_row.id

        # Bind the authoritative marker read to an immutable local identity.
        # Partial legacy databases can lack the metadata row; in that case the
        # central identity and path fingerprint captured *before* this read are
        # revalidated under the publisher's write lock.
        try:
            with sample_engine.connect() as conn:
                local_state = conn.execute(
                    sa.select(
                        sa.select(annotation_state.c.value)
                        .where(annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY)
                        .scalar_subquery()
                        .label("marker_value"),
                        sa.select(sample_metadata_table.c.id)
                        .where(sample_metadata_table.c.id == 1)
                        .scalar_subquery()
                        .label("metadata_id"),
                        sa.select(sample_metadata_table.c.file_format)
                        .where(sample_metadata_table.c.id == 1)
                        .scalar_subquery()
                        .label("file_format"),
                        sa.select(sample_metadata_table.c.file_hash)
                        .where(sample_metadata_table.c.id == 1)
                        .scalar_subquery()
                        .label("file_hash"),
                        sa.select(sample_metadata_table.c.created_at)
                        .where(sample_metadata_table.c.id == 1)
                        .scalar_subquery()
                        .label("created_at"),
                    )
                ).one()
        except sa.exc.OperationalError:
            return False
        marker_value = local_state.marker_value
        has_local_identity = local_state.metadata_id == 1
        expected_file_format = (
            local_state.file_format if has_local_identity else sample_row.file_format
        )
        expected_file_hash = local_state.file_hash if has_local_identity else sample_row.file_hash
        expected_created_at = (
            local_state.created_at if has_local_identity else sample_row.created_at
        )

        from backend.db.update_manager import (
            publish_cyp2c9_phenytoin_reanalysis_prompt,
            retract_cyp2c9_phenytoin_reanalysis_prompt,
        )

        def retract_prompt() -> bool:
            try:
                retract_cyp2c9_phenytoin_reanalysis_prompt(
                    self.reference_engine,
                    sample_id=sample_id,
                    expected_db_path=sample_row.db_path,
                    expected_file_format=expected_file_format,
                    expected_file_hash=expected_file_hash,
                    expected_created_at=expected_created_at,
                )
            except sa.exc.SQLAlchemyError as exc:
                logger.warning(
                    "cyp2c9_phenytoin_reanalysis_prompt_deferred",
                    reason="prompt_retraction_failed",
                    sample_id=sample_id,
                    error=str(exc),
                )
                return False
            return True

        # The sample marker is authoritative. A previous synchronizer may have
        # published the central correction immediately before a successful
        # annotation deleted the marker, so reconcile that identity-bound item
        # before considering this engine synchronized.
        if marker_value is None:
            return retract_prompt()

        prompted_marker = {
            "database": "cpic",
            "prompted": True,
            "reason": CYP2C9_PHENYTOIN_REANALYSIS_REASON,
            "sample_schema_version": 21,
        }

        def mark_prompted() -> bool:
            try:
                with sample_engine.begin() as conn:
                    result = conn.execute(
                        annotation_state.update()
                        .where(annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY)
                        .values(value=json.dumps(prompted_marker))
                    )
            except sa.exc.SQLAlchemyError as exc:
                logger.warning(
                    "cyp2c9_phenytoin_reanalysis_prompt_deferred",
                    reason="marker_update_failed",
                    sample_id=sample_id,
                    error=str(exc),
                )
                return False
            if result.rowcount != 1:
                if retract_prompt():
                    logger.info(
                        "cyp2c9_phenytoin_reanalysis_prompt_race_retracted",
                        sample_id=sample_id,
                    )
                return False
            return True

        try:
            publication_complete = publish_cyp2c9_phenytoin_reanalysis_prompt(
                self.reference_engine,
                sample_id=sample_id,
                expected_db_path=sample_row.db_path,
                expected_file_format=expected_file_format,
                expected_file_hash=expected_file_hash,
                expected_created_at=expected_created_at,
                sample_db_path=target_path,
                expected_file_fingerprint=target_fingerprint,
            )
        except sa.exc.SQLAlchemyError as exc:
            logger.warning(
                "cyp2c9_phenytoin_reanalysis_prompt_deferred",
                reason="prompt_publish_failed",
                sample_id=sample_id,
                error=str(exc),
            )
            return False
        if not publication_complete:
            logger.warning(
                "cyp2c9_phenytoin_reanalysis_prompt_deferred",
                reason="sample_identity_changed",
                sample_id=sample_id,
            )
            return False
        return mark_prompted()

    @property
    def vep_engine(self) -> sa.Engine:
        """Lazy-loaded VEP bundle engine (read-only, ~500 MB).

        Tracks the installed file's fingerprint and disposes/recreates the
        cached engine when the bundle artifact is replaced (``run_vep_bundle_update``
        swaps ``vep_bundle.db`` atomically, giving it a new inode). Without this,
        a warmed pooled connection keeps reading the unlinked old inode after
        replacement while the registry version already describes the new file, so
        an annotation could query stale data and stamp it with the new version
        (#1953). Mirrors :attr:`encode_ccres_engine`.
        """
        db_path = self._settings.vep_bundle_db_path
        fingerprint = self._file_fingerprint(db_path)
        if self._vep_engine is None or self._vep_fingerprint != fingerprint:
            if self._vep_engine is not None:
                self._vep_engine.dispose()
            self._vep_engine = self._create_engine(
                db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
                read_optimized=True,
            )
            self._vep_fingerprint = fingerprint
        return self._vep_engine

    @property
    def gnomad_engine(self) -> sa.Engine:
        """Lazy-loaded gnomAD engine (read-only, ~2 GB)."""
        if self._gnomad_engine is None:
            self._gnomad_engine = self._create_engine(
                self._settings.gnomad_db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
                read_optimized=True,
            )
        return self._gnomad_engine

    @property
    def dbnsfp_engine(self) -> sa.Engine:
        """Lazy-loaded dbNSFP engine (read-only, ~10+ GB for the full release)."""
        if self._dbnsfp_engine is None:
            self._dbnsfp_engine = self._create_engine(
                self._settings.dbnsfp_db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
                read_optimized=True,
            )
        return self._dbnsfp_engine

    @property
    def alphamissense_engine(self) -> sa.Engine:
        """Lazy-loaded AlphaMissense engine (read-only missense predictions)."""
        if self._alphamissense_engine is None:
            self._alphamissense_engine = self._create_engine(
                self._settings.alphamissense_db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
                read_optimized=True,
            )
        return self._alphamissense_engine

    @property
    def gtex_eqtl_engine(self) -> sa.Engine:
        """Lazy-loaded GTEx eQTL engine (read-only regulatory-context associations)."""
        if self._gtex_eqtl_engine is None:
            self._gtex_eqtl_engine = self._create_engine(
                self._settings.gtex_eqtl_db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
                read_optimized=True,
            )
        return self._gtex_eqtl_engine

    @property
    def spliceai_engine(self) -> sa.Engine:
        """Lazy-loaded SpliceAI engine (read-only, optional BYO splice predictions)."""
        if self._spliceai_engine is None:
            self._spliceai_engine = self._create_engine(
                self._settings.spliceai_db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
                read_optimized=True,
            )
        return self._spliceai_engine

    @property
    def encode_ccres_engine(self) -> sa.Engine:
        """Lazy-loaded ENCODE cCREs engine (read-only, ~30 MB)."""
        db_path = self._settings.encode_ccres_db_path
        fingerprint = self._file_fingerprint(db_path)
        if self._encode_ccres_engine is None or self._encode_ccres_fingerprint != fingerprint:
            if self._encode_ccres_engine is not None:
                self._encode_ccres_engine.dispose()
            self._encode_ccres_engine = self._create_engine(
                db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
            )
            self._encode_ccres_fingerprint = fingerprint
        return self._encode_ccres_engine

    def get_sample_engine(self, sample_db_path: str | Path) -> sa.Engine:
        """Get or create an engine for a per-sample database.

        On first access, ensures the sample schema is current by adding
        any missing tables (e.g. ``haplogroup_assignments`` from P3-33).

        Args:
            sample_db_path: Path to the sample SQLite file.

        Returns:
            Cached SQLAlchemy Engine for the sample.
        """
        key = str(sample_db_path)
        if key not in self._sample_engines:
            engine = self._create_engine(Path(sample_db_path), wal=self._settings.wal_mode)
            # Ensure schema is up to date (adds missing tables like
            # haplogroup_assignments for pre-P3-33 sample databases).
            from backend.db.sample_schema import ensure_sample_schema_current

            ensure_sample_schema_current(engine)
            self._sample_engines[key] = engine
        engine = self._sample_engines[key]
        if self._sample_prompt_sync_complete.get(key) is not engine:
            if self._sync_cyp2c9_phenytoin_reanalysis_prompt(engine, sample_db_path):
                # Store the engine identity, not a bare path bit. If a delete
                # races this assignment, a replacement engine at the same path
                # still differs and must synchronize for itself.
                self._sample_prompt_sync_complete[key] = engine
        return engine

    def dispose_sample_engine(self, sample_db_path: str | Path) -> None:
        """Dispose and remove a cached sample engine.

        No-op if the engine is not cached.
        """
        key = str(sample_db_path)
        if key in self._sample_engines:
            self._sample_engines[key].dispose()
            del self._sample_engines[key]
        self._sample_prompt_sync_complete.pop(key, None)

    def dispose_all(self) -> None:
        """Dispose all engines. Call on application shutdown."""
        self.reference_engine.dispose()
        for engine in self._sample_engines.values():
            engine.dispose()
        self._sample_engines.clear()
        self._sample_prompt_sync_complete.clear()
        if self._vep_engine is not None:
            self._vep_engine.dispose()
            self._vep_engine = None
            self._vep_fingerprint = None
        if self._gnomad_engine is not None:
            self._gnomad_engine.dispose()
            self._gnomad_engine = None
        if self._dbnsfp_engine is not None:
            self._dbnsfp_engine.dispose()
            self._dbnsfp_engine = None
        if self._alphamissense_engine is not None:
            self._alphamissense_engine.dispose()
            self._alphamissense_engine = None
        if self._gtex_eqtl_engine is not None:
            self._gtex_eqtl_engine.dispose()
            self._gtex_eqtl_engine = None
        if self._spliceai_engine is not None:
            self._spliceai_engine.dispose()
            self._spliceai_engine = None
        if self._encode_ccres_engine is not None:
            self._encode_ccres_engine.dispose()
            self._encode_ccres_engine = None
            self._encode_ccres_fingerprint = None


_registry: DBRegistry | None = None


def get_registry() -> DBRegistry:
    """Return the singleton DBRegistry instance."""
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = DBRegistry(get_settings())
    return _registry


def reset_registry() -> None:
    """Reset the registry singleton. Useful for testing."""
    global _registry  # noqa: PLW0603
    if _registry is not None:
        _registry.dispose_all()
    _registry = None
