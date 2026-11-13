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

The run spine and one device role. A script declares the roles it uses and
sends them commands; a lab configuration says which machines and devices there
are. The runner dry-runs a script against a lab, probing every device it needs
without recording anything, and runs it, writing a run directory with a
manifest, a journal of every command, reply and event, and results. The one
role kind is capture, which records a device through brenn-pod's recorder,
`pod-capture`, installed where the lab configuration says. There is no
playback, alignment or run-validity checking yet, and roles on other machines
cannot be reached.

- `docs/reference/experiments.md`: the lab configuration, writing a script,
  the capture role, the dry run and the run.
- `docs/reference/run-directory.md`: what a run leaves behind, file by file
  and key by key.

## Contributing

The build, the gates, the test lanes and the rules every change follows are in
`AGENTS.md`.

## Licence

Apache License 2.0; see `LICENSE` and `NOTICE`.
