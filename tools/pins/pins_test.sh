#!/usr/bin/env bash
# Asserts that the Python version, stated in four files that cannot share a
# constant, is the same in all of them:
#   1. PYTHON_VERSION in MODULE.bazel (major.minor);
#   2. python_version of mypy_cli in tools/mypy/BUILD.bazel;
#   3. python_version in mypy.ini;
#   4. target-version in ruff.toml, as py<major><minor>.
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

if [[ "$status" -eq 0 ]]; then
    echo "pins agree: python $python_version"
fi
exit "$status"
