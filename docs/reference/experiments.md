# Experiments: the lab configuration, scripts, the dry run and the run

An experiment is a script run against a lab. The **lab configuration** says
which machines and devices there are. The **script** says what to do with
them. The **dry run** checks a script against a lab without recording
anything, and the **run** carries it out and leaves a run directory, which
`run-directory.md` describes. **Marking** a source lets a run's captures be
**aligned** with what it played afterwards, by the sound alone (*Marking a
source*, *Aligning a run*). Every run that was not interrupted is
**assessed** at its end, and `lab assess` assesses one again: the run then
says whether it is usable (*Assessing a run*).

## The lab configuration

A TOML file, read from wherever the caller says. Schema 1:

```toml
schema = 1

[machines.bench.programs]
pod_capture = { pinned = true }    # the recorder this harness's build pins
# or: pod_capture = "/opt/brenn-pod/bin/pod-capture", one installed at that path on bench

[roles.mic]
kind = "capture"
machine = "bench"

[roles.mic.device]            # the kind's parameters
card = "Flex XVF3800 L16K6Ch"
rate_hz = 16000
sample_format = "s16_le"
layout = ["conference_beam", "asr_beam", "raw_mic_0", "raw_mic_1", "raw_mic_2", "raw_mic_3"]

[roles.mic.stated]            # optional, free-form: what a person knows about the device
image = "six-channel USB audio image"
position = "desk, 1 m from the door"
```

- `schema` is 1. Any other value is `unsupported_schema`, and nothing else is
  checked.
- `machines` and `roles` are required tables; either may be empty.
- `machines.<m>`: a machine of any name. Its optional `programs` table says
  where programs that roles call are **on that machine**, each in one of two
  forms. Only program names some kind uses are read there; the capture kind
  uses `pod_capture`, brenn-pod's recorder, and the playback kind
  `pulse_client`, the sink client a role on a sound-server sink runs.
  - A string is an absolute path on that machine where the program is
    installed (`relative_path` otherwise).
  - `{ pinned = true }` is the program this harness's build pins, as carried
    by the harness process that hosts the machine's roles: in this version
    the runner, for the machine `--machine` names (see *The pinned
    recorder*). `pinned = false` is `bad_value`: to use an installed program,
    name its path. A table without `pinned` is `missing_key`, one with
    another key `unknown_key`, and any value of another type `wrong_type`.

  There is no default and neither form falls back to the other: a machine
  that names nothing has no recorder, and a path with no executable is not
  replaced by the pinned recorder, nor the reverse. Whether a program exists
  where its form says is not a configuration problem: the role's `check`
  reports it (`recorder_missing`).
- `roles.<r>`: a role, the name a script uses for one device. A role name
  matches `[a-z][a-z0-9_]*` and is at most 32 bytes: it names a directory in
  the run and is the recorded stream's source name.
  - `kind`: the role's kind, `capture` or `playback`.
  - `machine`: a machine in `machines`. A role whose kind needs a program its
    machine does not name is `missing_program`. A playback role needs
    `pulse_client` on a sink (`output = "pulse"`) and nothing on a card.
  - `device`: the kind's parameters. For `capture`: `card`, a non-empty
    string that selects the card; `rate_hz`, a positive integer; `sample_format`,
    a string; `layout`, a non-empty list of distinct non-empty channel names.
    For `playback`: see *The playback kind*, *Configuration*.
  - `stated`: optional and free-form. It is copied into the manifest as the
    configuration's statement, never as a reading. A date, a time or a
    non-finite number in it is `wrong_type`: it has no JSON form.
  - `sample_clock`: optional, a role of any kind. A name of the same form as
    a role name (`bad_value` otherwise). **Two roles that state the same name
    are stated to sample on one clock.** It is a person's statement about the
    hardware: nothing infers it and nothing checks it, and a role that states
    none shares a clock with nothing. Two streams on one card usually share a
    clock and need not; two cards on one word clock share one that no
    enumeration shows. The manifest records it as `roles.<r>.sample_clock`.
    A playback role on a sound-server sink states none: the server resamples
    and remixes its frames on the way to the device, so no frame it keeps is
    a sample on any device's clock, and a stated one is `bad_value`.
- **The configuration does not repeat the recorder's limits.** A rate, a
  format or a channel name the recorder's command line refuses is refused by
  the recorder, which exits before it opens anything. The capture role reports
  that as `recorder_usage` with the recorder's own words, so a mistyped
  `sample_format` shows up in the dry run's `check`. What the device refuses
  of an accepted command line is the recorder's `params`.

The loader reports every problem it finds, not the first, and a configuration
with any problem is not used. Each problem names the dotted `key` at fault
(the empty string for the file as a whole) and one of these reasons:
`not_toml`, `unsupported_schema`, `missing_key`, `wrong_type`, `unknown_key`,
`bad_value`, `unknown_kind`, `unknown_machine`, `relative_path`, `bad_name`,
`missing_program`. A machine or role with any problem is left out of what the
rest is checked against, so one fault is not reported twice.

**Which machine the runner is on** is not in the configuration. The caller
says so with `--machine <name>`, a name in `machines`. The runner hosts the
roles on that machine. This version has no transport to any other machine:
every command to a role on another machine is answered by the runner with the
failure `unreachable`, and no role object stands behind it.

## Writing a script

A script is one Python file, kept anywhere; the runner is given its path. It
defines two names:

```python
from brenn_lab import capture
from brenn_lab.script import Lab

ROLES = {"mic": capture.KIND}


def run(lab: Lab) -> None:
    mic = lab.role("mic")
    mic.send(capture.Start())
    lab.wait(10.0)
    mic.send(capture.Stop())
```

- `ROLES` maps each role the script uses to the kind it expects. The dry run
  checks every role in it, even those the script's own path never reaches,
  and the manifest records it.
- `lab.role(name)` returns the role's handle. A name not in `ROLES` raises
  `UndeclaredRole`.
- `handle.send(command)` sends one command and returns its typed result. A
  failure reply raises `RoleFailure`, which carries the role, the command and
  the failure (`.failure.reason`, `.failure.detail`, `.failure.data`). A
  script may catch it; the run still does not complete. An error a role
  raises instead of replying reaches the script as that exception, not as a
  `RoleFailure`, and the run does not complete whether or not the script
  catches it.
- `handle.send` refuses `release`, which only the runner sends, by raising
  `RunnerCommand` (a `TypeError`) before anything is sent. Nothing is
  journaled. Uncaught, it is a script error like any other. A script may send
  `check`.
- `lab.wait(seconds)` sleeps on the runner's monotonic clock; in a dry run it
  returns at once. A script that calls `time.sleep` itself makes the dry run
  as slow as the run: use `lab.wait`.

That is the whole API. A script cannot read events: they go to the journal
and the results. A script runs in the runner's interpreter with the runner's
dependencies, `brenn_lab` and the standard library; a script that needs a
third-party package needs it added to `requirements.in`.

### Placing a play in time

A play may state when it starts: `playback.Play(..., start=...)`, where the
start is a `ClockReading` of the playing role's own machine. A script never
reads its own clock to make one. It asks the role for a reading with
`playback.Clock()`, whose result `.at` is the role's clock as it replies, and
moves it later with `ClockReading.after(seconds)`:

```python
LEAD_S = 2.0

talker_now = talker.send(playback.Clock()).at
speaker_now = speaker.send(playback.Clock()).at
talker.send(playback.Play(source="talker.wav", volume_mb=talker_mb, start=talker_now.after(LEAD_S)))
speaker.send(
    playback.Play(
        source="own.wav", volume_mb=speaker_mb, start=speaker_now.after(LEAD_S + OFFSET_S)
    )
)
lab.wait(LEAD_S + max(talker_s, OFFSET_S + own_s) + 1.0)
```

- The role does every slow step of the play (judging the source, the kept
  copy, attaching and opening the output, setting and reading back the
  controls) before it replies, and replies as soon as the play is ready. At
  the start it hands the first frames over and does nothing else first. The
  reply does not wait for the start, so a second role's play can be readied
  while the first waits.
- `LEAD_S` is room for both plays to be readied before either start. A start
  that has passed by the time the output is ready fails the play
  (`start_passed`); it is never played late.
- **A role refuses a start that is not on its own clock**: a start naming
  another machine, or another clock, fails `start_clock` before anything is
  read, attached or opened, live and in a dry run. So the idiom above states
  each start on its own role's reading and never hands one role's reading to
  another.
- **On one machine** the two readings are asked one after the other and
  treated as one moment. The gap between the two plays' first marks is the
  stated offset plus the time between the two `clock` replies, plus the
  difference of the two plays' `started_after_us`. On the bench that was a
  tenth of a millisecond plus under one millisecond (*The anchoring scenario*).
- **On two machines** the same script runs unchanged. The harness claims each
  play's start against its own machine's clock, at its first mark; it claims
  nothing about the gap between the two machines' plays beyond what the
  script could know: both readings were taken between its sending the first
  `clock` and receiving the second's reply, and the journal's four lines for
  those commands record that interval. Alignment measures the gap that
  sounded after the fact; nothing compares the two clocks.
- The harness claims a start at the play's first mark, the mark taken after
  the first write. It corrects for no output's latency: what an output adds
  after the mark (a quarter second on a Bluetooth sink) is in the gap in the
  room, and alignment measures it.

`examples/` holds scripts that are tracked and type-checked. Scripts for one
experiment, with a room's particulars in them, need not be in the repository
at all.

## The seam

A script's only way to a device is a **command**, a plain value. What comes
back is a **reply** to that command, or an **event** that no command asked for
at that moment. Each crosses the seam as JSON, which is how the journal
records it:

```json
{"role": "mic", "command": "capture.start", "dry": false, "args": {}}
{"role": "mic", "command": "capture.start", "dry": false, "ok": true, "result_type": "capture.started", "result": {…}}
{"role": "mic", "command": "capture.start", "dry": false, "ok": false, "failure": {"reason": "no_card", "detail": "…", "data": {…}}}
{"role": "mic", "event": "capture.gap", "data": {…}}
```

- A reply answers exactly one command and is a result or a **failure**: a
  `reason` from a closed vocabulary, a prose `detail`, and `data` for what the
  reason carries.
- A successful reply names its result's type in `result_type`; a failure has
  none. `check` and `release` are common to every kind and each kind answers
  them with its own type, so the command's name does not say what the result
  is. Decoding is strict: a successful reply without `result_type`, or a
  failure with one, is rejected.
- **`dry`** is part of the command. A role handling a dry command does
  everything it can to establish whether the real command would succeed,
  without recording, playing, moving or leaving anything in a run, and
  replies as the real command would.
- Two commands are common to every kind:
  - `check`: is the device there, and does it grant what the configuration
    asks. It never plays or moves, and it is the same dry or not.
  - `release`: the session is over; stop whatever is still running and say
    what that was. Only the runner sends it. Each kind answers with its own
    result, and the kind says from that result what was still running
    (`capture 2`, `play 1`): the run's and the dry run's `left_running`.
- **An event that reports a failure carries it in a field named `failure`**,
  of the failure's form. A kind cannot register an event whose `failure`
  field is anything else. That is where the runner, and anything else that
  reads a role's events, finds a failure in an event of any kind.

The registered names:

| | Names |
|---|---|
| Commands | `check`, `release`, `capture.start`, `capture.stop`, `playback.play`, `playback.stop`, `playback.clock` |
| Results | `capture.started`, `capture.stopped`, `capture.checked`, `capture.released`, `playback.started`, `playback.stopped`, `playback.checked`, `playback.released`, `playback.clock_read` |
| Events | `capture.gap`, `capture.late`, `capture.failed`, `playback.ended`, `playback.underrun`, `playback.failed` |

## The capture kind

A capture role records every channel of one device to a frame log with
brenn-pod's recorder, `pod-capture`: the pinned one or the one installed at a
path, as the machine's `programs.pod_capture` says, and never the other.

### The pinned recorder

`{ pinned = true }` names the `pod-capture` this repository's build pins and
builds with the runner, `//brenn_lab:lab`. It runs on the runner's machine
and loads that machine's ALSA runtime library (`libasound.so.2`); the build
carries none. On a host without it the loader ends the recorder with exit
status 127 before it prints a line, and the role reports `recorder_exited`
with `data.exit_status` 127, from `check` as from `start`. The failure does
not carry the loader's message: a dry run keeps the recorder's stderr nowhere,
and a run keeps it in `logs/<role>/recorder-<NNNN>.stderr.log`. Its build
reports the commit the build pins, with
`source` `declared` and `dirty` `null` (see `run-directory.md`, *What `dirty`
means*).

### Commands and results

`capture.start` and `capture.stop` take no arguments. What to record and how
is the configuration's.

- `capture.start` → `capture.started`: `capture` (the role's capture number in
  the run, from 1; 0 in a dry reply), `capture_id`, `location`
  (`{machine, path}`, the path the role gave the recorder; `null` in a dry
  reply), `recorder` (`{program, repository, build}`), `device`, `granted`,
  `layout`, `start`, `device_state`. These are the manifest's capture entry
  (see `run-directory.md`).
- `capture.stop` → `capture.stopped`: `capture`, `frames`, `gaps_reported`,
  `end`.
- `check` → `capture.checked`: `recorder`, `device`, `granted`, `layout`,
  `device_state`.
- `release` → `capture.released`: `stopped`, a `capture.stopped` or `null`.

`build.dirty`, `device.usb_id` and `device.usb` may be `null`. `device.usb`
and `device_state` are the recorder's own statement of where the device is on
USB and of the device's state, carried as given; their meaning is brenn-pod's
(`docs/reference/frame-log.md`). `device_state.outcome` is `read`,
`unavailable` (the recorder tried and could not reach the state, for example
an account that may not open the device's usbfs node) or `not_attempted` (a
device the recorder does not read). The recorder writes nothing to the device;
for an XVF3800 it reads the chip's firmware identity and routing registers
before the capture starts. A check is a real start of the recorder, so
`capture.checked` says what that start read: a dry run shows a board whose
chip cannot be read before anything is recorded.

### Events

- `capture.gap`: `{capture, cause, at_frame, frames, at, detail}`. Audio is
  missing at `at_frame`; `frames` is `null` for a loss of unknown length.
- `capture.late`: `{capture, at_frame, backlog_frames, at}`. The recorder fell
  a period or more behind; nothing was lost.
- `capture.failed`: `{capture, failure, stopped}`. The capture is over and
  nobody asked it to stop. `stopped` is a `capture.stopped`, with the
  recorder's frame and gap counts, when the recorder stopped cleanly with
  nobody asking (`stopped_unasked`), and `null` otherwise.

### How the role behaves

- `capture.start`: starts the recorder into the next
  `captures/<role>/<NNNN>.framelog`. One capture runs at a time:
  `already_capturing` while a recorder is running.
- `capture.stop`: stops the running capture and replies `capture.stopped`.
  With no capture, `not_capturing`. A capture that already ended unasked is
  answered with the failure it ended with, as long as no new start came
  between.
- `check` runs a **probe**: a start into a scratch file in a fresh temporary
  directory, a stop as soon as it has started, and the directory deleted. It
  replies from what the start reported. A probe opens the device, so it is
  refused with `already_capturing` while a capture is running (or, in a dry
  run, while the dry state is capturing). The device's open, the parameter
  grant and the destination are exercised exactly as in a run.
- A dry `capture.start` or `capture.stop` checks the order of commands against
  a dry state of the role's own and answers with the probe's `capture.started`
  and `capture.stopped`, running the probe first if none has run. Where the
  probe failed, both are answered with the probe's failure.
- `release` stops a running capture and replies with its `capture.stopped`,
  and drops the dry state. A dry `release` reports the dry state only.
- A capture that ended unasked does not block the next start or probe. A new
  start replaces it, and its failure then stays the `capture.failed` event
  alone.

The recorder's channel names are passed on its command line joined with
commas, so a channel name containing a comma cannot be passed. The role
refuses it with `recorder_usage` before starting anything, from `check` and
from `capture.start`.

### Failure reasons

From the recorder, each keeping the recorder's word and meaning. brenn-pod's
`docs/reference/pod-capture.md` is the authority on them; in short:

| Reason | Meaning |
|---|---|
| `destination_exists` | the capture's file already exists |
| `no_card` | no card matches `card`; `data.cards` lists the cards there are |
| `ambiguous_card` | more than one card matches; `data.cards` lists them |
| `open` | the device could not be opened, for one because something else holds it |
| `params` | the device refused what the command line asked of it |
| `destination` | the capture's file could not be made |
| `stream_died` | the stream failed after the capture started |
| `destination_full` | free space ran under the recorder's margin; the log ends there |
| `write_failed` | writing the frame log failed; `detail` says whether its end landed |
| `write_stalled` | the log's writer had not reported within 5 s of the stream stopping; whether the log ends is not established (`data.log_complete` is `not_established`), and brenn-pod's `frame-log` tool is what can say |

The role's own:

| Reason | Meaning |
|---|---|
| `recorder_missing` | no executable at the recorder's path, the configured one or the pinned one's; `data.program` is the path tried |
| `recorder_usage` | the device parameters cannot be put to the recorder: a channel name with a comma, or the recorder refused its command line (exit status 2); `data.stderr` is the recorder's stderr whole and `detail` its first line |
| `recorder_exited` | the recorder ended without a final line |
| `recorder_contract` | the recorder printed something its contract does not allow, its exit status disagrees with its last line, or the layout or channel count it reported is not what was sent |
| `recorder_timeout` | no first line within 10 s of the start, or no final line within 15 s of the stop; the recorder is killed. Inside the first 10 s the recorder opens the card and, for an XVF3800, first reads the chip's state (152 ms on the maintainer's Flex). A chip that answers "retry" throughout can hold that read past the 10 s, and the start then fails here; bounding the read is brenn-pod's to add |
| `stopped_unasked` | the recorder stopped cleanly when the role had not asked |
| `already_capturing` | a capture is running |
| `not_capturing` | no capture is running |
| `unknown_command` | the command is not one the capture kind has |
| `role_io` | the role could not write a file of its own: its directories in the run, its copies of the recorder's output, the pid note, the probe's scratch directory. Not `destination`, which is the recorder's own file |

From the runner, with no role behind it: `not_configured` (the role is not in
the configuration), `kind_mismatch` (the configuration binds it to another
kind) and `unreachable` (it is on a machine this build has no transport to).

What each reason carries in `data` is tabled in `run-directory.md`, *Failure
data*.

A `role_io` failure while copying the recorder's output ends the capture.
It reaches the run by whatever route any end of the capture takes at that
moment: the reply to `capture.start` if the recorder had not yet reported its
start, the stop's reply if a stop was asked, the `capture.failed` event
otherwise.

A recorder newer than the harness that prints an event the harness does not
know ends the capture with `recorder_contract`.

## The playback kind

A playback role plays WAV files through one output device, keeps an exact
copy of what it handed the device, and holds that device under a volume cap
and quiet hours that only its configuration sets. It has two output kinds:
`alsa`, an ALSA card opened directly, written by the runner's own
interpreter; and `pulse`, a sink of the desktop's sound server, reached
through the pinned sink client (*On a sound-server sink*). The role applies
one rule to either. What follows is said of a card where the two differ, and
*On a sound-server sink* says what differs on a sink. Every level is an
integer number of **millibels** (mB), hundredths of a decibel, the unit ALSA
reports volumes in.

### Configuration

```toml
[roles.speaker]
kind = "playback"
machine = "bench"
sample_clock = "flex"

[roles.speaker.device]
output = "alsa"
card = "Flex XVF3800 L16K6Ch"
source_dir = "/data/lab/sources"
cap_mb = -2400
quiet_hours = [["22:00", "07:00"]]

[roles.speaker.device.level]     # the volume control a play's `volume_mb` sets
element = "PCM"
index = 1

[[roles.speaker.device.held]]    # every other playback control of the card
element = "PCM"
index = 0
volume_mb = 0
```

- `output`: `"alsa"` or `"pulse"`; any other value is `bad_value`. The keys
  below are a card's; a sink's are under *On a sound-server sink*. A key of
  the other kind is `unknown_key`.
- `card`: non-empty. Matched case-insensitively as a substring of each card's
  name or long name; exactly one card must match. That is the rule the
  capture kind's recorder applies. The device is `hw:<index>,0`.
- `source_dir`: an absolute path on the role's machine. A play names its
  source relative to it, so a script names no machine's file system.
- `cap_mb`: the cap (see *The cap*). Required: a playback role without one is
  `missing_key`.
- `quiet_hours`: a list of `["HH:MM", "HH:MM"]` spans of local civil time on
  the role's machine. A span whose end is not after its start wraps midnight;
  a start equal to its end is `bad_value`. Required: `[]` states that there
  are none.
- `level`: the simple mixer element, `element` and `index`, whose volume a
  play sets. Required.
- `held`: every other simple element of the card that has a playback volume
  or a playback switch, each with the `volume_mb` the role holds it at
  (omitted for an element with a switch and no volume). May be empty or
  absent. A control named twice across `level` and `held` is `bad_value`.
- A control is stated with one volume whatever its channel count. The role
  applies it to every playback channel the element has.
- **The cap and quiet hours have no other source.** No command, script,
  environment variable or command-line flag supplies, raises or suspends
  them.

### Commands and results

- `playback.play {source, volume_mb, start}` → `playback.started`. `source`
  is a path relative to `source_dir`; `volume_mb` is the level control's
  setting for this play. `start` is `null` (the default) or a clock reading
  of the role's own machine and clock: when the role's monotonic clock reaches
  `start.monotonic.us`, the first frames are handed over. Only the monotonic
  value places the play; the wall value is carried for the record. The reply
  to an untimed play comes once the first frames have been handed to the
  device; the reply to a timed play comes once the play is ready, before its
  start (*How the role plays*). Either way the play goes on on a thread of
  the role's, and the script is not blocked for the length of the source.
  - `play`: the role's play number in the run, from 1; 0 in a dry reply.
  - `source`: `{name, sha256, frames, rate_hz, channels, sample_format,
    peak_mb, marker}`: the file as named, the hash of its bytes, what it
    holds, and its largest sample magnitude relative to full scale, rounded
    up; `peak_mb` is `null` for a source of silence. `marker` is the
    harness's marker the file's samples carry, as `run-directory.md`, *A
    source entry*, describes it, or `null`; a dry reply carries it too.
  - `copy`: the `Location` of the kept copy; `null` in a dry reply.
  - `output`: `{kind, library: {name, version}}`: `"alsa"`, and the host's
    ALSA library with the version it reports; or `"pulse"`, and the pinned
    sink client.
  - `device`, `granted`: the card, and what the open stream granted, in the
    capture kind's shapes. On a sink `device` is the sink
    (`run-directory.md`, *A source entry*).
  - `controls`: every control the role holds, read back after setting:
    `{element, index, part, channels}`, `part` `level` or `held`, and one
    `{channel, volume_mb, switch}` per playback channel, `channel` being
    ALSA's name for it, `volume_mb` or `switch` `null` where the element has
    none. `found`: the same, as the controls stood before the role moved
    them. In a dry reply both are the controls as found.
  - `level_mb`: the level of a full-scale sample (see *The cap*), from the
    values read back; in a dry reply, from the asked `volume_mb` and the held
    controls' configured values. `cap_mb`: the cap.
  - `local_time`: the machine's local civil time, ISO 8601 with its UTC
    offset, at which quiet hours were last judged before the open.
  - `first`: the mark after the first write; `null` in a dry reply, and
    `null` for a timed play, of which no frame has been handed when the reply
    is made. A timed play's first mark is in its end.
  - `asked_start`: the command's `start`, echoed; `null` for an untimed play.
- `playback.stop` → `playback.stopped`: cuts the running play. Frames still in
  the device's buffer are discarded, the stream is closed and the controls are
  restored. `play`, `frames_handed`, `unplayed_frames` (the device's delay
  read just before the cut, `null` when it could not be read), `underruns` (a
  count), `last` (the mark after the last write; `null` in a dry reply and for
  a play that handed no frame), `end` (a clock reading once the stream is
  closed), `restored` (whether every control was put back as found), `woke`
  and `first` (below). `not_playing` when nothing is playing, a play that
  already ended included. A timed play stopped before its start is cut with
  `frames_handed` 0.
- `playback.clock` → `playback.clock_read`: `at`, the role's clock reading,
  taken as the last thing before the reply. It touches no output, is
  answered while a play runs, and is the same live and dry. It is how a
  script gets a reading to state a start from (*Placing a play in time*).
- `check` → `playback.checked`: `output`, `device`, `controls` (each with its
  `range`, `{min_mb, max_mb}` or `null`, and its channels as found), `cap_mb`,
  `quiet_hours`, `local_time`, `quiet_now`. It resolves the card and reads the
  mixer. It sets nothing and opens no stream. It is refused with
  `already_playing` while a play runs. A script sends it as
  `brenn_lab.data.Check()`, the command every kind answers, so `send` returns
  `object`; the script narrows the reply to `brenn_lab.playback.Checked`
  before reading it:

  ```python
  checked = speaker.send(Check())
  assert isinstance(checked, playback.Checked)
  level = checked.cap_mb
  ```

  That is `examples/level_step.py`, which plays at its role's cap.
- `release` → `playback.released`: `stopped`, a `playback.stopped` when a play
  was running, else `null`. A play cut by `release` is `left_running`.

A **mark** is `{frames_handed, delay_frames, at}`: the frames handed so far,
the output's delay read then (`null` if the read failed), and the role's clock
reading taken directly after. By the output's own account, frame
`frames_handed − delay_frames` of the copy is at its output at `at`: on a card
by the device's account, on a sink by the sound server's estimate.

There is no command that waits for a play to end. A script passes the time
with `lab.wait`, which a signal interrupts at once:

```python
played = speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))
lab.wait(played.source.frames / played.source.rate_hz + 1.0)
```

That is `examples/play_and_record.py`, which records on another role while it
plays. A script that waits too short a time has its play cut at `release`,
and the run fails with `left_running`.

### Events

- `playback.ended`: the source was handed over whole and the device played
  its buffer out. The fields of `playback.stopped`, `unplayed_frames` 0.
- `playback.underrun`: `{play, at_frame, at, detail}`. The output reported
  that it had run dry, when `at_frame` frames of the copy had been handed;
  the stream was recovered and the play goes on. On a card, sound was
  interrupted there for an unknown length and every frame handed after it
  plays, late. On a sink, frames handed after the server ran out may be
  skipped and never sound, and the report can trail the server's running
  out by one write.
- `playback.failed`: `{play, failure, …the fields of playback.stopped…}`. The
  play ended and nobody asked: the stream died, a held control moved, quiet
  hours began, the recoveries ran out, a timed play woke too late, or the
  role's own code failed. `last` is `null` when the play ended before any
  frame was handed.

A play's end (`playback.ended`, `playback.failed`, `playback.stopped`)
carries two fields of a timed play's start, both `null` for an untimed play:

- `woke`: the role's clock reading when the play's wait ended at its start,
  before the first write; `null` too for a timed play that ended during its
  wait. With it a run tells a role that woke late from a device that took the
  write late.
- `first`: the mark after the first write; `null` for a play that handed no
  frame. A play's first mark is stated once: in its reply when untimed, in
  its end when timed.

Nothing is reported at a timed play's start.

### How the role plays

For `playback.play`, in order. Any refusal is a failure reply, and nothing has
been played:

1. **Source.** Resolve `source` under `source_dir` and read it as a WAV of
   16- or 32-bit integer PCM (`s16_le`, `s32_le`). One pass gives the hash,
   the frame count and the peak.
2. **Limits.** Resolve the card and read its mixer: it must have every
   control the configuration names and no playback volume or switch it does
   not name. `volume_mb` must lie in the level control's range. The level is
   computed from `volume_mb` and the held controls' configured values and
   held against the cap (`over_cap`). Quiet hours are judged over the whole
   length of the source. Nothing has been written, set or opened.
3. **Kept copy.** `sources/<role>/<NNNN>.wav` in the run: a WAV holding
   exactly the frames the role will hand the device, written and synced
   before anything plays. Writing it hashes the source again; a source that
   is no longer the file step 1 judged is `source_changed`. The role plays
   **from the copy** and transforms nothing, so the copy is what was sent.
   Writing and syncing the copy takes time, so quiet hours are then judged
   again; a play refused then leaves no copy and uses no number.
4. **Open.** Open the stream and demand the source's rate, channel count and
   format exactly, with resampling off. A period of 20 ms and a buffer of
   500 ms are asked for at the nearest the device has, and recorded as
   granted. A busy device is `open`; nothing is freed.
5. **Controls.** Read every channel of every control (this is `found`), then
   move them to the play's values: the level control to `volume_mb` and each
   held control to its configured value on every playback channel, rounded
   down to the control's step, every switch on (see *Moving the controls*).
   Every channel is read back: one that reads higher than asked, or a switch
   that reads off, is `controls`. One that reads lower is the control's step,
   and is accepted and recorded.
6. **Write.** Hand the first chunk over, take the first mark, reply. The
   thread then hands the copy over in chunks of 100 ms. Before each chunk it
   reads every channel of every control it holds and judges quiet hours.
   After the last it lets the output play its buffer out, closes the stream,
   restores the controls and reports `playback.ended`. That wait is bounded:
   the first delay above 0 it reads sets a deadline, that delay at the play's
   rate plus 2 s, never moved. A delay still above 0 at the deadline ends the
   play with `stream_died`, its `data` the last delay read, the buffer
   dropped and the controls restored. A stop, a moved control or quiet hours
   are judged on every turn of the wait, as between chunks.

- An **underrun** is recovered and reported as `playback.underrun`. On a card
  it is a gap, then every frame handed after it, late: the kept copy is what
  sounded, with a gap. On a sink it is a gap, and frames handed after the
  server ran out may be skipped and never sound: the kept copy is then longer
  than what sounded (*On a sound-server sink*). Eight
  consecutive recoveries without a successful write end the play with
  `stream_died`. Any other write error ends the play with `stream_died` and
  is not recovered: a recovery from a suspended stream can discard the
  device's buffer, and the play's record would not show it.
- **A stop, a release, a moved control or the start of quiet hours** is
  noticed between chunks, and the device's buffer is discarded, not played
  out. The sound ends **within about a quarter of a second** of the cause.
  That bound is a property of the write loop, held by the default check
  against an in-process output; it has been measured on no device.
- One play runs at a time per role: `already_playing` while one does. Two
  playback roles on two devices play at once. Two on one card: the second
  play fails with `open` while the first runs.
- The role keeps no stream open between plays.

#### A timed play

A play with a `start` runs these steps instead:

1. **Own clock.** Before anything else, the start must name the role's
   machine and the clock of the role's own readings; otherwise
   `start_clock`, with nothing read, attached, written or opened. Live and
   dry alike.
2. **Source and limits**, as above, but quiet hours are judged over the hold
   and the sound: from now to the start plus the source's length.
3. **Kept copy**, as above; quiet hours judged again over the same span.
4. **Open**, as above. On a sink the stream is created corked.
5. **Controls**, as above: read, moved, read back.
6. **Passed?** The role reads its clock. At or past the start, the play
   fails `start_passed` with `data` `{late_us}`: no frame, stream closed,
   controls restored, output let go, no event; the copy is left and its
   number used, as for a play refused at the controls.
7. **Reply** `playback.started` with `first` `null` and `asked_start`; the
   play's thread starts and the play is the role's running play.
8. **Wait.** While more than 100 ms remain, the thread makes the
   between-chunks check (a stop, a moved control, quiet hours) and waits
   100 ms on the play's stop. Inside the last 100 ms it makes no check and
   waits for what remains of a fresh reading of the role's clock, again
   while that clock still reads before the start. No frame is handed while
   the clock reads before the start. So the controls are verified no longer
   before the first frame than between two chunks of a running play, and no
   round trip to a mixer or a sound server stands between the wake and the
   write.
9. **At the start.** The thread reads the clock (`woke`). More than 20 ms
   past the start, the play fails `start_passed` in a `playback.failed`
   event with no frame handed: a play in the wrong place is worse than a
   failed run, and 20 ms is one period of the stream. Otherwise the first
   chunk is handed over, and the play goes on as any play from step 6 above.
   `woke` and the first mark are kept for the play's end.

**What a readied play holds**, from its reply to its end: its output
attached, its stream open with no frame written (on a sink, corked), the
controls at the play's level, and the role refusing another play or a
`check` (`already_playing`). `stop`, `release` and an interrupt end the wait
at once; the play ends `playback.stopped` with `frames_handed` 0 and `last`,
`first` and `woke` `null`, its controls restored. A start far ahead holds the
output that long; the runner's release at the script's end stops it.

**Nothing is reported at a timed play's start.** From its wake to its first
underrun or its end, the play's thread reports nothing and takes no lock the
script's thread can hold. The runner records a reply under a lock held across
the manifest's rewrite and `fsync`, and an event takes the same lock; a
thread that reported its first mark there could wait past its first 100 ms
chunk while the script readied another role's play, and underrun its own
head. The first mark and `woke` are carried by the play's end.

### The cap

**The level of a play** is the level control's volume plus the sum of the
held controls' volumes: the level at which a full-scale sample is played,
relative to the loudest the device's output can be driven. **The cap is the
highest level the role will set the device's controls to.** A play whose
level exceeds `cap_mb` is refused with `over_cap`, before anything is set,
written or opened.

- The source does not enter. A low peak, or silence, earns no volume on the
  hardware. The loudest the play itself reaches is `level_mb +
  source.peak_mb`, which a reader computes from the source entry.
- **Every channel, and the loudest counts.** The role sets and reads every
  playback channel of every control it holds. A control's volume in the
  level is the highest of its channels', and a switch is on only when every
  channel's is.
- The cap is in units the role controls and no others. What sound pressure a
  level makes depends on the amplifier and speaker behind the output; the
  maintainer sets each device's cap by ear.
- The role scales no samples. A script chooses how loud by `volume_mb` and by
  the source; the role refuses a `volume_mb` that puts the controls above the
  cap and never lowers anything to fit.
- **The controls are the role's while it plays.** Any channel of any control
  found different from what the role set, higher or lower, or any switch
  found off, ends the play with `controls_moved`. Another program, a desktop
  sound server for one, can therefore raise the device's volume for up to
  about a quarter of a second before the play is cut.
- A held control stated below its maximum is relied on for the cap exactly
  as the level control is.

#### Moving the controls

Whenever the role moves the controls, to what a play asks or back to what was
found, **it lowers before it raises**:

1. **Read** every channel of every control it holds. If the read fails, the
   move writes nothing and has failed.
2. **Lower.** Every channel whose volume is above its target is set down to
   it, and every switch that is on with a target of off is turned off. Each
   lowering is attempted, a failed one not stopping the rest.
3. **Raise**, only if the read and every lowering succeeded: every channel to
   its target, every switch to its target. Each write is attempted.

That is said of a write the device refuses; an error raised in the move ends
it.

A switch going off is a lowering and one going on is a raise. Of the role's
own writes, on a mixer that takes each write as written, rounded down, or
refuses it and changes nothing: **after every write the role makes,
successful or refused, from its first of a play to its last, either the level
is at or under the cap or no channel is above where the role found it.**

- The restoration puts back what was found, a setting above the cap
  included: the device's state before the role touched it is its owner's.
- **`restored` false** means the restoration failed (the device went away,
  its first read failed, a mixer write was refused, or an error was raised
  in the restoration) and was not tried again. If the read failed, nothing
  was written, and the controls are where the play set them, at or under the
  cap. If a lowering was refused, nothing was raised: every channel is at or
  under what the play set. If a raise was refused, no channel is above where
  it was found. The device may then be quieter than it was found on some
  channel, and after a refused lowering a channel may stand above where it
  was found, at what the play set. That is a change to the machine's sound
  state the role could not undo. A raise, unlike a refusal, stops the
  restoration where it is raised: the writes after it were not made, and the
  controls are as the last write made left them, at or under the cap or no
  louder on any channel than the role found it.
- **An error raised while a play is ended** (its stream closed, its controls
  put back, its output let go) does not stop the rest of the end: every
  step is still made, and the play ends with a `playback.failed` event. It
  keeps the reason it ended for, with every exception raised in its detail,
  or is `role_error` when it had drained or been asked to stop; a stop
  waiting on it answers `not_playing`. Its `unplayed_frames` is `null` where
  the delay could not be read.
- **The runner killed outright mid-play**: the stream closes with the
  process and the sound stops. The controls stay where the role put them,
  their level at or under the cap whatever the source, and possibly lower
  than found. A kill that falls while the role is moving the controls leaves
  the device as its last write left it: at or under the cap, or no louder on
  any channel than the role found it. The kept copy stays; there is no
  `results.json`.

### Quiet hours

Judged on the local civil time of the role's machine, as its clock and its
zone setting (`/etc/localtime`, UTC when absent) give it. A play is refused
with `quiet_hours` if any instant from now to now plus the source's length
falls in a quiet span, so a source that would run into quiet hours never
starts. That is judged with the limits and again just before the open, once
the kept copy is written. A timed play is judged from now to its start plus
the source's length, so a play whose hold or sound touches a quiet span is
refused before anything is written, and the wait before its start judges
again every 100 ms until the last 100 ms. Before each chunk the thread judges again, against a stalled or
stepped clock, and ends the play with `quiet_hours` if the time has come. The
reply and `check` carry the local time the role judged by, so a machine with a
wrong zone shows in the dry run's report; the role cannot know its zone is
wrong.

### The dry run of a playback role

`check` resolves the card and reads the mixer, and opens nothing. A dry
`playback.play` does steps 1, 2 and 4 of *How the role plays* and ends at the
first refusal: it **opens the output without writing a frame**, reads the
grant and closes it. It writes no copy and sets no control. Quiet hours are
judged at the moment of the dry run, with the limits and again just before
the open, and inside them a dry play is refused before the open, so a dry run made then does not show the grant. The role
opens its output at no time inside the device's quiet hours, dry or not.

Nobody has yet listened to that open on a real output, so **a dry run of a
playback role on a real output is made only inside an agreed window**, as
`AGENTS.md` says (`output-open-not-listened-to`). The Flex's own microphones
have recorded it: in two runs of the audible bench, the 500–3500 Hz level of
each raw microphone, per half second, did not rise in the half seconds the
dry run spanned (−88 to −92 dB, as around them). In both, about one to two
seconds after the dry run had ended, one half second rose 8 to 13 dB on all
four microphones; what made it is not known.

A dry timed play makes the own-clock step first (`start_clock` on another
machine's or another clock's start), then what any dry play makes, with
quiet hours judged from now to its start plus the source's length. It
judges nothing about whether the start has passed, waits for nothing,
replies with `asked_start`, and holds nothing open after its reply.

A dry `playback.stop` checks only that a dry play preceded it. A dry role
reports no event.

### Failure reasons

| Reason | Meaning |
|---|---|
| `source_outside` | `source` is empty, absolute, not a file name, or resolves, links included, outside `source_dir` |
| `source_missing` | `source` is not a readable file |
| `source_format` | not a WAV of 16- or 32-bit integer PCM, or its data is not whole frames or not what its header declares |
| `source_changed` | the source changed between the role's two reads of it; nothing played |
| `alsa_missing` | the host's `libasound.so.2` could not be loaded |
| `no_card`, `ambiguous_card` | no card, or more than one, matches `card` |
| `no_control` | the card lacks a control the configuration names |
| `unheld_control` | the card has a playback volume or switch the configuration does not name; the cap cannot be held over it |
| `volume_range` | `volume_mb` is outside the level control's range, the level control has no volume, or a held control's stated volume does not fit it (out of range, stated for a control with no volume, or missing for one with a volume) |
| `over_cap` | the play's level is above the cap |
| `quiet_hours` | the play would fall in a quiet span, or one began during it |
| `start_clock` | the play's `start` names another machine than the role's, or another clock than the role's own readings'; `data` `{machine, clock, role_machine}`, the start's machine and clock and the role's machine |
| `start_passed` | a timed play's start had passed when its output was ready (a failure reply), or its thread woke more than 20 ms after it (a `playback.failed` event); no frame was handed. `data` `{late_us}`, how far past the start the role's clock read |
| `open` | the device could not be opened, for one because something else holds it |
| `params` | the device did not grant the source's rate, channel count and format exactly |
| `mixer` | a mixer call failed: the mixer could not be opened or loaded, a read or write was refused, or a volume has no dB scale (such a control cannot be held to a cap in millibels) |
| `controls` | a control read back above what was set, or with a switch off |
| `controls_moved` | during a play a held control changed, or could not be read |
| `stream_died` | the stream failed beyond recovery: a write failed with anything but an underrun, a recovery failed, eight ran without a successful write, a write took no frames, or the buffer could not be played out: the drain failed, or the delay was still above 0 at the end-of-play deadline (*How the role plays*, step 6). On a sink, also: the server killed, moved or suspended the stream during a write, or the client failed during a write or a drain |
| `no_server` | on a sink: `XDG_RUNTIME_DIR` is unset, or no sound server answered at `$XDG_RUNTIME_DIR/pulse/native`, within 4 s for the whole start |
| `client_missing` | on a sink: the configured `pulse_client` could not be run |
| `client` | on a sink: the client died, did not answer within its bound (5 s; a write and a drain, the buffer's length plus 7 s), answered a line it could not have meant, or refused a request for a reason with no name of its own, such as a server that did not answer it within 4 s; the detail carries its last lines of stderr |
| `no_sink`, `ambiguous_sink` | on a sink: no sink, or more than one, matches `sink`; or the sink vanished |
| `sink_volume` | on a sink: the sink has flat volume, under which the server raises it to its loudest stream, or no decibel volume; refused before any stream exists |
| `stream_moved` | on a sink: the play's stream is not on the named sink, not at 0 dB on every channel, or muted, or the client could not read it. During a play the role reports it as `controls_moved` |
| `role_io` | the role could not write or read its kept copy, or another file of its own |
| `role_error` | the play's thread raised, while it handed frames over or while it ended the play; the play was ended, the restoration attempted (`restored` says how it went), the output let go, and the exception raised on. Not the runner's `role_raised`, the run's failure when a role raised instead of replying to a command (see the run-directory reference) |
| `already_playing` | a play is running |
| `not_playing` | no play is running, or no dry play preceded a dry stop |
| `unknown_command` | the command is not one the playback kind has |

What each reason carries in `data` is tabled in `run-directory.md`, *Failure
data*.

### What the limits cover

The cap and quiet hours hold for everything sent to the role. A script runs
in the runner's interpreter, the role's own process, so a script that opens a
sound device itself, by `ctypes` or by starting a player, is not stopped by
them (`script-shares-the-playback-roles-process`).

The playback role loads the host's ALSA runtime library, `libasound.so.2`,
the first time it resolves a card, and at no other time. A host without it
gets `alsa_missing` from `check` and from `playback.play`. A role on a
sound-server sink loads no host library: it runs the pinned sink client,
which speaks the server's protocol itself.

### Finding a level

How loud an output must play for the microphones to find a marked play
depends on the device, its placement and the room, so it is found on the
bench, not written into a script. `examples/level_step.py`, roles `mic` and
`speaker`, is one step: it reads the role's cap from `check` and plays at
that level, so the cap is the step's level and nothing in the run is louder.
It starts the capture, waits one second, plays `primer.wav` (any unmarked
sound of a few seconds, which also wakes a speaker that fades in after
silence) and waits its length plus a second, then plays `marked.wav` (any
file from `lab mark`), waits its length plus a second and stops the capture.

By hand, a run is: set the role's `cap_mb` in the lab file to the level,
`lab run` `level_step.py`, and read the `alignment.json` the run's closing
assessment wrote. The play was detected in that run when, in the pair of
`speaker`'s play 2 with the capture, every `raw_mic_` channel has `start` and
`end` both `found`. Near the threshold one level is detected in some runs and
missed in others, so a level is found when three runs in a row at it detect
the play; a run that misses sends the hand up one level. The audible bench,
`//brenn_lab:bench_audible_test`, makes the ramp so for each output it plays
on:

- the levels are the role's minimum (the level control's `range.min_mb` from
  a dry run's `check`), then −12000 mB upward in steps of 600 mB, ending at
  the output's ceiling: −1800 mB on the Flex's output, −2000 mB on a sink;
- it stops at the first level at which three runs in a row detect the play,
  and plays nothing louder: after a level's first detection it takes up to
  two more runs at that level, none louder to confirm, and a run that misses
  ends the level, whatever the runs before it there read; on a sink a
  detection at the minimum, which is silence, is a defect on its first run;
- a run whose plays did not run as asked (a record not `completed` with no
  underrun and `restored`, or a control read back above the level asked)
  stops the ramp as a defect, at any level;
- an undetected run above −4000 mB whose plays ran as asked is read before
  the next: a primer not 10 dB above the room on every raw microphone, or a
  marked play's 500–3500 Hz band 3 dB or more above the room's that still was
  not found, stops the ramp as a defect; otherwise the marker is still under
  the room's noise and the ramp goes on;
- every later play on the output alone is one step above the level found,
  never above the ceiling, under a cap of that level. A play that starts
  under another output's sound takes its level from a ramp made with that
  sound playing (*A level under another play*).

Measured on one bench on 2026-11-14, in a quiet room, the speakers close to
the microphones:

- **The Flex's own output** was detected at its level control's minimum,
  −6000 mB, in every ramp (raw-microphone scores 0.54 to 0.78), so later
  plays were at −5400 mB. In the last lane run, which counted three runs in
  a row, the three runs there read start-marker scores of 0.67 to 0.73, 0.68
  to 0.78 and 0.68 to 0.77. The control has nothing quieter. The primer read
  −70 to −75 dB at 1 kHz on the raw microphones against a room of −115 to
  −134 dB, flat from its first quarter second.
- **An LG-PL5 Bluetooth speaker** as a sink was detected at −8400 mB in every
  ramp, with −9000 mB undetected, so later plays were at −7800 mB; in the
  last lane run its three runs at −8400 mB each read 0.65 to 0.89. At
  −9000 mB and below the primer was at the room's noise; at −8400 mB it read
  about −50 dB. Sixty decibels for a 6 dB step reads as the speaker sounding
  nothing under some sink volume between those two settings, so how far a
  sink's volume moves this speaker's sound was not measurable in the ramp.

#### A level under another play

A level found for an output alone is a margin over the level at which that
output was detected alone in a quiet room, not over another output's sound. On the bench
above, `examples/anchoring_scenario.py` with each output one step above its
alone-level (the Flex's at −5400 mB, the LG-PL5's at −7800 mB) located both
plays in one run of four: the Flex's start marker, which sounds about a
second into the talker's speech, scored 0.15 to 0.20 in the other three, while
its end marker was found every time. The talker masked it.

The bench answers this by raising the overlapping output's level, found by a
second ramp made with the other output playing. Two other answers were not
taken: a different offset between the plays, because the overlap is what the
scenario is for, and a weaker expectation in the test, because finding both
plays in the overlap is what the test asserts. Each ramp counts a play
detected at a level when three runs in a row there detect it. The bench's
ramps took one run per level at first, and a run that scored high by chance
then stopped the ramp under the talker two levels below the level earlier
ramps had found (below).

By hand: set the talker's cap to its own play level and the speaker's cap to
its alone-level; run the example; read `alignment.json`; until three runs in
a row at one level have every `raw_mic_` channel with the speaker's `start`
and `end` `found`, raise the speaker's cap 600 mB after a run that misses and
run again; play one step above that level.

The audible bench makes that ramp after both alone-ramps, each run one run
of the scenario itself (a priming play on the talker, then the example) with
the talker at its play level and the speaker's cap at the run's level:

- the levels are the Flex's alone-level as found, then every 600 mB above it,
  ending at the ceiling;
- a run whose plays did not run as asked stops it, at any level;
- it stops at the first level at which both of the Flex's markers are found
  on every raw microphone in three runs in a row, and plays nothing louder; a
  run that misses ends its level; whether the talker was found is printed
  and is not a stop;
- an undetected run above −4000 mB is read before the next, whether or not
  earlier runs at its level were detected. The Flex's tone,
  over 0.8 s placed by the clock tie, must stand 10 dB above the larger of
  its 900 Hz and 1100 Hz neighbours on some raw microphone, or the ramp stops:
  speech has no line that stands out so across 0.8 s, a tone that reached the
  microphones does. A tone that stands out at a step 2000 mB or more above
  the ramp's first step stops the ramp as not recognised: a score is about
  the square root of the marker's share of the band's energy, so 8 dB of
  level carries a marker from the search's floor, about 0.1, to the 0.25
  threshold, and the rule allows 12 dB more before it calls the play
  unexplained by its level. Otherwise the ramp goes on;
- the scenario then plays the Flex one step above the level found, never
  above the ceiling, and runs three times.

Measured on the same bench, the talker at −7800 mB, in three ramps an hour
after the alone-level runs: the Flex's raw-microphone scores were, start and
end, 0.09 to 0.13 and 0.30 to 0.36 at −6000 mB; 0.14 to 0.20 and 0.49 to 0.54
at −5400 mB; and at −4800 mB 0.30 to 0.35 and 0.58 to 0.70, detected. The
scenario played the Flex at −4200 mB. A fourth ramp, in a later lane run,
read 0.30 to 0.33 and 0.30 to 0.34 at −6000 mB and stopped there, detected;
the scenario then played the Flex at −5400 mB (see *The anchoring scenario*).
Those four ramps took one run per level, so a run that scored high by chance
stopped the ramp below the level the scenario needed. With the talker
playing, the Flex's start marker had scored over the threshold in one run of
four at −6000 mB and two of ten at −5400 mB.

In the next lane run, the first to count three runs in a row, the ramp under
the talker read, start and end: at −6000 mB one run, 0.08 to 0.13 and 0.29
to 0.34, missed; at −5400 mB one run, 0.15 to 0.17 and 0.48 to 0.51, missed;
at −4800 mB three runs, 0.28 to 0.32 and 0.62 to 0.67, 0.28 to 0.32 and 0.61
to 0.67, 0.29 to 0.33 and 0.63 to 0.68, each detected. It found −4800 mB, and
the scenario played the Flex at −4200 mB.

Why one level scores two ways was read from the kept runs at −6000 and
−5400 mB: in all three high runs the Flex's play started 0.98 to 1.06 s after
the talker's in the capture, and in all thirteen low runs 0.80 to 0.88 s
after. The speech clip's 500–3500 Hz band over the Flex's start-marker span
is about 12 dB quieter at the later placement (−32 dB of full scale against
−20 dB in the source), and at the microphones the band under that span read
−70 to −73 dB in the high runs against −61 to −62 dB in the low, with the
half second before it at −61 dB in both and the Flex's own tone the same at
each level. So a high score is the start marker landing in a quieter stretch
of the speech; the talker's delay estimate, the talker's level and the
Flex's level were ruled out. The ramp that counted three in a row had no
high run at −6000 or −5400 mB, and its three runs at −4800 mB scored alike.

What moved the two plays against each other was read from all 34 scenario
runs of that day (`run-directory.md`, *The same device played and
recorded*): not either output's path, but the runner's work between the
talker's reply and the speaker's command, and the speaker's own pre-roll
from its command to its first mark, both on the machine's clock. Those runs
were **untimed**: the example sent the talker's play, waited one second, and
sent the speaker's.

The example now states both starts (*Placing a play in time*). In the lane
run of the same afternoon all eight scenario runs were timed, five in the
ramp under the talker and three of the scenario itself. Each play's first
mark lay 0.17 to 0.32 ms after its stated start on the Flex and 0.41 to
0.86 ms after it on the sink; each thread woke 0.04 to 0.16 ms after the
start. The two first marks were 999.4 to 1000.0 ms apart for a stated
1000 ms, the two clock readings having been taken 0.1 ms apart. In the six
runs where alignment located both plays, the speaker's play lay 755.3 to
760.5 ms after the talker's in the capture, a spread of 5.2 ms, in one
group: the stated second less the Bluetooth path's quarter second, which the
harness does not correct for. The two clusters do not occur in timed runs.
The ramp under the talker read, start and end: at −6000 mB 0.09 to 0.13 and
0.27 to 0.30, missed; at −5400 mB 0.15 to 0.17 and 0.45 to 0.48, missed; at
−4800 mB three runs at 0.28 to 0.31 and 0.61 to 0.66, each detected. It
found −4800 mB, as the untimed ramp that counted three in a row had.

So, in a timed run, a run that misses at a level where an earlier run was
detected is not explained by where the plays fell against each other. Above
−4000 mB the bench reads that miss, and a Flex's tone that stands out there
stops the ramp as not recognised: past −4000 mB the bench plays nothing
louder for a miss its level does not explain. Every run is kept with its
scores, so such a stop shows a detection and a miss at one level, which is
what tells it from a play the level does not explain.

`examples/anchoring_scenario.py` takes its levels from the configuration: it
sends each playback role `check` before the capture starts and plays each
source at its role's `cap_mb`. A configuration for it sets the talker's cap
one step above the talker's level found alone, and the speaker's, whose play
starts under the talker's, one step above the level found with the talker
playing.

### On a sound-server sink

A playback role with `output = "pulse"` plays on one sink of the desktop's
sound server: any server that speaks the PulseAudio native protocol,
PulseAudio itself or PipeWire's pulse server. The role is the same role; what
it holds and how it reaches the device differ.

```toml
[machines.bench.programs]
pulse_client = { pinned = true }   # the sink client this harness's build pins

[roles.talker]
kind = "playback"
machine = "bench"
# no sample_clock: a sink refuses one

[roles.talker.device]
output = "pulse"
sink = "desk speaker"
source_dir = "/data/lab/sources"
cap_mb = -2400
quiet_hours = [["22:00", "07:00"]]
```

- `sink`: non-empty. Matched case-insensitively as a substring of each sink's
  name or description; exactly one sink must match (`no_sink`,
  `ambiguous_sink`, each naming the sinks there are). A Bluetooth speaker can
  then be named by its description, and its address kept out of the
  configuration; the record keeps the name the server gave.
- `source_dir`, `cap_mb` and `quiet_hours` are as on a card. `level` and
  `held` are not stated: a sink has one control. `card`, `level` or `held`
  under `pulse` is `unknown_key`.
- The machine names `pulse_client` (`missing_program` otherwise). The role
  starts it as a child for each attach, with the socket
  `$XDG_RUNTIME_DIR/pulse/native` and no other: `PULSE_SERVER` and
  `PULSE_RUNTIME_PATH` are not read, so no variable sends a role to another
  server. It authenticates with the cookie at `$HOME/.config/pulse/cookie`
  when there is one. A fault in the client ends one play with `client`, and
  the runner and every other role go on.

**The control.** A sink's volume and mute are one control, `sink` index 0,
a channel per sink channel named by its position (`front-left`, …; a
repeated position gets `#<n>`). A play's `volume_mb` sets it, lowering before
raising, every channel to one volume and unmuted; it is read back, read
again before every chunk, and restored after the stream is closed, exactly
as a card's level control. A balance across the sink's channels is flattened
for the play and restored, each channel to the raw volume found. The server's
volume is a cubic scale of raw integers; the role reads one in millibels
rounded up, so a reading never understates, and sets the largest raw volume
that reads at or under what was asked. Restoration writes the raw volume
first found, not the largest of its reading. A sink refuses a set that does
not name every channel with one switch (`mixer`), and a control other than
`sink` 0 is `no_control`. The sink is found by its name at every read, so a
sink that comes back under that name with other channels reads as `mixer`:
during a play that ends it `controls_moved`, and the restoration fails.

**The stream.** The role makes its own stream on the sink for each play,
made at exactly 0 dB on every channel, unmuted, pinned to the sink, with the
source's rate, channel count and format exactly (`params` otherwise), no
pre-buffer, and corked until its first frames have been sent. It is not a
second control: the output checks it, with the sink, at every read, and a
stream that is not on the sink, not at 0 dB or muted is `stream_moved`. The
client asks a session manager not to restore a saved volume or target onto
the stream and not to save its own (`state.restore-props` and
`state.restore-target`, both `false`), and once the stream is made it sets
the stream's volume to 0 dB itself: on PipeWire a stream nothing has set
read 0 on every channel with its sink at the sink's lowest volume. A server
that restores a saved stream volume anyway, or ignores the one set, is caught
at the read-back before the first frame. During a play the role reports a
moved stream as `controls_moved`.

**The cap on a sink** covers the sink's volume and mute, the stream's volume
and mute, and the stream's sink. It cannot see a knob on the speaker, a
Bluetooth speaker's own mapping of the volume it is sent, any filter or
effect in the server, the server's remix gains, or what the server does with
a channel that has no place on the sink. A Bluetooth speaker's own buttons
move the sink's volume, and end the play, only when the speaker uses absolute
volume; otherwise they are invisible to the role.

- **A sink's level is relative to the sink's full volume**, 0 mB. The device
  entry's `base_volume_mb` is the server's base volume for the sink: where it
  is under 0 (a hardware sink whose mixer goes above 0 dB), the device's own
  gain at 0 mB is above its unamplified level by `−base_volume_mb`, and a
  level on the sink reads lower by that much than the same loudness reads on
  the card opened directly. The cap is each role's own and in its output's
  scale: **it does not carry between a `pulse` role and an `alsa` role on one
  piece of hardware.** Whether a base volume under full marks the mixer's
  0 dB on a real sink is unmeasured.
- A sink with flat volume, under which the server raises the sink to its
  loudest stream, or with no decibel volume, is refused with `sink_volume`
  before any stream exists.
- **A play changes the desktop's volume on that sink** for its length, so
  every other stream on the sink changes loudness, and restores it after. A
  runner killed mid-play leaves the sink at the play's setting, as it leaves
  a card's mixer. Two roles on one sink each hold its volume, so the second
  play's set ends the first with `controls_moved`.

**Channels.** The stream's channel map for a source of `n` channels is the
one PulseAudio's own client library gives a stream that states none: the
default map of the largest count at or under `n` that has one, then `aux0`,
`aux1`, … for the rest.

| Channels | Positions |
|---|---|
| 1 | mono |
| 2 | front-left, front-right |
| 3 | the 2-channel map, then aux0 |
| 4 | the 2-channel map, then rear-left, rear-right |
| 5 | the 4-channel map, then front-center |
| 6 | the 5-channel map, then lfe |
| 7 | the 6-channel map, then aux0 |
| 8 | the 6-channel map, then side-left, side-right |
| 9 to 32 | the 8-channel map, then aux0, aux1, … |

A source of more than 32 channels is `params`. The server remixes by
position onto the sink, and what it does with a channel that has no place
there (an `aux` one on a stereo sink) is the server's: it may drop it.
Dropped is quieter than the cap judged, never louder. The role addresses no
channel of the sink.

**The kept copy** is what the role handed to the sink. The server converts
and remixes it on the way to the device, so it is not a copy of any device's
samples. After an underrun the server may skip frames handed after it ran
out, so the kept copy of a play with an underrun is longer than what
sounded. The first frames are sent before the stream is uncorked, so a
play's head is not skipped. A sink's underrun is reported at the role's next
write: one that happens as the play's last write is sent is not on the play's
record, and such a play can end `completed` with no underrun, having lost
frames of its last tenth of a second. That is read in one server's source and
measured on none.

**Marks and delay.** A mark's `delay_frames` on a sink is **by the sound
server's estimate**: the frames in the stream's buffer plus the server's
latency to the sink, as the server reports them. Once the buffer is seen
empty, the latency is counted down in real time, so the figure reaches 0 at
the end of a play. On a Bluetooth speaker it understated the path by about
a quarter second (*Measured on a Bluetooth speaker*, below).
`unplayed_frames` is that figure too.

**The end of a play** is bounded as on a card (*How the role plays*, step 6).
A server that stops reading the stream and says nothing, a sink suspended by
hand for one, ends the play with `stream_died` within the buffer's remaining
length plus 2 s, the sink's volume restored. A server that announces the
suspension ends it at the role's next read, `controls_moved`.

**The dry run on a sink.** `check` lists the server's sinks, selects one and
reads its volume; it makes no stream and sets nothing. A dry `playback.play`
makes one corked stream on the sink, reads its grant and deletes it, writing
no frame and setting nothing. Whether a corked stream leaves a suspended sink
silent is unmeasured, so this dry run too is made only inside an agreed
window.

**Measured on a Bluetooth speaker.** On 2026-11-14 the audible bench played
on an LG-PL5 over A2DP: PipeWire 1.6.9's pulse server (protocol server
version 15.0.0), the sink 48 kHz, two channels, `s16le`, hardware volume,
base volume 0 mB. Through the ramps, test runs and the anchoring scenario:

- Once the client set the stream's volume itself (*The stream*), every play
  ended `completed`, no underrun, `restored`, no failure: no `stream_died`
  at the end-of-play bound, no false underrun, the stream at 0 dB and
  unmoved at every read. Before that, a session manager's restore had left
  plays' streams under 0 dB, caught as `stream_moved` before a frame.
- The sink read back exactly the level asked (−7800 mB on both channels,
  0 mB under), and its volume was back at what it was before (−4235 mB) after
  every run.
- A play over the cap was refused with `over_cap` before any stream existed,
  and the capture during it was about 60 dB or more under the primer heard
  at the ramp's level.
- A 16 kHz one-channel `s16_le` source was granted as asked, with no
  conversion in the client, and sounded with both markers found on every raw
  microphone (scores 0.65 to 0.89). Alignment reported `drift_ppm` 28 to 62:
  the server's conversion and the speaker's clock against the Flex's,
  together; one run cannot tell them apart. A conversion off by more than
  the ±500 ppm the search spans would show as a marker not found.
- The first mark's `delay_frames` was 1600 frames (100 ms) in every run but
  one; alignment put copy frame 0 243 to 249 ms after where that estimate
  puts it, on every raw microphone. The Bluetooth path's latency is the
  server's estimate plus about a quarter second. In the one run the first
  mark read 4650 frames, and frame 0 sounded where it sounded in the others,
  246 ms after the mark's reading: the estimate moved, the path did not.
- No fade-in was seen: every sink play's primer that sounded reached its
  full level in its second quarter second, the first quarter second being
  the latency above. Every such play came seconds after another stream on
  the speaker, the ramp's quieter steps included, so the fade-in the speaker
  is known to make after minutes of silence had no chance to show, and it is
  unmeasured.
- At the sink's silent minimum, −28899 mB, every play of every ramp's first
  step sounded a burst of about half a second at its start: it began 50 to
  100 ms before where the play's first frame sounds, peaked at −45 dB in the
  500–3500 Hz band on the raw microphones (louder than the primer at −8400 mB,
  about −53 dB) and decayed into the room. It is not the played content: it
  was the same in the primer and in the marked play, whose first half second
  is digital silence, and its 1 kHz level sat about 12 dB under its 900 Hz
  and 1100 Hz neighbours, where the primer's tone stands far above them. It
  is not the end of a play at the restored volume: at −8400 mB the primer's
  tone ended within 30 ms of the role's restore and decayed from its play
  level, with nothing louder after, and no step showed sound after the
  restore. It does not follow the level asked: no step from −12000 to
  −9000 mB sounded it. The same burst, its peak within 2.5 dB, came in eleven
  ramps over two hours. The maintainer's account of it: it is the speaker's
  own chirp, the sound this speaker makes when its volume is set above or
  below a limit.

#### The anchoring scenario

`examples/anchoring_scenario.py` records on `mic` while a talker plays on
`talker` and the robot's own sound, one second later, on `speaker`; it stops
the capture a second after the later of the two plays has ended. It states
both starts, each on its own role's clock reading, two seconds ahead
(*Placing a play in time*), so the same script runs with its roles on one
machine or on two. Both
sources are marked with `lab mark`, one member each, so that `lab assess`
can locate each play in the capture. Roles are named, no device: a lab
configuration for it, with invented names. The example plays each source at
its role's `cap_mb`, so here a cap is the play's level, not a ceiling over
it: set the talker's from the level found for its output alone, and the
speaker's from the level found with the talker playing (*Finding a level*,
*A level under another play*). The caps below are the levels the bench of
that section played at, and are right for no other bench:

```toml
schema = 1

[machines.bench.programs]
pod_capture = { pinned = true }
pulse_client = { pinned = true }

[roles.mic]
kind = "capture"
machine = "bench"
sample_clock = "flex"

[roles.mic.device]
card = "Flex XVF3800 L16K6Ch"
rate_hz = 16000
sample_format = "s16_le"
layout = ["conference_beam", "asr_beam", "raw_mic_0", "raw_mic_1", "raw_mic_2", "raw_mic_3"]

[roles.speaker]
kind = "playback"
machine = "bench"
sample_clock = "flex"

[roles.speaker.device]
output = "alsa"
card = "Flex XVF3800 L16K6Ch"
source_dir = "/data/lab/sources"
cap_mb = -4200                       # the level this example plays at
quiet_hours = [["22:00", "07:00"]]

[roles.speaker.device.level]
element = "PCM"
index = 1

[[roles.speaker.device.held]]
element = "PCM"
index = 0
volume_mb = 0

[roles.talker]
kind = "playback"
machine = "bench"

[roles.talker.device]
output = "pulse"
sink = "desk speaker"
source_dir = "/data/lab/sources"
cap_mb = -7800                       # the level this example plays at
quiet_hours = [["22:00", "07:00"]]
```

`source_dir` holds `talker.wav` and `own.wav`, each made by `lab mark` from a
source of the experimenter's choosing, with different members. The tree's
known speech clip, `brenn_lab/testing/speech/arctic_a0001.wav`, is one
talker; its README says where it comes from and under what licence.

Run on 2026-11-14 on the bench of *Finding a level*, the speech clip as the
talker and a marked 1 s tone as the robot's sound, every run completed with
both plays healthy and the talker found on every raw microphone (scores 0.65
to 0.89). First, each output one step above the level found for it alone
(the Flex's own output at −5400 mB, the LG-PL5 sink at −7800 mB): the
robot's sound was located in one run of four. Its end marker was found every
time (0.46 to 0.54); its start marker, which sounds about a second into the
talker's speech, scored 0.15 to 0.20 in three runs and 0.41 to 0.45 in one,
so the play was not located and alignment failed the run. Then the Flex one
step above the level found with the talker playing (−4800 mB found, played
at −4200 mB), the talker unchanged: both plays were located in nine runs of
nine, the robot's start marker scoring 0.46 to 0.56 and its end marker 0.67
to 0.77, with `drift_ppm` −5.5 to 12.2 on the Flex's play and 28.5 to 35.3
on the talker's. In a later lane run the ramp under the talker stopped at
−6000 mB and the scenario played the Flex at −5400 mB: both plays were
located in one run of three, the robot's start marker scoring 0.48 to 0.53
in that run and 0.15 to 0.19 in the other two, its end marker 0.47 to 0.54
in all three. Then, with the ramp counting a level found only when three runs
in a row there detect the play, it found −4800 mB again and the scenario
played the Flex at −4200 mB: both plays were located in three runs of three,
the robot's start marker scoring 0.47 to 0.61 and its end marker 0.68 to
0.76, with `drift_ppm` −1.0 to 4.9 on the Flex's play and 28.0 to 35.3 on
the talker's. All the runs of this paragraph so far were untimed: the
example waited one second between sending its two plays.

Timed, in the lane run of the same afternoon, the ramp under the talker
found −4800 mB and the scenario played the Flex at −4200 mB: both plays
were located in three runs of three, the robot's start marker scoring 0.46
to 0.51 and its end marker 0.68 to 0.74. Each play's first mark lay 0.21 to
0.32 ms after its stated start on the Flex and 0.41 to 0.59 ms after it on
the sink, and the two first marks were 999.7 to 1000.0 ms apart. In the
capture the robot's play lay 757.8 to 760.5 ms after the talker's; from its
first mark, the Flex's play lay 5.3 to 5.6 ms on, the sink's 244.4 to
247.9 ms. So on one machine a stated gap between two outputs' first marks
holds to under a millisecond, on a card and on a sink alike, and the gap
that sounds is that less the difference of the two outputs' paths, which
alignment measures and the harness does not correct for.

## Starting the runner

```
bazel run //brenn_lab:lab -- dry-run --lab <lab.toml> --machine <name> --script <script.py>
bazel run //brenn_lab:lab -- run     --lab <lab.toml> --machine <name> --script <script.py> --out <dir> [--notes <file>] [--frame-log <path>]
```

`make lab ARGS="dry-run --lab … --machine … --script …"` is the same. Under
`bazel run` a relative path is taken from the directory `bazel run` was
started in. The same entry point has three more commands, `mark`, `align` and
`assess`, described under *Marking a source*, *Aligning a run* and *Assessing
a run*; this section is about `dry-run` and `run`. `run`'s `--frame-log`
names the reader its closing assessment runs instead of the pinned one, for a
lab whose recorder is an installed one newer than the pin. A path that is not
an executable file is refused before anything is created. A program that is
not a frame-log reader is found only by the closing assessment, which then
finds every capture `capture_unreadable`; `lab assess --frame-log` with the
right reader assesses the run again.

stdout carries one JSON document and nothing else, prose goes to stderr:

- `dry-run`: the dry-run report.
- `run`: a pointer,
  `{"run": "<dir>", "outcome": "…", "completed": true, "results": "<dir>/results.json", "usability": {…}}`.
  `usability` is the closing assessment's (*The run*, step 6):
  `{"answer": "clean", "complete": true, "assessment": "<dir>/assessment.json"}`
  with the assessment's `answer` (`clean`, `findings` or `incomplete`) and
  `complete`, or `{"answer": "not_assessed", "reason": "…", "detail": "…"}`
  when none was made, `reason` being `interrupted` (the run's outcome is
  `interrupted`; `lab assess` assesses it when wanted), `refused` (the
  assessment refused the run directory; `detail` gives its reason and
  detail), `raised` (the assessment raised; the traceback is on stderr and
  `detail` is `"<Type>: <message>"`) or `assessment_interrupted` (SIGINT or
  SIGTERM arrived during it).
- a refusal: `{"ok": false, "problems": […]}`. Nothing was created.

### Exit statuses

| Status | `dry-run` | `run` |
|---|---|---|
| 0 | the report has no problem | the run completed, whatever its assessment answered |
| 1 | the report has problems, problems in the configuration or the script included | a run directory was created and the run did not complete |
| 2 | refused: no report could be made | refused before anything was created |
| 3 | the runner raised instead of returning | the runner raised instead of returning |

**Exit status 2** prints the refusal. Its problems have the seven keys of a
dry-run problem (below), and `where` is one of:

| `where` | `reason` | Command |
|---|---|---|
| `arguments` | `usage`: the command line was refused, or asked for help with `-h` or `--help` and so ran nothing; the usage or help text is on stderr. A flag cannot be abbreviated | both |
| `arguments` | `unreadable`: the configuration, the script or the notes file could not be read; or, for `run`, `--frame-log` does not name an executable file | both |
| `arguments` | `unknown_machine`: `--machine` is not in the configuration's `machines` | both |
| `out` | `out_exists`, `out_parent_missing`, `out_in_workspace` | `run` |
| `lab` | the loader's | `run` |
| `script` | `not_loadable`, `bad_roles`, `bad_run` | `run` |
| `role` | `not_configured`, `kind_mismatch` | `run` |

When a configuration has problems, `--machine` is not checked against it: a
dry run reports the `lab` problems (exit 1) and a run is refused with them
(exit 2).

**Exit status 3** means the runner itself did not finish its job: no report
was produced, or a run did not end under the runner's control. Nothing is on
stdout, and the traceback is on stderr. It says nothing about the lab or the
devices. It happens:

- when a dry run is interrupted, by Ctrl-C, by SIGTERM, or by a script that
  raises `KeyboardInterrupt` itself, which a dry run cannot tell apart;
- when a run gets Ctrl-C (SIGINT) before it has taken the signals as its own
  stop (see *Stopping a run*), or its script raises `KeyboardInterrupt` while
  loading. The run directory may then exist, with no `results.json`. SIGTERM
  in that stretch ends the runner at once, with no traceback and no exit
  status of the runner's;
- when a run gets Ctrl-C after it has given the signals back, once its roles
  are released (see *Stopping a run*). Every role has then been released.
  The run directory may hold `results.json`, which then says how the run
  ended, or may lack it, with a `.results.json.tmp` left beside the journal.
  SIGTERM there likewise ends the runner at once. That stretch ends when the
  closing assessment begins: a signal during the assessment gives the run's
  own exit status and a pointer whose `usability` says
  `assessment_interrupted`;
- when a probe's recorder reader fails in a way the role cannot turn into a
  reply;
- when a run could not write its own files or a role's release raised. Every
  role the run used has then already been sent its `release`, and the run
  directory has no `results.json`. A release raises, among other cases, for
  a capture whose recorder reader failed, or a play whose end could not be
  reported, when no command of the script's has raised that
  error first: the role's next start, stop or play raises it, as a
  `RuntimeError` and not a `RoleFailure`, and the run then ends `failed`
  with exit status 1, whether or not the script catches the error.

## The dry run

A dry run records nothing and leaves nothing behind. In order, never stopping
at the first problem:

1. Load the configuration: every problem.
2. Load the script: it raised while loading (`not_loadable`, `SystemExit` and
   an exception deriving from `BaseException` included, `KeyboardInterrupt`
   excepted: that is taken as an interrupt, as in step 5), `ROLES` is missing
   or not a mapping of strings (`bad_roles`), or `run` is missing or not
   callable (`bad_run`).
3. For every role in `ROLES`: not in the configuration (`not_configured`),
   bound there to another kind (`kind_mismatch`), on a machine the runner
   cannot reach (`unreachable`).
4. Send `check` to every role that passed step 3. Every failure is a problem.
5. **Trace the script**: call `run` with a lab whose every `send` is dry and
   whose `wait` returns at once. A failure reply is a problem. A `send` to a
   role that failed step 3 is answered by the runner with that role's step-3
   failure. An exception leaving `run` ends the trace:
   - a `RoleFailure` is not a problem of its own, since the failure reply it
     carries already is one;
   - `KeyboardInterrupt` is taken as an interrupt, and the dry run exits 3;
   - anything else, `SystemExit` and an exception deriving from
     `BaseException` included, is a problem, `where` `trace`, `reason`
     `script_error`.
6. Send `release` to every role that passed step 3. A role whose answer says
   something was still running, by its kind's reading of it (a capture), is
   a problem, `left_running`: the script ends without stopping it. A dry
   play is never left running, since no time passes in a trace: a script
   that waits too short a time for its play is seen only in the run.

Steps 2 to 6 run only if step 1 found nothing. Step 5 runs whenever the
script loaded, whatever steps 3 and 4 found. A failure reply whose role and
reason repeat that role's problem from step 3 or step 4 is not listed again,
so the problems a faulty role adds do not depend on which commands the script
sends it, or in what order.

A script that does not catch `RoleFailure`, traced against a card that is not
there, gives exactly one problem (`where` `role`, `reason` `no_card`, from
step 4) and an incomplete trace.

**The trace's limit.** It follows the path the script takes when every
command succeeds with the probe's answers and no time passes. A branch taken
only on a real reading is not checked.

### The report

```json
{"schema": 2, "ok": false,
 "problems": [{"where": "role", "reason": "no_card", "detail": "…", "role": "mic",
               "command": "check", "key": null, "data": {"at_frame": null, "at": {…}, "cards": […]}}],
 "roles": {"mic": {"kind": "capture", "machine": "bench", "check": {…the whole reply…}}},
 "trace": {"complete": false, "commands": 1, "stopped_at": "RoleFailure: … at ten_seconds.py:11"}}
```

- `ok`: true when `problems` is empty.
- Every problem has exactly seven keys, in this order, `null` where a key does
  not apply:
  - `where`: the step that found it (below).
  - `reason`, `detail`.
  - `role`: the role, for `where` `role`.
  - `command`: the command whose reply failed. `check` for a step-4 problem;
    `null` for a step-3 problem and for `left_running`.
  - `key`: for `where` `lab`, the dotted path of the configuration value at
    fault (the empty string for the whole file).
  - `data`: what the reason carries (`run-directory.md`, *Failure data*), `{}`
    when nothing.
- `roles.<r>`: `kind`, the kind the script's `ROLES` expects; `machine`, the
  configured machine, or `null` when the role is not configured; `check`, the
  whole encoded reply to `check`, `result_type` included, or `null` for a role
  that failed step 3.
- `trace`: `complete`; `commands`, every command the script sent, those the
  runner answered itself included; `stopped_at`, `null` when complete,
  otherwise `"<ExceptionType>: <message> at <script file>:<line>"`.
- When step 1 or step 2 found a problem, the report is
  `{"schema": 2, "ok": false, "problems": […], "roles": {}, "trace": null}`.

| `where` | Found by | `reason` |
|---|---|---|
| `lab` | step 1 | the loader's |
| `script` | step 2 | `not_loadable`, `bad_roles`, `bad_run` |
| `role` | steps 3, 4 and 6, and a failure reply in step 5 | step 3: `not_configured`, `kind_mismatch`, `unreachable`; step 6: `left_running`; otherwise the role's failure reason unchanged |
| `trace` | step 5: an exception other than `RoleFailure` leaving `run` | `script_error` |

## The run

`run` does not dry-run first: it sends no `check` and traces nothing.

1. Read the configuration, the script and the notes file, each once, and load
   the script from the bytes read. **Refuse before creating anything** (exit
   2) what the files and the arguments alone show to be wrong (see *Exit
   statuses*): a file that cannot be read, a configuration with problems, an
   unknown `--machine`, a script that does not load, a `ROLES` naming a role
   that is not configured or is bound to another kind, and an `--out` that
   exists, whose parent does not exist, or that lies inside the workspace.
   A role on a machine the runner cannot reach is not refused: such a run
   is created and fails at the first command to that role, with
   `unreachable`.
2. Create the run directory, its `.gitignore` first; copy the script, the
   configuration and the notes into it; write the first manifest; open the
   journal.
3. Call `run(lab)`. Every command, reply and event is journaled as it
   happens.
4. When `run` returns, or anything at all leaves it, send `release` to every
   role the script used that the runner hosts. A role on an unreachable
   machine is sent none. A release is journaled with `by` `runner`. A file
   of its own that the runner cannot write does not keep any role from its
   release.
5. Write `results.json`.
6. Unless the run was interrupted, assess it (*Assessing a run*), writing
   `alignment.json` and `assessment.json`, and put the assessment's answer in
   the pointer's `usability`. Nothing the assessment does changes the exit
   status or `results.json`. For the length of the assessment, SIGINT and
   SIGTERM each end it, and the pointer says `assessment_interrupted`.

Whatever leaves the script's `run`, the run ends under the runner's control,
writes its results and exits 1: a `KeyboardInterrupt` a script raises, an
exception deriving from `BaseException`, a `SystemExit`. Each is a
`script_error` unless a role's failure came first. What the results say, and
which one thing `failure` names, is in `run-directory.md`, *`results.json`*.

**Stopping a run.** From just before the script's `run` is called until its
roles are released, SIGINT (Ctrl-C) or SIGTERM to the runner stops the
script. A signal that arrives while a role is handling a command takes effect
once the reply is recorded, or once the role's raise is recorded if the role
raised instead of replying: the script then sees the stop, not the role's
error, and the role's raise stays in its `failures`.
The roles are released, results are written with `outcome` `interrupted`, and
the runner exits 1. Within that stretch neither signal raises
`KeyboardInterrupt` in the script, and one that arrives while the roles are
being released interrupts nothing but still makes the outcome `interrupted`.
Before the stretch and after it, while the results are written, the runner has Python's default handling of both signals: those
cases are under *Exit statuses*, exit status 3.

### Where a run may be written

Wherever `--out` says, which must not exist yet and whose parent must. Under
`bazel run` a run directory inside the brenn-lab workspace is refused
(`out_in_workspace`): recordings never enter the repository. That refusal
holds only under `bazel run`. A runner started directly from its built binary
does not know its workspace and cannot refuse it.

What holds in every case is that **every run directory ignores itself**: its
first file is a `.gitignore` containing `*`, so git ignores the whole run in
whatever checkout it lies. Whoever wants a run tracked in a repository of
their own deletes that file.

### A recorder left running

The runner stops every capture it started when the script ends, however it
ends. If the runner is killed outright (SIGKILL, a power loss), the recorder,
which runs in a session of its own, keeps recording and keeps the device. The
next start on that device fails with `open`. The recorder's pid is in the
run's `logs/runner.log`. Send it SIGTERM and it stops cleanly.

## Marking a source

```
bazel run //brenn_lab:lab -- mark --source <in.wav> --out <out.wav> --member <0…510>
```

writes `<out.wav>`: the source with the harness's marker of member
`--member` before its content and after it, with silence around both, at the
source's own rate, channel count and sample format. The content's frames are
unchanged. `run-directory.md`, *The marker*, defines the marker and the marked
file's layout. Play the marked file as any other source; the playback role
still transforms nothing.

**One member per play that may sound in one capture**, across every role. Two
plays of one member are told apart only when one role plays both and every
capture that holds one holds both; otherwise alignment reports them
`ambiguous` by name. The family has 511 members.

When a play starts, the role recognises the marker from the source's samples
and records it: a script reads it back as `started.source.marker`, and the
manifest's source entry and the results' record carry the same object. A
file altered after marking (normalised, resampled, trimmed) carries none,
and plays as before with `marker: null`. Nothing is refused on the marker's
account.

stdout is one JSON document:

```json
{"marked": "<out.wav>",
 "source": {"frames": 312000, "rate_hz": 16000, "channels": 2, "sample_format": "s16_le"},
 "marker": {"definition": "gold-bpsk-1", "member": 3, "start_code": 6, "end_code": 7,
            "start": {"frame": 8000, "frames": 8193}, "end": {"frame": 336193, "frames": 8193}}}
```

`source` describes the file given; `marker` is the record a play of the
marked file will carry, its spans in the marked file's frames.

| Status | `mark` |
|---|---|
| 0 | the marked file was written |
| 2 | refused, nothing written: `where` `mark`, `reason` `unreadable` (the source cannot be read), `source_format` (not a WAV of 16- or 32-bit integer PCM, as the playback role refuses it), `rate_unsupported` (below 8000 Hz), `member_range` (outside 0 to 510) or `out_exists`; or `where` `arguments`, `reason` `usage` |
| 3 | the command raised; nothing is left at `<out.wav>` |

## Aligning a run

```
bazel run //brenn_lab:lab -- align --run <dir> [--frame-log <path>]
```

searches every capture of the run for the markers of every marked play and
writes `<dir>/alignment.json`: for each play, capture and capture channel,
whether each marker was found, where (in the capture's frames), the offset
of the kept copy in the capture, and the drift between the two clocks.
`run-directory.md`, *`alignment.json`*, says what every key means and what
the search can and cannot find. No clock reading is used, so the captures
may come from any machine or device; the whole of each capture is searched.

A run's closing assessment, and `lab assess`, align the run in the same pass
(*Assessing a run*). `align` changes nothing else in the run, and may be run again: the new report
names the one it replaced. It may be run on a run still in progress, and
then describes the run so far.

Captures are read with brenn-pod's `frame-log`, the one the harness's build
pins. `--frame-log <path>` runs another, for a capture made by an installed
recorder newer than the pin; the report says which reader was used.
Aligning loads numpy, and with it the host's C++ runtime and zlib. Its
memory does not grow with a capture's length, and the run's filesystem needs
room for one capture's export at a time (`run-directory.md`, *The statistic
and its limits*).

stdout is one JSON document, a pointer:
`{"run": "<dir>", "report": "<dir>/alignment.json", "ok": true, "summary": {…}}`,
`summary` as in the report. `ok` restates the exit status, and is not a
verdict on the run.

| Status | `align` |
|---|---|
| 0 | a report was written; every capture and play was read and every marked play located in some capture. A run with no marked play aligns with 0 |
| 1 | a report was written that says otherwise |
| 2 | refused, no report written: `where` `run`, `reason` `unreadable` (no directory, no `manifest.json` or `results.json` that is JSON of the expected shape), `layout` (not layout 4) or `not_writable` (the run directory cannot be written: a read-only disk, a directory without write permission, or a leftover `.alignment.tmp/` that cannot be removed, which the detail names; decided after the run is read and before any capture is read); or `where` `arguments`, `reason` `usage` |
| 3 | the command raised; a `.alignment.tmp/` left in the run is not part of the record, and the next `align` clears it, or refuses with `not_writable` if it cannot |

`align` writes its report and its scratch into the run and nowhere else. To
align an archived or read-only run, copy it somewhere writable, and make the
copy's directories writable too: a copy can keep the source's read-only
modes.

## Assessing a run

```
bazel run //brenn_lab:lab -- assess --run <dir> [--frame-log <path>]
```

states whether the run is usable. In alignment's pass it reads every capture
once, measures every channel (level, offset, silence, a stuck value, samples
at the edge of the format), states every loss of samples from both the
recorder's account and the reader's map, reads how each play ended, and
searches for the plays' markers. It writes `<dir>/alignment.json`, then
`<dir>/assessment.json`, whose `answer` is `clean`, `findings` or
`incomplete`. `run-directory.md`, *`assessment.json`*, says what every key,
finding and `not_assessed` entry means. No finding rests on a threshold: an
experiment that needs a bar reads the measurements and sets its own.

`lab run` makes the same assessment at the end of every run that was not
interrupted (*The run*, step 6). `assess` may be run again on any run, an
older one included; each report names the one it replaced. It may be run on
a run still in progress, and then describes the run so far. It reads
captures as `align` does, with the pinned `frame-log` or the one
`--frame-log` names, loads numpy as `align` does, and needs the same room.

stdout is one JSON document, a pointer:

```json
{"run": "<dir>", "assessment": "<dir>/assessment.json", "alignment": "<dir>/alignment.json",
 "answer": "clean", "complete": true, "findings": 0, "not_assessed": 0}
```

| Status | `assess` |
|---|---|
| 0 | both reports were written and `answer` is `clean` |
| 1 | both reports were written and `answer` is `findings` or `incomplete` |
| 2 | refused, nothing written: `where` `run`, `reason` `unreadable` (as for `align`, and also a manifest or results with a key the assessment reads of the wrong type, or missing; the detail names the file and the key), `layout` or `not_writable`, as for `align`; or `where` `arguments`, `reason` `usage` |
| 3 | the command raised; a `.alignment.tmp/` left in the run is cleared by the next `align` or `assess` |

SIGINT to `assess` raises: the exit status is 3, nothing is on stdout, and
the scratch directory is removed on the way out. SIGTERM ends it at once and
can leave `.alignment.tmp/`, which the next `align` or `assess` clears.
