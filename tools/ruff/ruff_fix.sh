#!/usr/bin/env bash
# Applies ruff's lint fixes, then its formatter, to the whole working tree.
set -euo pipefail
if [[ -z "${BUILD_WORKSPACE_DIRECTORY:-}" ]]; then
  echo "run me with 'bazel run //tools/ruff:fix'" >&2
  exit 2
fi
ruff="$(realpath "$1")"
cd "$BUILD_WORKSPACE_DIRECTORY"
"$ruff" check --no-cache --fix .
"$ruff" format --no-cache .
