#!/usr/bin/env bash
# A lane's canary. Passes only when LAB_LANE names the lane given as $1, which
# only `make hardware-test` and `make audible-test` set. If the default test
# filter ever stops excluding a lane, that lane's canary runs without the
# variable and the gate goes red. It touches no device and makes no sound.
set -euo pipefail
lane="${1:?usage: lane_canary.sh <lane>}"
if [[ "${LAB_LANE:-}" != "$lane" ]]; then
    echo "FAIL: the $lane lane ran outside 'make $lane-test' (LAB_LANE='${LAB_LANE:-}')." >&2
    echo "The default test filter must exclude every test tagged $lane." >&2
    exit 1
fi
echo "the $lane lane is wired"
