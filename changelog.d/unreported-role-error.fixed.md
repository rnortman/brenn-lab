- An error a role raises while handling a command now fails the run, even
  when the script catches it: the results name it, `where` `role`, `reason`
  `role_raised`, with the role and the command, and its traceback is in
  `logs/runner.log`. A capture whose recorder reader failed, or a play whose
  thread failed before reporting its end, no longer goes unseen to the end
  of the run: the role's next start, stop, play or release raises the
  error. Raised at release, it leaves the run with no `results.json`.
  Raised by a command of the script's, it ends the run `failed`; that
  capture's or play's record still keeps `ended` `null`, and the run's
  assessment refuses the run as `unreadable`.
