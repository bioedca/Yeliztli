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

from pathlib import Path

import sqlalchemy as sa

from backend.config import Settings, get_settings
from backend.db.sqlite_engine import SQLiteSynchronousMode, make_sqlite_engine


class DBRegistry:
    """Singleton managing SQLite engine connections for all databases.

    Reference DB engines are created once at startup. Sample DB engines
    are created on demand and cached.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sample_engines: dict[str, sa.Engine] = {}

        # Reference DB (shared, long-lived)
        self.reference_engine = self._create_engine(
            settings.reference_db_path,
            wal=settings.wal_mode,
            synchronous=self._wal_synchronous,
        )

        # Large reference DBs (opened lazily on first access)
        self._vep_engine: sa.Engine | None = None
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

    @property
    def vep_engine(self) -> sa.Engine:
        """Lazy-loaded VEP bundle engine (read-only, ~500 MB)."""
        if self._vep_engine is None:
            self._vep_engine = self._create_engine(
                self._settings.vep_bundle_db_path,
                wal=self._settings.wal_mode,
                synchronous=self._wal_synchronous,
                read_optimized=True,
            )
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
        return self._sample_engines[key]

    def dispose_sample_engine(self, sample_db_path: str | Path) -> None:
        """Dispose and remove a cached sample engine.

        No-op if the engine is not cached.
        """
        key = str(sample_db_path)
        if key in self._sample_engines:
            self._sample_engines[key].dispose()
            del self._sample_engines[key]

    def dispose_all(self) -> None:
        """Dispose all engines. Call on application shutdown."""
        self.reference_engine.dispose()
        for engine in self._sample_engines.values():
            engine.dispose()
        self._sample_engines.clear()
        if self._vep_engine is not None:
            self._vep_engine.dispose()
            self._vep_engine = None
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
