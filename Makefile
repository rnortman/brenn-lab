# Repo-root Makefile: a frontend to Bazel, the check gate, and hook wiring.
#
# Bazel is the build. Every recipe here is a spelled-out Bazel invocation, so
# `check` is one definition that the pre-commit hook and CI both run,
# reproducible on a fresh clone with nothing installed but bazelisk.

# Recipes run under bash with -e and pipefail, so a failing command anywhere in a
# chain or a pipeline fails the target. Without this a broken step degrades into a
# green result, which is the worst thing a gate can do.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

.DEFAULT_GOAL := help

SCRUB := bazel run //tools/scrub:brenn-scrub --

CHANGES := bazel run //tools/allthechanges --

.PHONY: help
help:
	@echo "Repo-root targets (all of them run Bazel):"
	@echo "  make check          the gate: scrub kit, fragment lint, build, test, ruff, mypy, buildifier, tree scan"
	@echo "  make build          build every target"
	@echo "  make test           run every default test, without the lint aspect"
	@echo "  make hardware-test  run the hardware lane: tests that need a device attached"
	@echo "  make audible-test WINDOW=<agreed window>"
	@echo "                      run the audible lane: tests that play sound or move a robot"
	@echo "  make fix            ruff (format and lint fixes) and buildifier the tree in place"
	@echo "  make repin          regenerate the Python lock and both gate-tool crate locks"
	@echo "  make todo-gather    fold todo.d fragments into TODO.md"
	@echo "  make changes        run allthechanges (ARGS=\"query\", ARGS=\"todo query\", ...)"
	@echo "  make setup          wire git at .githooks (once per clone)"
	@echo "  make scrub-tree     whole-tree secret sweep"
	@echo "  make clean          drop Bazel's outputs for this workspace"

# The gate, in five steps. The scrub kit check is cheap and its failure the
# most important, so it runs first. The fragment lint follows: a malformed
# changelog.d or todo.d fragment fails here rather than at release time. One
# `bazel test` then carries the build of every target, every default test
# (pytest, ruff, buildifier, the pins), and (via the config) mypy over every
# Python target. The tree scan runs last: it is the backstop that sees the
# whole tracked tree. Every tool is pinned in MODULE.bazel and fetched by
# checksum, so no lane can skip.
# TODO(dependency-audit-lane): a licence and advisory audit of the dependency graph, through
# Bazel, as its own target, never run by check.
.PHONY: check
check:
	$(SCRUB) init --check
	$(CHANGES) query
	$(CHANGES) todo query
	bazel test --config=lint //...
	$(SCRUB) tree

.PHONY: build
build:
	bazel build //...

.PHONY: test
test:
	bazel test //...

# The two lanes. .bazelrc filters both tags out of every `bazel test`; these
# targets replace that filter, and set LAB_LANE so the lane's canary passes.
# hardware-test excludes audible, so a test carrying both tags runs only under
# audible-test and its WINDOW.
.PHONY: hardware-test
hardware-test:
	bazel test --test_tag_filters=hardware,-audible --test_env=LAB_LANE=hardware //...

# Anything audible needs the maintainer's agreement on when. WINDOW is a speed
# bump, not an enforcement: it keeps the lane off tab completion and puts the
# rule on screen at the moment it matters.
.PHONY: audible-test
audible-test:
	@if [[ "$(origin WINDOW)" != "command line" || -z "$(WINDOW)" ]]; then \
	    echo "make audible-test: the audible lane plays sound or moves a robot." >&2; \
	    echo "Ask the maintainer for a time window, wait for it, and batch audible work" >&2; \
	    echo "into as few windows as possible. Then: make audible-test WINDOW=<the agreed window>" >&2; \
	    exit 1; fi
	bazel test --test_tag_filters=audible --test_env=LAB_LANE=audible //...

# Passthrough to allthechanges for queries and dry runs:
# `make changes ARGS="todo query"`,
# `make changes ARGS="gather --version 0.1.0 --dry-run"`.
ARGS ?=
.PHONY: changes
changes:
	$(CHANGES) $(ARGS)

.PHONY: todo-gather
todo-gather:
	$(CHANGES) todo gather

# Scoped to what `check` enforces that has a fixer, so `make fix && make check`
# is a clean cycle: ruff's format and lint fixes, then buildifier. mypy
# findings are fixed by hand.
.PHONY: fix
fix:
	bazel run //tools/ruff:fix
	bazel run //:buildifier

# Regenerates requirements_lock.txt from requirements.in with the pinned uv
# and the hermetic interpreter, then re-renders both gate-tool crate hubs,
# tools/scrub's and tools/allthechanges's, with the pinned cargo; a hub whose
# inputs did not change renders byte-identically. Commit every lock file that
# changed.
.PHONY: repin
repin:
	bazel run //:requirements.update
	CARGO_BAZEL_REPIN=1 bazel fetch --repo=@scrub_crates --repo=@allthechanges_crates

# Wire git at the tracked hooks directory through the scrub kit's own init,
# which sets core.hooksPath, removes a stale .git/hooks/pre-commit and lays
# nothing that is already present. Idempotent; run once per clone.
.PHONY: setup
setup:
	@command -v bazel >/dev/null 2>&1 || { \
	    echo "bazel not found on PATH: install bazelisk as 'bazel'; every gate runs through it." >&2; \
	    exit 1; }
	$(SCRUB) init

.PHONY: scrub-tree
scrub-tree:
	$(SCRUB) tree

.PHONY: clean
clean:
	bazel clean
