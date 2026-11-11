#!/usr/bin/env bash
# ruff check and ruff format --check over every Python file in the working
# tree. Runs unsandboxed (tag no-sandbox): it finds the tree through the
# MODULE.bazel runfile, a symlink chain that ends in the source file.
set -uo pipefail
ruff="$(realpath "$1")"
workspace="$(dirname "$(realpath "$2")")"
cd "$workspace" || exit 2
status=0
"$ruff" check --no-cache . || status=1
"$ruff" format --check --no-cache . || status=1
exit "$status"
