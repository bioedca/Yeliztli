"""Docs↔Settings guard: external-service credentials stay documented (#1560).

``pubmed_api_key`` (env ``YELIZTLI_PUBMED_API_KEY``) — the optional NCBI API key
that raises the Entrez literature-lookup rate limit — shipped **undocumented**
while its two sibling external-service keys (``pubmed_email``, ``omim_api_key``)
were listed in ``docs/install/configuration.md``. Worse, a UI↔config naming trap
(the setup wizard's "NCBI API Key" field is stored as the config key
``pubmed_api_key``, not ``ncbi_api_key``) meant a ``config.toml``/env operator
had no way to discover the right name.

This locks the external-service credential settings — the ones a config/env
operator sets by hand — into the configuration doc so none can silently fall out
again, and pins that the wizard-facing NCBI alias stays called out.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "docs" / "install" / "configuration.md"

# External-service credential Settings (backend/config.py "External services")
# that a config.toml/env operator sets by hand. Each must appear in the doc with
# both its config key and its YELIZTLI_-prefixed env var.
_EXTERNAL_SERVICE_SETTINGS = ("pubmed_email", "pubmed_api_key", "omim_api_key")


def _env_var(setting: str) -> str:
    return f"YELIZTLI_{setting.upper()}"


def test_external_service_settings_are_documented() -> None:
    text = _DOC.read_text(encoding="utf-8")
    missing: list[str] = []
    for setting in _EXTERNAL_SERVICE_SETTINGS:
        if setting not in text:
            missing.append(setting)
        env_var = _env_var(setting)
        if env_var not in text:
            missing.append(env_var)
    assert not missing, (
        f"docs/install/configuration.md omits external-service setting(s)/env var(s): "
        f"{missing}. Add each to the settings table and example config.toml "
        f"(cf. #1560, where pubmed_api_key had fallen out of the doc)."
    )


def test_ncbi_api_key_alias_is_documented() -> None:
    """The doc must connect the wizard's "NCBI API Key" field to the real config key
    ``pubmed_api_key`` and name ``ncbi_api_key`` as its accepted alias (#1634).
    Pins the mapping, not just a bare token."""
    text = _DOC.read_text(encoding="utf-8")
    for token in ("NCBI API Key", "pubmed_api_key", "ncbi_api_key"):
        assert token in text, (
            f"configuration.md should tie the wizard's 'NCBI API Key' field to canonical "
            f"config key pubmed_api_key and accepted alias ncbi_api_key (#1634); "
            f"missing {token!r}."
        )


def test_documented_settings_are_real_config_fields() -> None:
    """Premise guard: the locked settings are still real ``Settings`` fields, so a
    rename in config.py trips this (revisit the doc + the lists above) rather than
    leaving the doc quietly asserting a field that no longer exists. Couples to the
    runtime model (like ``test_reference_data_docs.py``), not the source text."""
    from backend.config import Settings

    missing = [s for s in _EXTERNAL_SERVICE_SETTINGS if s not in Settings.model_fields]
    assert not missing, (
        f"backend/config.py Settings no longer declares external-service field(s): "
        f"{missing}. Revisit docs/install/configuration.md and _EXTERNAL_SERVICE_SETTINGS."
    )
