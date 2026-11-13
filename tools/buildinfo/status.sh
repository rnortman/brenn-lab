#!/usr/bin/env bash
# Bazel's workspace status command: the one place the build reads git. Prints
# the commit of the workspace's checkout and whether a tracked file differs
# from it (untracked files do not count), or `unknown` for both when the
# workspace is not the top of a git checkout or git fails. Bazel fails the
# build when this exits non-zero, so it always exits 0. It takes no lock in
# the checkout: a build must not block the user's own git commands.
#
# TODO(correctness-clean-stamp-hides-untracked-file-named-in-build): a commit
# that names a new source file in a BUILD file without adding the file stamps
# clean, though the runner holds code no commit has.

commit=unknown
dirty=unknown
if top=$(git rev-parse --show-toplevel 2>/dev/null) &&
    [[ "$top" -ef "$PWD" ]] &&
    head=$(git rev-parse --verify --quiet HEAD 2>/dev/null) &&
    changes=$(git --no-optional-locks status --porcelain --untracked-files=no 2>/dev/null); then
    commit=$head
    if [[ -n "$changes" ]]; then dirty=true; else dirty=false; fi
fi
echo "STABLE_BRENN_LAB_COMMIT $commit"
echo "STABLE_BRENN_LAB_DIRTY $dirty"
exit 0
