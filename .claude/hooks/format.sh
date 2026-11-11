#!/bin/bash
# Claude Code PostToolUse: format the one file just written, with the same
# pinned tools the gate checks it with. Silent; a parse error in mid-edit code
# is the gate's business, not this hook's.
set -u
payload="$(cat)"
# A crude filter that only saves the Bazel call: a payload it lets through is
# still parsed and decided by //tools/format:hook. The suffixes match
# BAZEL_NAMES, BAZEL_SUFFIXES and `.py` in tools/format/format_hook.py; a
# suffix added there is added here too.
formatted='"file_path": *"[^"]*(\.py|\.bzl|\.bazel|BUILD)"'
[[ "$payload" =~ $formatted ]] || exit 0
cd "$CLAUDE_PROJECT_DIR"
bazel run --noshow_progress --ui_event_filters=-info \
    //tools/format:hook <<<"$payload" >/dev/null 2>&1
exit 0
