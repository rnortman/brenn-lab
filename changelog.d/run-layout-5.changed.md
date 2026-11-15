- The run directory is now layout 5, and `lab align` and `lab assess` refuse
  layouts 1 to 4. `results.json` is now `schema` 3 and `assessment.json`
  `schema` 2: a play's `restored` may be null when the play's end was never
  reported. A dry run's report and a refusal stay `schema` 2.
- A role that raises when the runner releases it no longer leaves the run
  with no `results.json` and exit status 3. The raise is recorded as that
  role's failure (`role_raised`, command `release`), the run is assessed,
  and `lab run` exits 1. Status 3 now means only that the runner could not
  write its own files. In that case `runner.log` ends with
  `the run could not be finished` and the traceback.
- Each line of `logs/runner.log` is now a JSON object, `{"at", "text"}`,
  stamped with the runner's clock like the journal's lines. The file exists
  from the start of every run. A recorder's pid is now in the journal, not
  in `runner.log`.
- An error raised while the runner records a program a role started is now
  that role's failure (`role_raised`), not `role_io`, and the program is
  killed first.
