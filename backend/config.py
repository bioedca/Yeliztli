"""Yeliztli configuration via Pydantic Settings.

Layered: defaults -> ~/.yeliztli/config.toml ([yeliztli] table) -> environment
variables (YELIZTLI_*).
"""

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]


DEFAULT_DATA_DIR = Path.home() / ".yeliztli"

# config.toml table key.
CONFIG_SECTION = "yeliztli"

# Canonical env-var prefix.
ENV_PREFIX = "YELIZTLI_"

# Fields never sourced from config.toml: data_dir is location-defining (it says
# *where* config.toml lives), so reading it back from config.toml is circular and
# would re-introduce a stale absolute path. It is resolved from init/env, the
# data_dir pointer file (below), or the default.
_TOML_EXCLUDED_FIELDS = frozenset({"data_dir"})

# Name of the fixed-location pointer that records the user-chosen data_dir. It
# deliberately lives in the DEFAULT home dir (NOT inside data_dir — that would be
# the same circular dependency that excludes data_dir from config.toml), so a
# storage path chosen in the setup wizard actually takes effect on the next
# launch. Resolved against the *live* ``DEFAULT_DATA_DIR`` (which tests
# monkeypatch), mirroring how the config.toml source is resolved.
DATA_DIR_POINTER_NAME = ".data_dir_pointer"


def data_dir_pointer_path() -> Path:
    """Absolute path of the data_dir pointer file (under ``DEFAULT_DATA_DIR``)."""
    return DEFAULT_DATA_DIR / DATA_DIR_POINTER_NAME


def config_toml_path() -> Path:
    """The single config.toml location, read by Settings and written by every writer.

    Lives in ``DEFAULT_DATA_DIR`` (the home dir), NOT the effective ``data_dir``:
    the read source (:class:`_ConfigTomlTableSource`) loads from here, so writers
    must target the same file or their values never round-trip back into Settings.
    Keeping it in the home dir (alongside the data_dir pointer) also means user
    config survives a relocated data volume being unmounted, while the bulk DBs
    live under the relocated ``data_dir``.
    """
    return DEFAULT_DATA_DIR / "config.toml"


class _ConfigTomlTableSource(PydanticBaseSettingsSource):
    """Load settings from the ``[yeliztli]`` table of config.toml.

    pydantic-settings' built-in ``TomlConfigSettingsSource`` reads only
    *top-level* TOML keys, but everything the setup wizard persists lives under a
    named table (``[yeliztli]``), so the built-in source silently ignored all of
    it — wizard-saved auth/theme never reached the runtime ``Settings`` (the Q13
    latent bug). This source descends into that table.
    """

    def __init__(self, settings_cls: type[BaseSettings], toml_path: Path) -> None:
        super().__init__(settings_cls)
        self._table: dict[str, Any] = {}
        if tomllib is not None and toml_path.exists():
            try:
                data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError):
                data = {}
            table = data.get(CONFIG_SECTION)
            if isinstance(table, dict):
                self._table = table

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return self._table.get(field_name), field_name, False

    def prepare_field_value(
        self, field_name: str, field: Any, value: Any, value_is_complex: bool
    ) -> Any:  # noqa: ARG002
        return value

    def __call__(self) -> dict[str, Any]:
        return {
            name: self._table[name]
            for name in self.settings_cls.model_fields
            if name in self._table and name not in _TOML_EXCLUDED_FIELDS
        }


class _DataDirPointerSource(PydanticBaseSettingsSource):
    """Source ``data_dir`` from the fixed-location pointer file.

    Provides *only* ``data_dir`` — the absolute path the setup wizard persisted.
    Ranked below init/env (an explicit ``YELIZTLI_DATA_DIR`` override still wins)
    but above defaults, so the wizard's chosen storage location survives a
    restart. A missing/empty/relative pointer is ignored (falls through to the
    default).
    """

    def __init__(self, settings_cls: type[BaseSettings], pointer_path: Path) -> None:
        super().__init__(settings_cls)
        self._data_dir: str | None = None
        try:
            text = pointer_path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text and Path(text).is_absolute():
            self._data_dir = text

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        if field_name == "data_dir" and self._data_dir is not None:
            return self._data_dir, field_name, False
        return None, field_name, False

    def prepare_field_value(
        self, field_name: str, field: Any, value: Any, value_is_complex: bool
    ) -> Any:  # noqa: ARG002
        return value

    def __call__(self) -> dict[str, Any]:
        return {"data_dir": self._data_dir} if self._data_dir is not None else {}


class Settings(BaseSettings):
    """Application settings with layered config resolution."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        extra="ignore",
    )

    # --- Paths ---
    data_dir: Path = Field(
        default=DEFAULT_DATA_DIR,
        description="Root directory for all Yeliztli data (DBs, samples, logs).",
    )

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    # --- Database ---
    wal_mode: bool = Field(default=True, description="Enable WAL mode on all SQLite DBs.")

    # --- Authentication (optional) ---
    auth_enabled: bool = False
    auth_password_hash: str = Field(default="", description="bcrypt hash of PIN/password.")
    session_timeout_hours: int = 4

    # --- External services ---
    pubmed_email: str = Field(default="", description="Email for NCBI Entrez (required by TOS).")
    pubmed_api_key: str = Field(
        default="", description="Optional NCBI API key for higher rate limits."
    )
    omim_api_key: str = Field(default="", description="Optional OMIM API key for enrichment.")

    # --- Update manager ---
    update_check_interval: Literal["startup", "daily", "weekly"] = "daily"
    update_download_window: str | None = Field(
        default=None,
        description='Optional time window for large downloads, e.g. "02:00-06:00".',
    )

    # --- LAI (Local Ancestry Inference) ---
    lai_bundle_path: Path | None = Field(
        default=None,
        description="Path to LAI bundle directory. Defaults to data_dir / 'lai_bundle'.",
    )
    lai_java_mem: str = Field(
        default="4g",
        description="JVM memory allocation for Beagle phasing (e.g. '4g').",
    )

    # --- UI preferences ---
    theme: Literal["light", "dark", "system"] = "system"

    # --- Logging ---
    log_level: str = "INFO"
    log_dir: Path | None = None  # Defaults to data_dir / "logs" at runtime

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def resolved_log_dir(self) -> Path:
        return self.log_dir or (self.data_dir / "logs")

    @property
    def reference_db_path(self) -> Path:
        return self.data_dir / "reference.db"

    @property
    def vep_bundle_db_path(self) -> Path:
        return self.data_dir / "vep_bundle.db"

    @property
    def gnomad_db_path(self) -> Path:
        return self.data_dir / "gnomad_af.db"

    @property
    def dbnsfp_db_path(self) -> Path:
        return self.data_dir / "dbnsfp.db"

    @property
    def alphamissense_db_path(self) -> Path:
        return self.data_dir / "alphamissense.db"

    @property
    def encode_ccres_db_path(self) -> Path:
        return self.data_dir / "encode_ccres.db"

    @property
    def resolved_lai_bundle_path(self) -> Path:
        return self.lai_bundle_path or (self.data_dir / "lai_bundle")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type["BaseSettings"],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Precedence: init > YELIZTLI_ env > data_dir pointer > [yeliztli] TOML > dotenv."""
        return (
            init_settings,
            env_settings,
            _DataDirPointerSource(settings_cls, data_dir_pointer_path()),
            _ConfigTomlTableSource(settings_cls, config_toml_path()),
            dotenv_settings,
        )


def read_config_section(content: dict[str, Any]) -> dict[str, Any]:
    """Return a mutable copy of the persisted ``[yeliztli]`` config table.

    Returns ``{}`` when the table is absent. The result is a shallow copy so
    callers can mutate it and persist it via :func:`write_config_section`.
    """
    section = content.get(CONFIG_SECTION)
    return dict(section) if isinstance(section, dict) else {}


def write_config_section(content: dict[str, Any], section: dict[str, Any]) -> None:
    """Store ``section`` under the ``[yeliztli]`` key in ``content``."""
    content[CONFIG_SECTION] = section


# Control characters that have a short TOML escape; everything else < 0x20 (and
# DEL) is emitted as ``\uXXXX``.
_TOML_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _escape_toml_basic_string(value: str) -> str:
    """Escape ``value`` for use inside a TOML basic (double-quoted) string.

    Implements the TOML basic-string escape rules: backslash and double-quote
    are escaped, the named control escapes are used where they exist, and any
    other control character (or DEL) becomes ``\\uXXXX``. Without this, a value
    containing ``\\`` / ``"`` / a newline (a Windows path, an API key, an email
    with odd characters) produces an unparseable config.toml that
    ``_read_config_toml`` then silently drops *in full* — losing every persisted
    setting, not just the offending value.
    """
    out: list[str] = []
    for ch in value:
        escaped = _TOML_SHORT_ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)


def dump_config_toml(content: dict[str, dict[str, object]]) -> str:
    """Serialize a nested ``{table: {key: value}}`` mapping to TOML text.

    String values are escaped per :func:`_escape_toml_basic_string`. ``bool`` is
    checked before ``int`` (``bool`` is an ``int`` subclass) so flags render as
    ``true``/``false``, not ``1``/``0``.
    """
    lines: list[str] = []
    for table_name, table_values in content.items():
        lines.append(f"[{table_name}]")
        if isinstance(table_values, dict):
            for key, value in table_values.items():
                if isinstance(value, bool):
                    lines.append(f"{key} = {'true' if value else 'false'}")
                elif isinstance(value, (int, float)):
                    lines.append(f"{key} = {value}")
                else:
                    lines.append(f'{key} = "{_escape_toml_basic_string(str(value))}"')
        lines.append("")
    return "\n".join(lines)


def write_config_toml(config_path: Path, content: dict[str, dict[str, object]]) -> None:
    """Write ``content`` to ``config_path`` as escaped TOML (creating parents)."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(dump_config_toml(content), encoding="utf-8")


# Serializes the read-modify-write of config.toml across every writer (setup
# credentials, preferences theme, auth settings). Without one shared lock, two
# concurrent saves each read the file, mutate their own key, and write back —
# last-writer-wins silently drops the other's key.
config_write_lock = threading.Lock()


def write_data_dir_pointer(data_dir: Path) -> None:
    """Persist the chosen ``data_dir`` to the fixed-location pointer file.

    The pointer lives in the default home dir so it is always found at startup,
    regardless of where ``data_dir`` itself points. Callers should
    :func:`get_settings.cache_clear` afterwards so the new path takes effect in
    the running process.
    """
    pointer = data_dir_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(data_dir), encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    """Create and return application settings instance."""
    return Settings()
