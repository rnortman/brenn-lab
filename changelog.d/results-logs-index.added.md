- A run now keeps the output of every process in it, and `results.json`
  lists every log file. Nothing a run's processes write is left only on a
  terminal or only in memory:
  - the `lab` process's own stdout and stderr, which hold a script's
    `print`, a library's messages and the output of any program the script
    starts, are saved as `logs/lab.stdout.log` and `logs/lab.stderr.log`.
    `lab run`'s stdout now holds only its JSON document. A dry run, which
    has no run directory, prints the script's output on stderr;
  - the sink client (the program a playback role runs to play on a
    sound-server sink) saves both of its streams for every check and play,
    as `logs/<role>/pulse-client-<NNNN>.stdout.jsonl` and `.stderr.log`. A
    capture role's `check` saves its probe's output the same way, as
    `probe-<NNNN>`;
  - each program a role starts is a `process.started` event in the run's
    journal, with its pid and where its logs are, so a run whose runner died
    still says where every log is;
  - a traceback from any thread of the `lab` process, and from the closing
    assessment, is written to `logs/runner.log`. After a run that did not
    complete, or in which a thread raised, `lab run` prints one line on
    stderr saying where to look.
- The new top-level `logs` section of `results.json` lists every log in the
  order its process started, giving the role, program, pid and file. Every
  failure the run records has a `log` field naming the file that explains
  it: `logs/runner.log` for a raise in the `lab` process, otherwise the
  stderr of the failing role's last process.
