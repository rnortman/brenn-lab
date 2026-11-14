- `lab run` assesses, and so aligns, every run at its end, except a run
  that was interrupted. Its pointer gains `usability`: the assessment's
  `answer` and the path of `assessment.json`, or `not_assessed` with the
  reason none was made. The exit status still says only whether the run
  completed. A signal during the closing assessment ends the assessment
  and leaves the run's pointer and exit status. `lab run` takes
  `--frame-log <path>` for the reader its assessment runs, and refuses one
  that is not an executable file before anything is created. A run now loads
  numpy, and with it the host's C++ runtime and zlib, at its end.
- `results.json`'s `usability` keeps `assessed: false`, says that the run's
  assessment is `assessment.json`, and gains `see: "assessment.json"`.
