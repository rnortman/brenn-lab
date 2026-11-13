# Recorder transcripts

Each `.jsonl` file here is a recorder's stdout: the event lines brenn-pod's
`pod-capture` prints, one per line. The stand-in recorder (`../stand_in.py`)
replays them; nothing in brenn-lab builds an event line.

brenn-pod commit: `145886ac600a4512a41c42a065a606e70e7046d8`

## `recorded/`

The stdout of the `pod-capture` this repository pins, built by Bazel at the
commit above, which that build declares, recording a reSpeaker Flex XVF3800 at
16 kHz, `s16_le`, six channels. Each file is the saved output, `<case>.jsonl`,
of the hardware-lane test `//brenn_lab:capture_flex_hardware_test` for its
case: the `logs/mic/recorder-0001.stdout.jsonl` of a run made through
`//brenn_lab:lab` with `examples/ten_seconds.py`.

- `clean.jsonl`: a ten-second capture started and stopped by the script.
- `loss.jsonl`: the same, with the recorder held with SIGSTOP for one second
  five seconds in and then resumed: a `capture_gap` of unknown length.
- `no_card.jsonl`: a start with a `card` that matches no card.

Replaced by hand before committing, and nothing else:

- `path`, by a placeholder path;
- the tail of `device.card_long_name` after " at " (the USB port path), by a
  placeholder port path;
- the entries of `cards`, by one synthetic entry of the same keys, and the same
  card listing where the `detail` of `no_card` repeats it.

These are a tool's event lines; no audio is in them. No replay file sits in
this directory: a recorded transcript replays by the stand-in's default rule.

To retake them: run `make hardware-test` with the Flex attached, take the three
saved outputs from
`bazel-testlogs/brenn_lab/capture_flex_hardware_test/test.outputs/`, make the
replacements above, and set the commit above to the one `MODULE.bazel` pins.

## `authored/`

Cases no lane test produces, written from brenn-pod's
`docs/reference/pod-capture.md` at the commit above. They are held to that
document only, and to the recorded lines wherever the same event was recorded.

- `stream_died.jsonl`: a capture that fails after it started.
- `write_stalled.jsonl` (`held = 1`): a capture that reports `write_stalled`
  once it is told to stop.
- `late.jsonl`: a capture with a `capture_late`.
- `exits_mid_capture.jsonl` (`unasked_exit = 1`): a recorder that exits with
  no terminal line mid-capture.
- `exits_at_stop.jsonl`: the same lines with no replay file, a recorder that
  exits with no terminal line when it is told to stop.
- `stops_unasked.jsonl` (`held = 0`): a recorder that stops cleanly unasked.
- `not_json.jsonl`: a line that is not JSON.
- `unknown_event.jsonl`: an event the contract does not have.
- `after_terminal.jsonl`: a line after the terminal line.
- `second_started.jsonl`: a second `capture_started`.
- `gap_first.jsonl`: a first line that is neither `capture_started` nor
  `capture_failed`.
- `channels_mismatch.jsonl`: a `capture_started` granting more channels than
  its layout names.
- `exit_after_stopped.jsonl` (`held = 0`, `unasked_exit = 3`): a recorder that
  exits 3 after `capture_stopped`.
- `exit_after_failed.jsonl` (`unasked_exit = 0`): a recorder that exits 0
  after `capture_failed`.
- `unknown_reason.jsonl`: a failure reason the contract does not have.
- `silent.jsonl`: empty, a recorder that prints nothing.

A `<name>.replay.toml` beside a transcript says when the stand-in prints its
lines and how it exits; it never says what is printed.
