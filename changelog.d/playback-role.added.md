- The harness can now play sound. A new kind of role, `playback`, plays WAV
  files from its configured `source_dir` through an ALSA sound card, found by
  card name. If the card can't take a file's sample rate, channel count or
  sample format exactly, the file is refused rather than converted. Safety
  limits are enforced inside the role, so no script can get around them.
  `cap_mb` caps where the role sets the card's volume controls, measured as
  the level of a full-scale sample. `quiet_hours` refuses any play that would
  run into them. Every run keeps an exact copy of everything it played under
  `sources/<role>/`, and the run's results gain `sources` and `played`.
  `examples/play_and_record.py` plays a clip while another role records.
- `make audible-test` now runs a play-and-record test on an attached reSpeaker
  Flex. It needs a speaker on the board, and no firmware on the board's
  XIAO ESP32 module that configures the XVF3800.
- A role of any kind may state `sample_clock`, a name. Two roles that state
  the same name are declared to sample on one device clock. This is never
  inferred, and the manifest records it for each role. What a play-and-record
  run on one device can establish between the two sides is not yet settled.
- In events, failures are now reported the same way for every role kind, in
  a field named `failure`. When a role is released, it reports what was
  still running.
