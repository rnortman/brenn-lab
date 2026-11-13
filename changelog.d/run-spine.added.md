- The run spine. An experiment is a Python script that declares the roles it
  uses and sends them commands; a TOML lab configuration says which machines
  and devices there are. `make lab ARGS="dry-run …"` checks a script against a
  lab without recording anything, probing every device it needs and tracing
  the script with every command dry. `make lab ARGS="run …"` runs it and
  writes a run directory: the script and configuration as run, a manifest of
  every revision and device fact, a journal of every command, reply and event,
  and results that say whether the run completed and what it recorded. Every
  run directory hides itself from git. Every device is released however a
  script ends, and Ctrl-C or SIGTERM stops a run cleanly. See `docs/reference/experiments.md`
  and `docs/reference/run-directory.md`.
- The capture role, which records every channel of one device to a frame log
  through brenn-pod's recorder, `pod-capture`, installed where the lab
  configuration says. Gaps, late blocks and failures are reported by name;
  the device's firmware and configuration are recorded as not read.
- The runner states the brenn-lab commit it was built from, stamped at build
  time.
