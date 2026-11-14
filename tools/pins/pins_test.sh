#!/usr/bin/env bash
# Holds three pins.
#
# The Python version, stated in four files that cannot share a constant, is the
# same in all of them:
#   1. PYTHON_VERSION in MODULE.bazel (major.minor);
#   2. python_version of mypy_cli in tools/mypy/BUILD.bazel;
#   3. python_version in mypy.ini;
#   4. target-version in ruff.toml, as py<major><minor>.
#
# The brenn-pod commit is stated once, as BRENN_POD_COMMIT in MODULE.bazel,
# and every use of it goes through that constant:
#   1. exactly one BRENN_POD_COMMIT line, a 40-digit lowercase hex commit;
#   2. every `rev = ` line in MODULE.bazel is `rev = BRENN_POD_COMMIT,`, and
#      there are two, one per hub;
#   3. exactly one "BRENN_POD_BUILD_COMMIT" line, whose value is
#      BRENN_POD_COMMIT, and no line naming the build-id crate's own
#      HIL_BUILD_ values;
#   4. the commit the recorded transcripts' README names is BRENN_POD_COMMIT,
#      so a pin moved without retaking the transcripts fails here.
#
# The pulseaudio crate's version, which the sink client reports, is the one
# the crate hub pins: PULSEAUDIO_VERSION in MODULE.bazel, used by the crate's
# spec, equals PULSEAUDIO_VERSION in brenn_lab/playback/pulse_client/BUILD.bazel.
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

module_bazel="$(rlocation "$MODULE_BAZEL")"
mypy_build="$(rlocation "$MYPY_BUILD")"
mypy_ini="$(rlocation "$MYPY_INI")"
ruff_toml="$(rlocation "$RUFF_TOML")"
transcripts_readme="$(rlocation "$TRANSCRIPTS_README")"
pulse_client_build="$(rlocation "$PULSE_CLIENT_BUILD")"

# Prints the one value a sed script extracts from a file; fails unless there
# is exactly one.
extract() {
    local what="$1" file="$2" script="$3" values count
    values="$(sed -n "$script" "$file")"
    count="$(grep -c . <<<"$values" || true)"
    if [[ "$count" -ne 1 ]]; then
        echo "FAIL: expected one $what in $file, found $count" >&2
        exit 1
    fi
    printf '%s\n' "$values"
}

python_version="$(extract PYTHON_VERSION "$module_bazel" \
    's/^PYTHON_VERSION = "\([^"]*\)"$/\1/p')"
mypy_cli_version="$(extract python_version "$mypy_build" \
    's/^ *python_version = "\([^"]*\)",$/\1/p')"
mypy_ini_version="$(extract python_version "$mypy_ini" \
    's/^python_version = \([^ ]*\)$/\1/p')"
ruff_target="$(extract target-version "$ruff_toml" \
    's/^target-version = "\([^"]*\)"$/\1/p')"

status=0

if [[ "$mypy_cli_version" != "$python_version" ]]; then
    echo "FAIL: python_version in tools/mypy/BUILD.bazel is $mypy_cli_version" \
        "but PYTHON_VERSION in MODULE.bazel is $python_version" >&2
    status=1
fi

if [[ "$mypy_ini_version" != "$python_version" ]]; then
    echo "FAIL: python_version in mypy.ini is $mypy_ini_version" \
        "but PYTHON_VERSION in MODULE.bazel is $python_version" >&2
    status=1
fi

expected_target="py${python_version/./}"
if [[ "$ruff_target" != "$expected_target" ]]; then
    echo "FAIL: target-version in ruff.toml is $ruff_target but PYTHON_VERSION" \
        "in MODULE.bazel is $python_version (expected $expected_target)" >&2
    status=1
fi

brenn_pod_commit="$(extract BRENN_POD_COMMIT "$module_bazel" \
    's/^BRENN_POD_COMMIT = "\([0-9a-f]\{40\}\)"$/\1/p')"
# Any line that assigns BRENN_POD_COMMIT, well-formed or not.
brenn_pod_commit_lines="$(grep -c '^ *BRENN_POD_COMMIT *=' "$module_bazel" || true)"
if [[ "$brenn_pod_commit_lines" -ne 1 ]]; then
    echo "FAIL: expected one assignment of BRENN_POD_COMMIT in MODULE.bazel," \
        "found $brenn_pod_commit_lines" >&2
    status=1
fi

rev_lines="$(grep '^ *rev = ' "$module_bazel" || true)"
rev_count="$(grep -c . <<<"$rev_lines" || true)"
rev_by_constant="$(grep -c '^ *rev = BRENN_POD_COMMIT,$' <<<"$rev_lines" || true)"
if [[ "$rev_count" -ne 2 || "$rev_by_constant" -ne 2 ]]; then
    echo "FAIL: expected two rev lines in MODULE.bazel, each" \
        "'rev = BRENN_POD_COMMIT,'; found:" >&2
    printf '%s\n' "$rev_lines" >&2
    status=1
fi

declared_count="$(grep -c '"BRENN_POD_BUILD_COMMIT"' "$module_bazel" || true)"
declared_by_constant="$(grep -c \
    '^ *"BRENN_POD_BUILD_COMMIT": BRENN_POD_COMMIT,$' "$module_bazel" || true)"
if [[ "$declared_count" -ne 1 || "$declared_by_constant" -ne 1 ]]; then
    echo "FAIL: expected one \"BRENN_POD_BUILD_COMMIT\" line in MODULE.bazel," \
        "'\"BRENN_POD_BUILD_COMMIT\": BRENN_POD_COMMIT,'; found:" >&2
    grep '"BRENN_POD_BUILD_COMMIT"' "$module_bazel" >&2 || true
    status=1
fi
internal_lines="$(grep -c 'HIL_BUILD_' "$module_bazel" || true)"
if [[ "$internal_lines" -ne 0 ]]; then
    echo "FAIL: MODULE.bazel names the build-id crate's own HIL_BUILD_ values" \
        "on $internal_lines lines; declare the commit through" \
        "BRENN_POD_BUILD_COMMIT" >&2
    status=1
fi

readme_commit="$(extract "brenn-pod commit" "$transcripts_readme" \
    's/^brenn-pod commit: `\([^`]*\)`$/\1/p')"
if [[ "$readme_commit" != "$brenn_pod_commit" ]]; then
    echo "FAIL: the recorded transcripts were taken at brenn-pod $readme_commit" \
        "but BRENN_POD_COMMIT in MODULE.bazel is $brenn_pod_commit; retake them" \
        "from the hardware lane's saved outputs" >&2
    status=1
fi

pulseaudio_version="$(extract PULSEAUDIO_VERSION "$module_bazel" \
    's/^PULSEAUDIO_VERSION = "\([^"]*\)"$/\1/p')"
client_pulseaudio_version="$(extract PULSEAUDIO_VERSION "$pulse_client_build" \
    's/^PULSEAUDIO_VERSION = "\([^"]*\)"$/\1/p')"
pulseaudio_spec="$(extract "pulseaudio version spec" "$module_bazel" \
    's/^ *version = "=" + \(PULSEAUDIO_VERSION\),$/\1/p')"
if [[ "$client_pulseaudio_version" != "$pulseaudio_version" ]]; then
    echo "FAIL: PULSEAUDIO_VERSION in brenn_lab/playback/pulse_client/BUILD.bazel" \
        "is $client_pulseaudio_version but in MODULE.bazel is $pulseaudio_version" >&2
    status=1
fi
if [[ "$pulseaudio_spec" != "PULSEAUDIO_VERSION" ]]; then
    echo "FAIL: the pulseaudio crate's spec in MODULE.bazel does not pin" \
        "PULSEAUDIO_VERSION" >&2
    status=1
fi

if [[ "$status" -eq 0 ]]; then
    echo "pins agree: python $python_version, brenn-pod $brenn_pod_commit," \
        "pulseaudio $pulseaudio_version"
fi
exit "$status"
