"""A stand-in for brenn-pod's `frame-log export-wav`, for tests.

TODO(alignment-reader-stand-in-in-default-check)

It takes the reader's command line, `export-wav <log> <out>`, and reads
`<log>` as a small JSON file a test wrote, never as a frame log:

- `{"wav": <path>, "map": <value>}`: copy the prepared WAV at `wav` (relative
  to `<log>`'s directory) to `<out>`, print `map` as one JSON line, exit 0;
- `{"stdout": <text>}`: print `text` as it is and exit 0, writing nothing;
- `{"status": <n>, "stderr": <text>}`: print `text` on stderr and exit `n`,
  writing nothing.

As the reader does, it refuses an existing `<out>` with status 1 and a command
line it cannot use with status 2.
"""

import json
import shutil
import sys
from pathlib import Path

USAGE = "usage: frame-log export-wav <LOG> <OUT>"


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "export-wav":
        print(USAGE, file=sys.stderr)
        return 2
    log, out = Path(argv[1]), Path(argv[2])
    if out.exists():
        print(f"error: {out} already exists", file=sys.stderr)
        return 1
    try:
        told = json.loads(log.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {log}: {e}", file=sys.stderr)
        return 1
    if "status" in told:
        print(told["stderr"], file=sys.stderr)
        status: int = told["status"]
        return status
    if "stdout" in told:
        sys.stdout.write(told["stdout"])
        return 0
    shutil.copyfile(log.parent / told["wav"], out)
    print(json.dumps(told["map"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
