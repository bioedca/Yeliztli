"""Tests for backend.config module."""

import tomllib
from pathlib import Path

import pytest

import backend.config as config
from backend.config import (
    Settings,
    dump_config_toml,
    get_settings,
    write_config_toml,
    write_data_dir_pointer,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the get_settings lru_cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_settings():
    """Settings should load with sensible defaults."""
    settings = get_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.debug is False
    assert settings.wal_mode is True
    assert settings.auth_enabled is False
    assert settings.theme == "system"
    assert settings.log_level == "INFO"
    assert settings.update_check_interval == "daily"


def test_data_dir_default(tmp_path, monkeypatch):
    """Default data_dir should be ~/.yeliztli (no pointer/env override).

    Point the pointer source at an empty temp dir so this stays hermetic: with
    no .data_dir_pointer present, data_dir falls back to the field default
    (~/.yeliztli). Without this, the new _DataDirPointerSource would read the
    developer's real ~/.yeliztli/.data_dir_pointer (written by the storage
    wizard) and the test would fail on any machine that has used the feature.
    """
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)
    settings = get_settings()
    assert settings.data_dir == Path.home() / ".yeliztli"


def test_derived_paths():
    """Derived paths should be relative to data_dir."""
    settings = Settings(data_dir=Path("/tmp/ylzt-test"))
    assert settings.samples_dir == Path("/tmp/ylzt-test/samples")
    assert settings.downloads_dir == Path("/tmp/ylzt-test/downloads")
    assert settings.resolved_log_dir == Path("/tmp/ylzt-test/logs")
    assert settings.reference_db_path == Path("/tmp/ylzt-test/reference.db")
    assert settings.vep_bundle_db_path == Path("/tmp/ylzt-test/vep_bundle.db")
    assert settings.gnomad_db_path == Path("/tmp/ylzt-test/gnomad_af.db")
    assert settings.dbnsfp_db_path == Path("/tmp/ylzt-test/dbnsfp.db")
    assert settings.resolved_grch37_fasta_path is None


def test_grch37_fasta_path_resolution(tmp_path):
    """Explicit FASTA path wins; otherwise data_dir/grch37.fa is auto-discovered."""
    default_fasta = tmp_path / "grch37.fa"
    settings = Settings(data_dir=tmp_path)
    assert settings.resolved_grch37_fasta_path is None

    default_fasta.write_text(">1\nA\n", encoding="ascii")
    assert Settings(data_dir=tmp_path).resolved_grch37_fasta_path == default_fasta

    explicit_fasta = tmp_path / "custom.fa"
    assert (
        Settings(data_dir=tmp_path, grch37_fasta_path=explicit_fasta).resolved_grch37_fasta_path
        == explicit_fasta
    )


def test_genome_browser_refseq_track_path_resolution(tmp_path):
    """Explicit RefSeq BED path wins; otherwise data_dir/grch37_refseq.bed is used."""
    default_refseq = tmp_path / "grch37_refseq.bed"
    settings = Settings(data_dir=tmp_path)
    assert settings.resolved_genome_browser_refseq_track_path is None

    default_refseq.write_text("chr1\t0\t10\tGENE1\n", encoding="ascii")
    assert Settings(data_dir=tmp_path).resolved_genome_browser_refseq_track_path == default_refseq

    explicit_refseq = tmp_path / "custom-refseq.bed"
    assert (
        Settings(
            data_dir=tmp_path,
            genome_browser_refseq_track_path=explicit_refseq,
        ).resolved_genome_browser_refseq_track_path
        == explicit_refseq
    )


def test_env_override(monkeypatch):
    """Canonical YELIZTLI_ environment variables should override defaults."""
    monkeypatch.setenv("YELIZTLI_PORT", "9000")
    monkeypatch.setenv("YELIZTLI_DEBUG", "true")
    settings = Settings()
    assert settings.port == 9000
    assert settings.debug is True


def test_download_staging_dir_defaults_under_data_dir():
    """Unset download_staging_dir keeps downloads_dir at data_dir/'downloads'.

    Backward-compat contract: with no override the staging dir is identical to
    the historical location, so every existing consumer is unaffected.
    """
    settings = Settings(data_dir=Path("/tmp/ylzt-test"))
    assert settings.download_staging_dir is None
    assert settings.downloads_dir == Path("/tmp/ylzt-test/downloads")


def test_download_staging_dir_override_respected():
    """An explicit download_staging_dir overrides only the staging dir.

    The persistent databases stay under data_dir — the whole point is to let a
    big transient archive stage on a roomy/disposable volume without moving the
    built DBs.
    """
    settings = Settings(
        data_dir=Path("/tmp/ylzt-test"),
        download_staging_dir=Path("/mnt/scratch/staging"),
    )
    assert settings.downloads_dir == Path("/mnt/scratch/staging")
    assert settings.data_dir == Path("/tmp/ylzt-test")
    assert settings.reference_db_path == Path("/tmp/ylzt-test/reference.db")
    assert settings.dbnsfp_db_path == Path("/tmp/ylzt-test/dbnsfp.db")


def test_download_staging_dir_env_override(tmp_path, monkeypatch):
    """YELIZTLI_DOWNLOAD_STAGING_DIR sets the staging dir via environment."""
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)
    monkeypatch.setenv("YELIZTLI_DOWNLOAD_STAGING_DIR", "/mnt/scratch/staging")
    settings = get_settings()
    assert settings.download_staging_dir == Path("/mnt/scratch/staging")
    assert settings.downloads_dir == Path("/mnt/scratch/staging")


def test_download_staging_dir_from_config_toml(tmp_path, monkeypatch):
    """download_staging_dir is settable via the [yeliztli] config.toml table.

    Unlike data_dir it is NOT location-defining for config.toml, so it is
    deliberately not excluded from the toml source.
    """
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    scratch = tmp_path / "scratch"
    _write_toml(
        tmp_path / "config.toml",
        f'[yeliztli]\ndownload_staging_dir = "{scratch}"\n',
    )
    settings = get_settings()
    assert settings.download_staging_dir == scratch
    assert settings.downloads_dir == scratch


def test_get_settings_caching():
    """get_settings should return the same instance on repeated calls."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def _write_toml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_config_toml_section_applied(tmp_path, monkeypatch):
    """Q13 fix: values under the [yeliztli] table reach Settings."""
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    _write_toml(
        tmp_path / "config.toml",
        '[yeliztli]\nauth_enabled = true\nauth_password_hash = "abc123"\ntheme = "dark"\n',
    )
    settings = Settings()
    assert settings.auth_enabled is True
    assert settings.auth_password_hash == "abc123"
    assert settings.theme == "dark"


def test_config_toml_data_dir_excluded(tmp_path, monkeypatch):
    """data_dir is never sourced from config.toml (location-defining; avoids stale path)."""
    monkeypatch.setattr("backend.config.DEFAULT_DATA_DIR", tmp_path)
    _write_toml(
        tmp_path / "config.toml",
        f'[yeliztli]\ndata_dir = "{tmp_path / "stale"}"\ntheme = "dark"\n',
    )
    settings = Settings()
    assert settings.theme == "dark"  # other keys still applied
    assert settings.data_dir != tmp_path / "stale"


# ── Shared escaped TOML writer ───────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        'has "double quotes"',
        r"back\slash",
        r"C:\Users\me\yeliztli",  # Windows path: every component is a \-escape
        "line1\nline2",
        "tab\tseparated",
        'mix: "q" \\ \n end',
        "plain@example.com",
    ],
)
def test_dump_config_toml_escapes_round_trip(value):
    """Special-character string values must survive a TOML write→reparse.

    The old hand-rolled writers emitted ``key = "<value>"`` unescaped, so a
    backslash/quote/newline produced an unparseable file that the loader then
    silently dropped *in full*.
    """
    text = dump_config_toml({"yeliztli": {"pubmed_email": value}})
    reparsed = tomllib.loads(text)
    assert reparsed["yeliztli"]["pubmed_email"] == value


def test_dump_config_toml_preserves_scalar_types():
    text = dump_config_toml(
        {"yeliztli": {"flag_on": True, "flag_off": False, "count": 7, "ratio": 1.5}}
    )
    table = tomllib.loads(text)["yeliztli"]
    assert table == {"flag_on": True, "flag_off": False, "count": 7, "ratio": 1.5}
    # bool must render as a TOML boolean, not 1/0 (bool is an int subclass).
    assert "flag_on = true" in text
    assert "flag_off = false" in text


def test_write_config_toml_creates_parents_and_round_trips(tmp_path):
    config_path = tmp_path / "nested" / "config.toml"
    content = {
        "yeliztli": {
            "pubmed_email": 'weird"name\\@example.com',
            "auth_password_hash": "$2b$12$abc\\def",
            "theme": "dark",
            "auth_enabled": True,
        }
    }
    write_config_toml(config_path, content)

    assert config_path.exists()
    reparsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert reparsed == content


def test_dump_config_toml_escapes_control_chars():
    text = dump_config_toml({"yeliztli": {"k": "a\x00\x07b"}})
    # NUL and BEL have no short escape → emitted as \uXXXX, and stay parseable.
    assert tomllib.loads(text)["yeliztli"]["k"] == "a\x00\x07b"


# ── data_dir pointer (storage-path persistence) ──────────────────────


def test_data_dir_pointer_source_applies(tmp_path, monkeypatch):
    """An absolute path in the pointer file becomes the effective data_dir."""
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)
    chosen = tmp_path / "chosen_store"
    (tmp_path / ".data_dir_pointer").write_text(str(chosen), encoding="utf-8")

    assert Settings().data_dir == chosen


def test_env_overrides_data_dir_pointer(tmp_path, monkeypatch):
    """An explicit YELIZTLI_DATA_DIR still wins over the pointer (init > env > pointer)."""
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)
    (tmp_path / ".data_dir_pointer").write_text(str(tmp_path / "from_pointer"), encoding="utf-8")
    monkeypatch.setenv("YELIZTLI_DATA_DIR", str(tmp_path / "from_env"))

    assert Settings().data_dir == tmp_path / "from_env"


def test_relative_pointer_is_ignored(tmp_path, monkeypatch):
    """A non-absolute pointer is ignored — data_dir falls back to the default."""
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)
    (tmp_path / ".data_dir_pointer").write_text("relative/path", encoding="utf-8")

    # Field default is frozen at import (the real ~/.yeliztli), not the patched
    # value — the point is only that the relative pointer is NOT applied.
    assert Settings().data_dir == Path.home() / ".yeliztli"


def test_write_data_dir_pointer_round_trip(tmp_path, monkeypatch):
    """write_data_dir_pointer + cache_clear makes the chosen path effective."""
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path)

    chosen = tmp_path / "relocated"
    write_data_dir_pointer(chosen)
    get_settings.cache_clear()

    assert (tmp_path / ".data_dir_pointer").read_text(encoding="utf-8").strip() == str(chosen)
    assert get_settings().data_dir == chosen
