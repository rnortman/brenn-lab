- A run can now say plainly whether it is usable. `lab assess --run <dir>`
  measures every channel of every capture (level, offset, whether it is
  silent or stuck on one value, and samples at the edge of the format),
  states every loss of samples from both the recorder's account and the
  capture file, reads how each play ended, and takes markers and drift from
  alignment, which it runs in the same pass. It writes `alignment.json` and
  then `assessment.json`, whose `answer` is `clean`, `findings` or
  `incomplete`. A finding names a fact about the run, never a level against
  a threshold; `not_assessed` names everything that was not examined. It
  exits 0 on `clean` and 1 otherwise, and `--frame-log <path>` points at
  another reader.
