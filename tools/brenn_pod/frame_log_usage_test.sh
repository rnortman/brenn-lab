#!/usr/bin/env bash
# Asserts that the pinned frame-log, run with no arguments, exits 2 with an
# empty stdout and its usage as the first line of stderr.
set -euo pipefail

# --- begin runfiles.bash initialization v3 ---
# Copy-pasted from the Bazel Bash runfiles library v3.
set -uo pipefail; set +e; f=bazel_tools/tools/bash/runfiles/runfiles.bash
# shellcheck disable=SC1090
source "${RUNFILES_DIR:-/dev/null}/$f" 2>/dev/null || \
  source "$(grep -sm1 "^$f " "${RUNFILES_MANIFEST_FILE:-/dev/null}" | cut -f2- -d' ')" 2>/dev/null || \
  source "$0.runfiles/$f" 2>/dev/null || \
  source "$(grep -sm1 "^$f " "$0.runfiles_manifest" | cut -f2- -d' ')" 2>/dev/null || \
  source "$(grep -sm1 "^$f " "$0.exe.runfiles_manifest" | cut -f2- -d' ')" 2>/dev/null || \
  { echo>&2 "ERROR: cannot find $f"; exit 1; }; f=; set -e
# --- end runfiles.bash initialization v3 ---

frame_log="$(rlocation "$FRAME_LOG")"
if [[ ! -x "$frame_log" ]]; then
    echo "FAIL: $FRAME_LOG not found in runfiles" >&2
    exit 1
fi

stdout_file="$TEST_TMPDIR/stdout"
stderr_file="$TEST_TMPDIR/stderr"
status=0
"$frame_log" >"$stdout_file" 2>"$stderr_file" || status=$?

if [[ "$status" -ne 2 ]]; then
    echo "FAIL: frame-log with no arguments exited $status, expected 2" >&2
    printf '%s\n' "$(<"$stderr_file")" >&2
    exit 1
fi
if [[ -s "$stdout_file" ]]; then
    echo "FAIL: frame-log with no arguments printed on stdout:" >&2
    printf '%s\n' "$(<"$stdout_file")" >&2
    exit 1
fi
first_line=""
IFS= read -r first_line <"$stderr_file" || true
if [[ "$first_line" != "usage: frame-log summary <log>" ]]; then
    echo "FAIL: first line of stderr is not the usage:" >&2
    printf '%s\n' "$(<"$stderr_file")" >&2
    exit 1
fi
echo "frame-log prints its usage and exits 2"
