- Each entry of `assessment.json`'s `captures` now states the device's state
  as the recorder gave it: `device_state` with its `outcome` (`read`,
  `unavailable` or `not_attempted`) and, for a state that was not read, the
  recorder's `why` and `detail`. No outcome is a finding, and none changes the
  run's `answer`, `lab assess`'s exit status or `lab run`'s `usability`. A
  manifest whose capture entry has no `device_state`, or one outside those
  three outcomes, is refused `unreadable`.
