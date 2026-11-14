- A script can now say when a play starts, and the playback role starts it
  then. Give `playback.Play` a `start`: a reading of the playback role's
  own clock, taken with the new `playback.Clock()` command (which touches
  no output) and moved forward with `ClockReading.after(seconds)`. The role
  does all its slow work before the start (checking the source, keeping
  its copy, opening the output, setting the level), replies, and at the
  start only hands over the first frames. Because the start is on the
  role's clock, a script works the same when its roles are on other
  machines. Quiet hours are judged up to the end of the sound.
- A start on another machine's clock is refused with `start_clock`, in a
  dry run too. A start that has already passed by the time the output is
  ready, or that the role wakes for more than 20 ms late, is refused with
  `start_passed`; no sound is played.
- The run records how close each timed play came: its manifest entry and
  `results.json` record carry `asked_start`, and the record also carries
  `woke` and `started_after_us` (how long after the asked start the first
  frames went out, in microseconds on the role's clock). The assessment
  copies `asked_start` and `started_after_us` into each play's record; no
  finding depends on them. A timed play's reply has `first: null`; its
  first mark arrives with its end (`playback.ended`, `playback.failed` or
  the `playback.stopped` reply).
