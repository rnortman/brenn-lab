#!/usr/bin/env bash
# Holds the sink client to its start without a server: given a socket where
# nothing listens it answers one line, `no_server` naming the socket, and
# exits 1; given a cookie path it cannot read it answers nothing, names the
# path on stderr and exits 1; given no socket it prints its usage and exits 2.
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

client="$(rlocation "$PULSE_CLIENT")"
socket="$TEST_TMPDIR/absent/native"

status=0
out="$("$client" --socket "$socket" </dev/null)" || status=$?
if [[ "$status" -ne 1 ]]; then
    echo "FAIL: exit $status with no server, expected 1" >&2
    exit 1
fi
lines="$(grep -c . <<<"$out" || true)"
if [[ "$lines" -ne 1 ]]; then
    echo "FAIL: expected one line, got $lines: $out" >&2
    exit 1
fi
if ! grep -q '"ok":false' <<<"$out" || ! grep -q '"error":"no_server"' <<<"$out" ||
    ! grep -qF "$socket" <<<"$out"; then
    echo "FAIL: expected no_server naming $socket, got: $out" >&2
    exit 1
fi

home="$TEST_TMPDIR/home"
mkdir -p "$home/.config/pulse/cookie"
status=0
cookie_out="$(HOME="$home" "$client" --socket "$socket" </dev/null 2>"$TEST_TMPDIR/stderr")" ||
    status=$?
if [[ "$status" -ne 1 || -n "$cookie_out" ]] ||
    ! grep -qF "$home/.config/pulse/cookie" "$TEST_TMPDIR/stderr"; then
    echo "FAIL: an unreadable cookie gave exit $status, stdout '$cookie_out'," \
        "stderr '$(cat "$TEST_TMPDIR/stderr")'; expected exit 1, no line, the path" >&2
    exit 1
fi

status=0
"$client" </dev/null >/dev/null 2>&1 || status=$?
if [[ "$status" -ne 2 ]]; then
    echo "FAIL: exit $status with no arguments, expected 2" >&2
    exit 1
fi

echo "no_server answered: $out"
