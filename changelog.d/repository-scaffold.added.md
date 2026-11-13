- First release of brenn-lab, an experimental harness for audio and motion
  work, published under the Apache 2.0 licence.
- Bazel is the only build. The Python interpreter and packages, ruff, mypy and
  every tool the checks use are pinned and downloaded by checksum. On linux
  x86_64 a fresh clone needs only bazelisk, git and make. `make check` is the
  one gate. It runs the build, the tests, ruff, mypy, buildifier, a lint of
  changelog and TODO fragments, and a secret scan.
- Secret scanning (brenn-scrub) runs before every commit and push once
  `make setup` has been run, and on every file Claude Code writes. GitHub
  Actions runs `make check` on every push to `main` and on every pull
  request.
- Tests that need a device attached, or that play sound or move a robot, go in
  separate lanes, run with `make hardware-test` and `make audible-test`. The
  default check never runs them.
- Changelog entries and TODO entries are kept as small fragment files under
  `changelog.d/` and `todo.d/`. A release gathers them into `CHANGELOG.md`
  and `TODO.md`.
