A thread of the `lab` process can raise in a run that completes: a reader or
copier thread whose loss nothing later depended on. Its traceback is in
`logs/runner.log` and `lab run` says so on stderr, but `results.json` says
`completed` with no failure and names the traceback nowhere, so a program
reading the result does not learn of it. The decision to make is whether such
a run is a failed run, or completes with the raise stated in the result, and
under which key. Done when a program holding only `results.json` can tell
that a thread raised during the run. The marker is at the thread hook in
`brenn_lab/runner.py`.
