#!/usr/bin/env bash
# Regenerate the vulture dead-code baseline (vulture_whitelist.py) — issue #579.
#
# When to run:
#   - After resolving a per-symbol dead-code issue (so its stale baseline entry
#     is dropped), or
#   - When adding framework code vulture can't see statically (a new Pydantic
#     model, SQLAlchemy table, dataclass, etc.) trips a false positive.
#
# Always review the diff before committing — the baseline should shrink over time
# as real dead code is removed; a growing baseline means new false positives to
# investigate (prefer a narrow fix over a blanket whitelist entry).
#
# min_confidence + ignore_decorators come from [tool.vulture] in pyproject.toml
# (single source of truth); only the analysed paths are passed here so the
# previous whitelist isn't fed back into its own regeneration.
set -euo pipefail
cd "$(dirname "$0")/.."
vulture backend tests scripts --make-whitelist > vulture_whitelist.py
echo "Regenerated vulture_whitelist.py ($(wc -l < vulture_whitelist.py) entries)"
