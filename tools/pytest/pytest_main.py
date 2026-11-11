"""Main for every py_pytest_test: runs pytest over the files named in argv."""

import os
import sys

import pytest


def main() -> int:
    # Runfiles are read-only, so pytest's cache plugin is off.
    args = ["-p", "no:cacheprovider", *sys.argv[1:]]
    xml = os.environ.get("XML_OUTPUT_FILE")
    if xml:
        args.append(f"--junitxml={xml}")
    code = pytest.main(args)
    # Bazel already fails any non-zero exit; the message says why.
    if code == pytest.ExitCode.NO_TESTS_COLLECTED:
        sys.stderr.write("pytest collected no tests\n")
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
