# brenn-lab

brenn-lab is an experimental harness for audio and motion work. An experiment
is a script that plays known sounds, records every device that can hear them,
and leaves behind a run directory that explains itself: what was played, what
each device captured, and enough about the setup to make sense of it later.

It is built for many devices on many machines. No experiment assumes one
microphone, one speaker, one sample rate or one host, and a device's stream
need not be audio: pose, fault and status streams are data too.

## Status

Scaffolding only. The repository has its build, its gates and a placeholder
target that proves them; it has no harness logic yet.

## Contributing

The build, the gates, the test lanes and the rules every change follows are in
`AGENTS.md`.

## Licence

Apache License 2.0; see `LICENSE` and `NOTICE`.
