# brenn-lab — agent instructions

## What this is

brenn-lab is an experimental harness for audio and motion work: experiment
scripts that play known sounds, record every device, and leave a run directory
that explains itself. Charter and status: `README.md`. Read it first.

## Build: Bazel, and only Bazel

Bazel is the build. The Makefile is a frontend whose every target is a
spelled-out `bazel` invocation; there is no target that runs `pip`, `uv`,
`python` or `cargo` on the host, and the gate never will.

- Every toolchain and every tool the gate runs is pinned in `MODULE.bazel` and
  fetched by checksum. On linux x86_64 the only host prerequisites are
  bazelisk (as `bazel`), git and make, on a base system that has `bash`,
  `grep`, `sed` and the coreutils `env`, `uname`, `dirname`, `cut`, `tr` and
  `wc`. On linux aarch64 the C toolchain has no pinned sysroot, so building
  the gate tools also needs the host's libc headers and libraries.
- Nothing in a build or test action may reach for a host-installed program.
  A test that needs a service, a fixture generator or a linter gets it as a
  Bazel dependency. If a tool is genuinely impractical to bring in through
  Bazel, pick a different tool, and if that is not possible either, surface it
  as a decision rather than adding a host dependency.
- Python dependencies are `requirements.in` and its lock,
  `requirements_lock.txt`, hash-pinned for both linux CPUs and resolved by the
  pinned uv with the hermetic interpreter. `make repin` regenerates it and the
  two gate-tool crate locks. After any change to `requirements.in` or a tool
  bump, run it and commit every lock file that changed.
- Python tests in BUILD files use `py_pytest_test` from
  `//tools/pytest:defs.bzl`.
- The Rust and LLVM toolchains exist only to build the two gate tools,
  `//tools/scrub` (the secret scanner) and `//tools/allthechanges` (the
  changelog and TODO fragment tool), each pinned by its own `Cargo.lock` and
  `cargo-bazel-lock.json`. The repository has no Rust of its own.
- The Python version is written in `MODULE.bazel`, `tools/mypy/BUILD.bazel`,
  `mypy.ini` and `ruff.toml`; `//tools/pins:pins_test` holds them equal.

## Commands

| Command | What it does |
|---|---|
| `make check` | The gate: scrub kit check, fragment lint, build, every default test, ruff, mypy, buildifier, tree scan |
| `make build` | Build every target |
| `make test` | Every default test, without the lint aspect (the inner loop) |
| `make hardware-test` | Run the hardware lane: tests that need a device attached |
| `make audible-test WINDOW=...` | Run the audible lane: tests that play sound or move a robot. Refuses without `WINDOW` (see Test lanes) |
| `make fix` | ruff (format and lint fixes) and buildifier the tree in place |
| `make repin` | Regenerate the Python lock and both gate-tool crate locks |
| `make todo-gather` | Fold `todo.d/` fragments into `TODO.md` |
| `make changes ARGS=...` | Run allthechanges: `ARGS="query"`, `ARGS="todo query"`, `ARGS="gather --version X.Y.Z --dry-run"` |
| `make setup` | Wire git at `.githooks` (once per clone; `make check` fails until it has run) |
| `make scrub-tree` | Whole-tree secret sweep |
| `make clean` | Drop Bazel's outputs for this workspace |

## Gates

- A commit scans the staged change for secrets and then runs `make check`,
  which also lints every `changelog.d/` and `todo.d/` fragment.
- A push scans the range being pushed.
- CI runs `make check` on every push to `main` and every pull request, with
  the module lockfile in error mode, so a resolution nobody committed is a red
  build.
- The Claude Code write hook scrubs every write before it lands and blocks on
  any failure, including a scanner that fails to start.

Every scan runs `bazel run //tools/scrub:brenn-scrub`. Treat the local hooks
as the gate and CI as a backstop, not the other way round: a CI run says
nothing about the commit sitting in your working tree.

If a gate blocks a write or a commit, **surface it**. Never route around it.
The gate being wrong is a thing that happens and is worth reporting; a
bypassed gate is not recoverable once it has been pushed.

One case is not a route-around. The write hook builds the scanner from the
working tree, so an edit that breaks a file the scanner's build reads (for
example `MODULE.bazel`, `.bazelversion`, `.bazelrc`, or anything under
`tools/scrub/`) makes the hook fail on every later write, the repair included.
When the hook's output shows that the scanner could not be built, and not that
it ran and reported a finding, and the failure follows an edit you made,
read `git diff HEAD -- <path>`, restore the file you broke to its last
committed content with `git restore --source=HEAD -- <path>`, then make every
change that diff showed again with the editing tool, not only the one that
broke it. Restore nothing else, write nothing by any other route, and say
what happened in your report of the work, quoting the hook's output. If no edit
of yours is behind the failure, or the hook still fails after the restore,
stop and report. To stay out of the lockout, make every edit to such a file
leave the build loadable: add a `load` before the rule that uses it.

Text an agent writes by hand into a file in this repository goes through its
file-editing tools, so the write hook scans it. What the repository's own
commands generate or rewrite (lock files, the files `make setup` lays,
formatter and fixer output) is not written by hand.

## Test lanes

A lane is a Bazel tag. Two lanes exist, and neither runs under `make check`:

- `hardware`: a test that needs a device attached. Recording may happen at any
  time, so this lane needs no permission to run.
- `audible`: a test that plays sound or moves a robot, a robot's speaker and
  motors included. A test is in one lane: an audible test is tagged `audible`
  only, even when it also needs a device.

Every lane test also carries `external`, so a result that depends on a device
is never served from cache.

- `.bazelrc` filters both tags out of every `bazel test`, whoever runs it.
  The lane targets still build and are type-checked under `make check`.
  A lane test without its tag would run in the default check: tag it.
  `bazel run` of a lane test bypasses the filter; the rules below cover
  running a lane test by any route.
- Run a lane with `make hardware-test` or `make audible-test`. The canaries in
  `tools/lanes/` pass only inside their lane and fail the default check if the
  filter ever stops excluding them.
- Hardware-in-the-loop tests assert the expected behaviour and are allowed to
  fail. They are not throwaway probes. An unexpected hardware reading goes to
  the maintainer before any test is changed to pass.
- Anything audible needs the maintainer's agreement on when. Ask for a time
  window, wait for it, and batch audible work into as few windows as possible.
  `make audible-test` refuses unless `WINDOW=<the agreed window>` is given;
  that is a reminder, not an enforcement.

## Design rules

- No design may assume one machine, one capture device or one playback
  device; a channel count, sample rate or sample format; that every device is
  an audio device (pose, fault and status streams are data too); that devices
  share a clock; or that whoever runs an experiment is on the devices'
  machine.
- Generality lives in the data model, the interfaces and the seams, not in
  implementations nothing uses. Of any abstraction a reviewer may ask which
  part of the harness's purpose needs it.
- Volume caps and quiet hours are enforced below the script, in the playback
  role, so no script and no LLM can exceed them. Each playback device has its
  own cap.

## Devices

- Do not flash the XVF3800. It needs a physical button held, so only the
  maintainer can. If work needs a different image, ask. (That chip, as
  stated; not a ban on flashing in general.)
- Anything that arms or moves a Reachy follows that project's
  fault-management doctrine:
  <https://github.com/rnortman/brenn-reachy/blob/main/docs/fault-management.md>.
- Nothing pushed to a device in development touches its flash.

## Sibling repositories

brenn-lab never re-implements a wire format it does not own. Frame logs,
stream frames, motion scripts and bus messages are read and written by their
owners' code, linked in or called as a tool. An owner's crate that cannot be
used from outside is a finding for that owner's repository.

## Data and licences

- Recordings never enter the repository. Fixtures are synthetic or carry a
  licence that allows redistribution, and a fixture the ignore globs would
  hide is re-included by its own named `!` line in `.gitignore`.
- Port only from permissively licensed references; nothing from XMOS's
  licensed packages.

## Principles

- Fail fast. Invalid input is rejected, not coerced. No fallbacks that hide a
  missing authority.
- No over-engineering, no premature abstraction, no hypothetical features.
  Scope discipline: do what was asked.

## Code standards

- Python 3.14, the pinned hermetic interpreter.
- `make check` must pass: ruff format and lint, mypy strict, buildifier,
  every default test.
- No unused code. No backwards-compatibility shims.
- Default branch: `main`.

## Changelog

Every user-visible change adds a fragment, `changelog.d/<slug>.<section>.md`,
where `<section>` is one of `added`, `changed`, `deprecated`, `removed`,
`fixed`, `security`. A fragment is a Markdown list: its first line starts with
`- `, continuation lines are indented two spaces. Never edit `CHANGELOG.md`;
`make changes ARGS="gather --version X.Y.Z"` gathers the fragments into it at a
release. `make changes ARGS="query"` lints and lists what is pending.

## TODO system

Two pieces that stay in sync:

- The master list: `TODO.md` at the repo root plus the fragments under
  `todo.d/` not yet gathered into it. Each entry has a slug, a description,
  and the deferral context.
- `TODO(slug)` comments in code, marking the spot where the work happens.

A new entry is a fragment, `todo.d/<slug>.md`: its first line is text, it has
no ATX heading outside a code fence, and every fence is closed. Never add an
entry by editing `TODO.md`. `make todo-gather` folds the fragments into
`TODO.md`; between gathers `make changes ARGS="todo query"` lists them.
Removing a done TODO deletes its fragment if it still is one, else its
`` ## `<slug>` `` entry in `TODO.md`, and its comment.

Slugs are the join key; adding a TODO requires both halves. Don't use TODOs for
vague aspirations. Every TODO describes a concrete thing, in a place where
"done" is obvious.

## Public tree

Everything tracked here is published. `TODO.md`, `CHANGELOG.md`, their
fragments under `todo.d/` and `changelog.d/`, commit messages and every code
comment are public writing: no internal host names, no absolute paths into
other trees, no references to documents a public reader cannot follow. Design
documents live outside this repository and are never cited from code.
