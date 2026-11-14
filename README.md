# brenn-lab

brenn-lab is an experimental harness for audio and motion work. An experiment
is a script that plays known sounds, records every device that can hear them,
and leaves behind a run directory that explains itself: what was played, what
each device captured, and enough about the setup to make sense of it later.

It is built for many devices on many machines. No experiment assumes one
microphone, one speaker, one sample rate or one host, and a device's stream
need not be audio: pose, fault and status streams are data too.

<!-- TODO(cross-machine-control-waits-on-bus-blob-transfer): control of devices on other machines starts from the owner's answer to this entry. -->

## Status

The run spine and two device role kinds. A script declares the roles it uses
and sends them commands; a lab configuration says which machines and devices
there are. The runner dry-runs a script against a lab, probing every device it
needs without recording anything, and runs it, writing a run directory with a
manifest, a journal of every command, reply and event, and results.

- **Capture** records a device through brenn-pod's recorder, `pod-capture`:
  the one this repository's build pins, or one installed where the lab
  configuration says. The build also pins brenn-pod's reader, `frame-log`.
  Its hardware-lane tests record from an attached reSpeaker Flex through the
  pinned recorder.
- **Playback** plays WAV files through an ALSA card or a sink of the
  desktop's sound server, keeps an exact copy of every play in the run, and
  holds the output under a volume cap and quiet hours that only the lab
  configuration sets. A lab configuration may state that two roles sample on
  one device clock. Its audible-lane test plays through an attached reSpeaker
  Flex and through a sound-server sink, each at the quietest level the
  Flex's microphones detect plus a margin, while the capture role records.
  On a sink the role runs a sound server client this repository builds,
  loads no host sound library, and holds the sink's volume; it has played on
  one Bluetooth speaker.
- **Examples**: `examples/anchoring_scenario.py` plays a talker on one output
  and the robot's own sound on another, overlapping, while a capture records;
  the tree carries one known speech clip, from CMU ARCTIC, to use as the
  talker.
- **Alignment** finds where each play lies in each capture by the sound
  alone, reading no clock. `lab mark` puts a marker before and after a
  source; a play records the marker its source carries; `lab align` searches
  a run's captures for its plays' markers and writes the offsets and drift
  into the run directory. It has been measured on marked plays recorded by a
  reSpeaker Flex, from its own output and from a Bluetooth speaker; two
  overlapping plays, the later one's level found with the earlier playing
  by detections that repeat three runs in a row, have been located together
  in three runs of three at the level that rule gave, and in nine runs of
  nine at that level before.
- **Assessment** says whether a run is usable. At the end of every run that
  was not interrupted, and again with `lab assess`, it measures every channel
  of every capture, states every loss of samples from both the recorder and
  the capture file, reads how each play ended, takes markers and drift from
  alignment, and writes `assessment.json`, whose `answer` is `clean`,
  `findings` or `incomplete`. No finding rests on a threshold.

Roles on other machines cannot be reached.

- `docs/reference/experiments.md`: the lab configuration, writing a script,
  the capture and playback roles, the dry run and the run, marking a source,
  aligning a run and assessing one.
- `docs/reference/run-directory.md`: what a run leaves behind, file by file
  and key by key.

## Contributing

The build, the gates, the test lanes and the rules every change follows are in
`AGENTS.md`.

## Licence

Apache License 2.0; see `LICENSE` and `NOTICE`.
