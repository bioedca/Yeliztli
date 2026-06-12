#!/usr/bin/env bash
# scripts/preflight_bundle_v2.sh
#
# Phase A pre-flight gate for the v2.0.0 bundle rebuild (build-plan §0e + §7).
# Wires every assertion from the §7 "Pre-flight checklist" into a single
# ✅/❌-per-check runner. By default it EXITS NON-ZERO on the FIRST failed check
# (plan §0e); pass -k/--keep-going to run all checks and print a summary.
#
# Usage:
#   conda activate GI            # checks 9–14 invoke the GI python/pytest
#   bash scripts/preflight_bundle_v2.sh           # stop at first failure (§0e)
#   bash scripts/preflight_bundle_v2.sh -k        # run every check, then summarise
#
# IMPORTANT: several checks probe external state — open GitHub releases, the
# operator-provided SLURM build host, the VEP-rebuild host, the `lai_bundle`
# conda env, and a human bio-validator. Before Phase 0 has fully merged and the
# v1.1.0 rollback release exists, ❌ here is EXPECTED: this script is precisely
# the gate that tells the Phase A driver what still has to become true. It is
# not a test of this repo's tree — it is the "is the world ready for Phase A?"
# checklist.
#
# Environment overrides (all optional):
#   PREFLIGHT_SSH_HOST          ssh alias for the LAI cluster      (no default)
#   PREFLIGHT_CLUSTER_DIR       scratch dir checked for free space (no default)
#   PREFLIGHT_CLUSTER_MIN_GB    GiB of free scratch required       (default: 500)
#   PREFLIGHT_LAI_ENV           conda env name on the cluster      (default: lai_bundle)
#   PREFLIGHT_MAIN_REF          git ref treated as "main"          (default: origin/main, then main)
#   PREFLIGHT_VEP_CACHE_DIR     VEP cache root                     (default: $HOME/.vep)
#   PREFLIGHT_BIOVALIDATOR_ACK  set to 1 once the bio-validator confirms standby

set -uo pipefail

# ─── Locate the repo root (so the python/git checks resolve regardless of cwd) ─
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || { printf 'cannot cd to repo root: %s\n' "$REPO_ROOT" >&2; exit 1; }

# ─── Tunables ─────────────────────────────────────────────────────────────
SSH_HOST="${PREFLIGHT_SSH_HOST:-}"
CLUSTER_DIR="${PREFLIGHT_CLUSTER_DIR:-}"
CLUSTER_MIN_GB="${PREFLIGHT_CLUSTER_MIN_GB:-500}"
LAI_ENV="${PREFLIGHT_LAI_ENV:-lai_bundle}"
VEP_CACHE_DIR="${PREFLIGHT_VEP_CACHE_DIR:-$HOME/.vep}"
ENV_LOCK="docs/lai-bundle-release-runbook-env.lock.yaml"
# ServerAlive* bounds a wedged session (~20s) on all platforms — ConnectTimeout
# alone only caps connection setup, not the runtime of the remote df / conda export.
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=2)

TOTAL=14            # number of §7 checklist items wired below
KEEP_GOING=0
n=0                 # checks run so far
failed=0            # checks failed so far

# ─── CLI ─────────────────────────────────────────────────────────────────
usage() {
  # Print the leading comment block (after the shebang), stripping '# ' — and
  # stop at the first non-comment line so help never leaks executable source.
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -k|--keep-going) KEEP_GOING=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown argument: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# ─── Runner ────────────────────────────────────────────────────────────────
# check "<description>" "<remediation hint>" <command> [args...]
#   Runs the command with output suppressed. Logs ✅ on success. On failure logs
#   ❌ + the hint and (default) exits 1 immediately (plan §0e); under -k it tallies
#   the failure and continues so the operator sees the whole checklist at once.
check() {
  local desc="$1" hint="$2"; shift 2
  n=$((n + 1))
  if "$@" >/dev/null 2>&1; then
    printf '✅ %2d/%d  %s\n' "$n" "$TOTAL" "$desc"
    return 0
  fi
  printf '❌ %2d/%d  %s\n' "$n" "$TOTAL" "$desc"
  [ -n "$hint" ] && printf '         ↳ %s\n' "$hint"
  failed=$((failed + 1))
  if [ "$KEEP_GOING" != "1" ]; then
    printf '\nPre-flight FAILED at check %d (exit-on-first-failure, plan §0e).\n' "$n" >&2
    printf 'Re-run with -k/--keep-going to list every remaining check.\n' >&2
    exit 1
  fi
  return 1
}

# Resolve the ref to treat as "main" for the merged-PR check (§7 item 14).
resolve_main_ref() {
  local cand
  for cand in "${PREFLIGHT_MAIN_REF:-}" origin/main main; do
    [ -n "$cand" ] || continue
    if git rev-parse --verify --quiet "${cand}^{commit}" >/dev/null 2>&1; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  return 1
}

# ─── §7 checklist, one function per bullet ──────────────────────────────────

# §7.1 — no in-flight bundle-v2.0.0 / lai-bundle-v2.0.0 drafts from a prior run.
no_inflight_drafts() {
  local drafts
  drafts="$(gh release list --limit 200 --json tagName,isDraft \
    --jq '.[] | select(.isDraft and (.tagName == "bundle-v2.0.0" or .tagName == "lai-bundle-v2.0.0")) | .tagName' \
    2>/dev/null)" || return 1
  [ -z "$drafts" ]
}

# §7.2 — rollback target lai-bundle-v1.1.0 resolves (Phase 0h completed).
rollback_release_resolves() {
  gh release view lai-bundle-v1.1.0 >/dev/null 2>&1
}

# §7.3 — bundle-v1.0.0 carries the vep_bundle.db asset (Phase A1 input).
vep_v1_asset_present() {
  gh release view bundle-v1.0.0 --json assets --jq '.assets[].name' 2>/dev/null \
    | grep -qx 'vep_bundle.db'
}

# §7.4 — ≥ CLUSTER_MIN_GB GiB free scratch under CLUSTER_DIR on the cluster.
cluster_inputs_configured() {
  [ -n "$SSH_HOST" ] && [ -n "$CLUSTER_DIR" ]
}

cluster_disk_free() {
  local min_bytes avail
  cluster_inputs_configured || return 1
  min_bytes=$(( CLUSTER_MIN_GB * 1024 * 1024 * 1024 ))
  # df the deepest *existing* ancestor of CLUSTER_DIR — the dir itself is not
  # created until Phase C. -PB1 → POSIX one-line-per-fs, available bytes in col 4.
  avail="$(ssh "${SSH_OPTS[@]}" "$SSH_HOST" bash -s -- "$CLUSTER_DIR" <<'REMOTE' 2>/dev/null
dir="$1"
while [ ! -d "$dir" ] && [ "$dir" != / ] && [ -n "$dir" ]; do dir="$(dirname "$dir")"; done
[ -d "$dir" ] || exit 1
df -PB1 "$dir" | awk 'NR == 2 { print $4 }'
REMOTE
)" || return 1
  [ -n "$avail" ] || return 1
  [ "$avail" -ge "$min_bytes" ] 2>/dev/null
}

# §7.5 — committed env lock matches the active lai_bundle conda env on the cluster.
env_lock_matches() {
  cluster_inputs_configured || return 1
  [ -s "$ENV_LOCK" ] || return 1
  local remote
  # `bash -lc` so the cluster's conda init (login profile) is on PATH.
  # shellcheck disable=SC2029  # $LAI_ENV is intentionally expanded client-side.
  remote="$(ssh "${SSH_OPTS[@]}" "$SSH_HOST" \
    "bash -lc 'conda env export -n $LAI_ENV --no-builds'" 2>/dev/null)" || return 1
  [ -n "$remote" ] || return 1
  diff <(printf '%s\n' "$remote" | sed '/^prefix: /d') "$ENV_LOCK" >/dev/null 2>&1
}

# §7.6 — Ensembl VEP 112 + GRCh37 cache available on this (VEP-rebuild) host.
vep_cache_present() {
  command -v vep >/dev/null 2>&1 || return 1
  compgen -G "$VEP_CACHE_DIR/homo_sapiens*/112_GRCh37" >/dev/null 2>&1
}

# §7.7 — the *active* gh account carries the classic 'repo' scope. Isolate the
# active account's stanza (multi-account auth is common) and match the exact
# quoted token 'repo' — so granular sub-scopes ('repo:status', 'public_repo')
# and an inactive account's scopes never trigger a false pass.
gh_repo_scope() {
  gh auth status 2>&1 \
    | awk '
        /account / { active = 0 }
        /Active account: true/ { active = 1 }
        active && /[Tt]oken scopes:/ { print }
      ' \
    | grep -q "'repo'"
}

# §7.8 — bio-validator has read the Integration Plan and is on standby.
# Not machine-verifiable; gated on an explicit operator acknowledgement.
bio_validator_standby() {
  [ "${PREFLIGHT_BIOVALIDATOR_ACK:-0}" = "1" ]
}

# §7.9 — manifest.json is at placeholder state (64 zeros) for both v2 bundles.
manifest_placeholder() {
  python -c 'import json, sys; b = json.load(open("bundles/manifest.json"))["bundles"]; z = "0" * 64; sys.exit(0 if all(b[k]["sha256"] == z for k in ("vep_bundle", "lai_bundle")) else 1)'
}

# §7.10 — database_registry DATABASES["lai_bundle"].sha256 is None (Phase 0i).
registry_lai_sha_none() {
  python -c 'import sys; from backend.db.database_registry import DATABASES; sys.exit(0 if DATABASES["lai_bundle"].sha256 is None else 1)'
}

# §7.11 — DATABASES["vep_bundle"].url points at the bundle-v2.0.0 asset (Phase 0i).
registry_vep_url() {
  python -c 'import sys; from backend.db.database_registry import DATABASES; sys.exit(0 if DATABASES["vep_bundle"].url.endswith("/releases/download/bundle-v2.0.0/vep_bundle.db") else 1)'
}

# §7.12 — coverage_report auto-detects 1-col/3-col via _load_catalog_rsids (Phase 0j).
coverage_report_autodetect() {
  python -c 'from scripts.build_vep_bundle import _load_catalog_rsids' >/dev/null 2>&1 || return 1
  pytest tests/backend/test_build_vep_bundle_coverage.py -q >/dev/null 2>&1
}

# §7.13 — env lock file exists, is non-empty, and is committed (Phase 0k).
# cat-file against HEAD asserts a real commit object — `git ls-files` would also
# accept a merely-staged file, which the §7.13 "committed" wording excludes.
env_lock_committed() {
  [ -s "$ENV_LOCK" ] || return 1
  git cat-file -e "HEAD:$ENV_LOCK" 2>/dev/null
}

# §7.14 — the Phase 0 PR (PR-0z) is merged, i.e. its deliverables are on main.
# New-to-Phase-0 files must exist on the main ref; the workflow content edits
# (ci.yml actionlint job, bundle-release.yml LAI extension) and the in-repo
# v1.1 → v1.1.0 URL swap are asserted on the same ref — existence alone cannot
# detect a reverted edit. (§7.14's registry + coverage_report edits are covered
# by checks 10–12 against the working tree, which post-merge equals main.)
phase0_merged() {
  local ref f
  ref="$(resolve_main_ref)" || return 1
  local files=(
    scripts/build_union_catalog.py
    scripts/build_ancestrydna_site_list.py
    scripts/extract_vep_bundle_rsids.py
    scripts/preflight_bundle_v2.sh
    docs/release-notes/bundle-v2.0.0.md
    docs/release-notes/lai-bundle-v2.0.0.md
    "$ENV_LOCK"
  )
  for f in "${files[@]}"; do
    git cat-file -e "$ref:$f" 2>/dev/null || return 1
  done
  git cat-file -p "$ref:.github/workflows/ci.yml" 2>/dev/null | grep -q 'actionlint' || return 1
  git cat-file -p "$ref:.github/workflows/bundle-release.yml" 2>/dev/null | grep -q 'lai_bundle' || return 1
  # No stale lai-bundle-v1.1 (non-.0) references survive on main (matches §9 Done #11).
  ! git grep -I -E -q 'lai-bundle-v1\.1([^.0-9]|$)' "$ref" -- \
    '*.md' '*.py' '*.json' '*.yml' '*.ts' '*.tsx' 2>/dev/null
}

# ─── Run the checklist (in §7 order) ────────────────────────────────────────
printf 'Pre-flight for the v2.0.0 bundle rebuild (build-plan §7)\n'
printf 'repo: %s   ssh host: %s   main ref: %s\n\n' \
  "$REPO_ROOT" "${SSH_HOST:-<unset>}" "$(resolve_main_ref || echo '<unresolved>')"

check 'no in-flight v2.0.0 release drafts from a prior attempt' \
  'gh release delete the stale draft(s), or run gh auth login' \
  no_inflight_drafts

check 'rollback target lai-bundle-v1.1.0 release resolves (Phase 0h)' \
  'complete Step 13 (rename lai-bundle-v1.1 → v1.1.0) before Phase A' \
  rollback_release_resolves

check 'bundle-v1.0.0 release has the vep_bundle.db asset (Phase A1 input)' \
  'confirm the v1.0.0 VEP bundle asset is still attached to its release' \
  vep_v1_asset_present

check "≥ ${CLUSTER_MIN_GB} GiB free scratch under ${CLUSTER_DIR:-<unset>} on ${SSH_HOST:-<unset>}" \
  "set PREFLIGHT_CLUSTER_DIR / PREFLIGHT_SSH_HOST for the build host, then confirm free space" \
  cluster_disk_free

check "committed env lock matches the active ${LAI_ENV} conda env on ${SSH_HOST:-<unset>}" \
  "set PREFLIGHT_SSH_HOST and regenerate docs/lai-bundle-release-runbook-env.lock.yaml without a prefix line" \
  env_lock_matches

check 'Ensembl VEP 112 + GRCh37 cache available on this host' \
  "install VEP 112 + GRCh37 cache, or set PREFLIGHT_VEP_CACHE_DIR (looked in ${VEP_CACHE_DIR})" \
  vep_cache_present

check "gh auth carries the classic 'repo' scope" \
  'gh auth login -s repo (fine-grained tokens are not detected by this check)' \
  gh_repo_scope

check 'bio-validator has read the Integration Plan and is on standby' \
  'once confirmed by the bio-validator, re-run with PREFLIGHT_BIOVALIDATOR_ACK=1' \
  bio_validator_standby

check 'bundles/manifest.json is at placeholder state for both v2 bundles' \
  'manifest sha256s must stay "0000…" until Phase D (PR-0a / PR-0c)' \
  manifest_placeholder

check 'database_registry DATABASES["lai_bundle"].sha256 is None (Phase 0i)' \
  'apply Step 5 (§0i) registry edit, or PR-0c has already re-set it (out of order)' \
  registry_lai_sha_none

check 'database_registry DATABASES["vep_bundle"].url → bundle-v2.0.0 asset (Phase 0i)' \
  'apply Step 5 (§0i) registry URL rewrite' \
  registry_vep_url

check 'build_vep_bundle coverage_report auto-detects 1-col/3-col TSV (Phase 0j)' \
  'apply Step 4 (§0j) _load_catalog_rsids patch; ensure GI env is active for pytest' \
  coverage_report_autodetect

check 'env lock file exists, is non-empty, and is committed (Phase 0k)' \
  'complete Step 11 (§0k) and commit docs/lai-bundle-release-runbook-env.lock.yaml' \
  env_lock_committed

check 'Phase 0 PR (PR-0z) merged — its files + workflow/URL edits are on main' \
  'merge PR-0z (Step 12) and git fetch, or set PREFLIGHT_MAIN_REF' \
  phase0_merged

# ─── Summary ────────────────────────────────────────────────────────────────
if [ "$failed" -gt 0 ]; then
  printf '\nPre-flight: %d/%d checks FAILED — Phase A is NOT ready.\n' "$failed" "$TOTAL" >&2
  exit 1
fi
printf '\nPre-flight: all %d checks passed — Phase A is clear to start.\n' "$TOTAL"
