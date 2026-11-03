"""Claim keys are validated before any lock registration or filesystem access."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.db import build_guard as guard
from backend.db.database_registry import DATABASES

INVALID_KEYS = [
    "",
    ".",
    "..",
    "../outside",
    "/absolute",
    "clinvar/child",
    "clinvar\\child",
    "C:\\outside",
    "clinvar ",
    " clinvar",
    "CLINVAR",
    "clinvar\n",
    "clinvar\r",
    "clinvar\x00",
    "%2e%2e%2foutside",
    "clinvar\u202e",
    "\uff43linvar",
    "a" * 65,
    None,
    True,
    7,
    b"clinvar",
    [],
    {},
]
ENTRYPOINTS = [
    "build_lock",
    "try_acquire_build_lock",
    "build_claim",
    "cross_process_build_claim",
    "is_build_locked",
    "is_cross_process_build_claimed",
    "_claim_path",
]


@pytest.mark.parametrize("key", INVALID_KEYS)
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_invalid_keys_fail_before_side_effects(key, entrypoint, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid claim key reached a filesystem operation")

    # Restore process-global filesystem APIs before pytest removes tmp_path
    # under tmp_path_retention_policy=failed (shutil.rmtree itself uses open).
    with monkeypatch.context() as patch:
        patch.setattr(guard, "_locks", {})
        patch.setattr(guard, "_held_claims", threading.local())
        patch.setattr(guard.os, "open", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        with pytest.raises(ValueError, match="claim key"):
            if entrypoint == "_claim_path":
                guard._claim_path(tmp_path, key)
            elif entrypoint == "is_build_locked":
                guard.is_build_locked(key)
            elif entrypoint == "is_cross_process_build_claimed":
                guard.is_cross_process_build_claimed(key, tmp_path)
            else:
                fn = getattr(guard, entrypoint)
                args = (key, tmp_path) if "claim" in entrypoint else (key,)
                with fn(*args):
                    pytest.fail("Invalid claim key entered a guarded operation")
        assert guard._locks == {}
        assert not hasattr(guard._held_claims, "names")


@pytest.mark.parametrize(
    "entrypoint", ["cross_process_build_claim", "is_cross_process_build_claimed"]
)
def test_non_posix_fallback_still_validates_keys(entrypoint, tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "fcntl", None)
    with pytest.raises(ValueError, match="claim key"):
        if entrypoint == "cross_process_build_claim":
            with guard.cross_process_build_claim("../outside", tmp_path):
                pytest.fail("Invalid fallback claim was accepted")
        else:
            guard.is_cross_process_build_claimed("../outside", tmp_path)


@pytest.mark.parametrize(
    "key",
    sorted(set(DATABASES) | {"mondo_hpo_source_bundles", "a", "a" * 64, "unknown_safe_name"}),
)
def test_legitimate_keys_remain_usable(key, tmp_path):
    with guard.build_claim(key, tmp_path) as acquired:
        assert acquired
        assert guard.is_cross_process_build_claimed(key, tmp_path)
        with guard.build_claim(key, tmp_path) as nested:
            assert nested
    assert not guard.is_cross_process_build_claimed(key, tmp_path)
    assert (tmp_path / ".claims" / f"{key}.claim").is_file()


@pytest.mark.parametrize("operation", ["path", "acquire", "probe", "build"])
def test_string_subclass_cannot_redirect_claim_namespace(operation, tmp_path):
    formats = []

    class RedirectedKey(str):
        def __format__(self, spec):
            formats.append(spec)
            return "../redirected"

    key = RedirectedKey("clinvar")
    root = tmp_path / "data"
    expected = root / ".claims" / "clinvar.claim"
    if operation == "path":
        assert guard._claim_path(root, key) == expected
    elif operation == "probe":
        with guard.cross_process_build_claim("clinvar", root) as held:
            assert held
            assert guard.is_cross_process_build_claimed(key, root)
    else:
        acquire = guard.cross_process_build_claim if operation == "acquire" else guard.build_claim
        with acquire(key, root) as held:
            assert held
            assert expected.is_file()
            assert guard.is_cross_process_build_claimed("clinvar", root)
    assert formats == []
    assert not (root / "redirected.claim").exists()
    assert not (root / ".claims" / "redirected.claim").exists()
