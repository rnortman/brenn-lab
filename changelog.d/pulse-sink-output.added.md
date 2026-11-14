- A playback role can play on a sink of the desktop's sound server (PulseAudio,
  or PipeWire's PulseAudio-compatible server), such as a Bluetooth speaker:
  `output = "pulse"` with `sink`, a case-insensitive part of the sink's name
  or description. The role runs the sink client the runner carries, which
  its machine names as `pulse_client` (`{ pinned = true }` or a path), and
  loads no host sound library. The sink's volume and mute are the role's one
  control, held under the role's cap; the play's stream is made at 0 dB,
  unmuted, on that sink, and checked at every read. A sink with flat volume
  or no decibel volume is refused, and a `pulse` role may state no
  `sample_clock`.
- A playback role's `device` in a check or a play is the sink as the server
  names it for a `pulse` role.
- `lab assess` calls every marker of a play with an underrun `unknown`,
  however the play ended, when its output is a sink or of a kind it does not
  know: a sound server may skip frames handed after it ran out. On a card
  such a play's markers are still `expected`.
- `make hardware-test` passes `XDG_RUNTIME_DIR` to its tests, and the lane
  gains a read-only test of the sink output against the desktop's sound
  server.
