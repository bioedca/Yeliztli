#!/usr/bin/env bash
set -o pipefail

log_file="${VITEST_STRICT_LOG:-}"
cleanup_log=0

if [[ -z "$log_file" ]]; then
  log_file="$(mktemp -t yeliztli-vitest.XXXXXX.log)"
  cleanup_log=1
fi

if [[ "$cleanup_log" == "1" ]]; then
  trap 'rm -f "$log_file"' EXIT
fi

vitest run "$@" 2>&1 | tee "$log_file"
vitest_status=${PIPESTATUS[0]}

if grep -q "not wrapped in act" "$log_file"; then
  echo "React act warning detected in Vitest output; failing test run." >&2
  exit 1
fi

exit "$vitest_status"
