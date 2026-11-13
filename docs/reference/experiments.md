# Experiments: the lab configuration, scripts, the dry run and the run

An experiment is a script run against a lab. The **lab configuration** says
which machines and devices there are. The **script** says what to do with
them. The **dry run** checks a script against a lab without recording
anything, and the **run** carries it out and leaves a run directory, which
`run-directory.md` describes.

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
  uses `pod_capture`, brenn-pod's recorder.
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
    machine does not name is `missing_program`. The playback kind needs none.
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
  script may catch it; the run still does not complete.
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
| Commands | `check`, `release`, `capture.start`, `capture.stop`, `playback.play`, `playback.stop` |
| Results | `capture.started`, `capture.stopped`, `capture.checked`, `capture.released`, `playback.started`, `playback.stopped`, `playback.checked`, `playback.released` |
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
  `layout`, `start`, `device_firmware`, `device_configuration`. These are the
  manifest's capture entry (see `run-directory.md`).
- `capture.stop` → `capture.stopped`: `capture`, `frames`, `gaps_reported`,
  `end`.
- `check` → `capture.checked`: `recorder`, `device`, `granted`, `layout`.
- `release` → `capture.released`: `stopped`, a `capture.stopped` or `null`.

`build.dirty` and `device.usb_id` may be `null`. `device_firmware` and
`device_configuration` are always `{"reason": "…", "read": false}` in this
version: the recorder sends the device no control command.

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
| `recorder_timeout` | no first line within 10 s of the start, or no final line within 15 s of the stop; the recorder is killed |
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
and quiet hours that only its configuration sets. In this version its one
output kind is `alsa`: an ALSA card opened directly, written by the runner's
own interpreter. Every level is an integer number of **millibels** (mB),
hundredths of a decibel, the unit ALSA reports volumes in.

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

- `output`: `"alsa"`; any other value is `bad_value`.
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

- `playback.play {source, volume_mb}` → `playback.started`. `source` is a
  path relative to `source_dir`; `volume_mb` is the level control's setting
  for this play. The reply comes once the first frames have been handed to
  the device; the play goes on on a thread of the role's, and the script is
  not blocked for the length of the source.
  - `play`: the role's play number in the run, from 1; 0 in a dry reply.
  - `source`: `{name, sha256, frames, rate_hz, channels, sample_format,
    peak_mb}`: the file as named, the hash of its bytes, what it holds, and
    its largest sample magnitude relative to full scale, rounded up; `peak_mb`
    is `null` for a source of silence.
  - `copy`: the `Location` of the kept copy; `null` in a dry reply.
  - `output`: `{kind, library: {name, version}}`: `"alsa"`, and the host's
    ALSA library with the version it reports.
  - `device`, `granted`: the card, and what the open stream granted, in the
    capture kind's shapes.
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
  - `first`: the mark after the first write; `null` in a dry reply.
- `playback.stop` → `playback.stopped`: cuts the running play. Frames still in
  the device's buffer are discarded, the stream is closed and the controls are
  restored. `play`, `frames_handed`, `unplayed_frames` (the device's delay
  read just before the cut, `null` when it could not be read), `underruns` (a
  count), `last` (the mark after the last write; `null` in a dry reply), `end`
  (a clock reading once the stream is closed), `restored` (whether every
  control was put back as found). `not_playing` when nothing is playing, a
  play that already ended included.
- `check` → `playback.checked`: `output`, `device`, `controls` (each with its
  `range`, `{min_mb, max_mb}` or `null`, and its channels as found), `cap_mb`,
  `quiet_hours`, `local_time`, `quiet_now`. It resolves the card and reads the
  mixer. It sets nothing and opens no stream. It is refused with
  `already_playing` while a play runs.
- `release` → `playback.released`: `stopped`, a `playback.stopped` when a play
  was running, else `null`. A play cut by `release` is `left_running`.

A **mark** is `{frames_handed, delay_frames, at}`: the frames handed so far,
the device's delay read then (`null` if the read failed), and the role's clock
reading taken directly after. By the device's own account, frame
`frames_handed − delay_frames` of the copy is at its output at `at`.

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
- `playback.underrun`: `{play, at_frame, at, detail}`. The device ran dry
  before frame `at_frame` of the copy was handed; the stream was recovered
  and the play goes on. Sound was interrupted there for an unknown length.
- `playback.failed`: `{play, failure, …the fields of playback.stopped…}`. The
  play ended and nobody asked: the stream died, a held control moved, quiet
  hours began, the recoveries ran out, or the role's own code failed.

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
   After the last it lets the device play its buffer out, closes the stream,
   restores the controls and reports `playback.ended`.

- An **underrun** is recovered and reported as `playback.underrun`. Eight
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

A switch going off is a lowering and one going on is a raise. Of the role's
own writes, on a mixer that takes each write as written, rounded down, or
refuses it and changes nothing: **after every write the role makes,
successful or refused, from its first of a play to its last, either the level
is at or under the cap or no channel is above where the role found it.**

- The restoration puts back what was found, a setting above the cap
  included: the device's state before the role touched it is its owner's.
- **`restored` false** means the restoration failed (the device went away,
  its first read failed, or a mixer write was refused) and was not tried
  again. If the read failed, nothing was written, and the controls are where
  the play set them, at or under the cap. If a lowering was refused, nothing
  was raised: every channel is at or under what the play set. If a raise was
  refused, no channel is above where it was found. The device may then be
  quieter than it was found on some channel, and after a refused lowering a
  channel may stand above where it was found, at what the play set. That is a
  change to the machine's sound state the role could not undo.
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
the kept copy is written. Before each chunk the thread judges again, against a stalled or
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
`AGENTS.md` says (`output-open-not-listened-to`).

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
| `open` | the device could not be opened, for one because something else holds it |
| `params` | the device did not grant the source's rate, channel count and format exactly |
| `mixer` | a mixer call failed: the mixer could not be opened or loaded, a read or write was refused, or a volume has no dB scale (such a control cannot be held to a cap in millibels) |
| `controls` | a control read back above what was set, or with a switch off |
| `controls_moved` | during a play a held control changed, or could not be read |
| `stream_died` | the stream failed beyond recovery: a write failed with anything but an underrun, a recovery failed, eight ran without a successful write, a write took no frames, or the buffer could not be played out |
| `role_io` | the role could not write or read its kept copy, or another file of its own |
| `role_error` | the play's thread raised; the play ended, the controls were restored, and the exception was raised on |
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
gets `alsa_missing` from `check` and from `playback.play`.

## Starting the runner

```
bazel run //brenn_lab:lab -- dry-run --lab <lab.toml> --machine <name> --script <script.py>
bazel run //brenn_lab:lab -- run     --lab <lab.toml> --machine <name> --script <script.py> --out <dir> [--notes <file>]
```

`make lab ARGS="dry-run --lab … --machine … --script …"` is the same. Under
`bazel run` a relative path is taken from the directory `bazel run` was
started in.

stdout carries one JSON document and nothing else, prose goes to stderr:

- `dry-run`: the dry-run report.
- `run`: a pointer,
  `{"run": "<dir>", "outcome": "…", "completed": true, "results": "<dir>/results.json"}`.
- a refusal: `{"ok": false, "problems": […]}`. Nothing was created.

### Exit statuses

| Status | `dry-run` | `run` |
|---|---|---|
| 0 | the report has no problem | the run completed |
| 1 | the report has problems, problems in the configuration or the script included | a run directory was created and the run did not complete |
| 2 | refused: no report could be made | refused before anything was created |
| 3 | the runner raised instead of returning | the runner raised instead of returning |

**Exit status 2** prints the refusal. Its problems have the seven keys of a
dry-run problem (below), and `where` is one of:

| `where` | `reason` | Command |
|---|---|---|
| `arguments` | `usage`: the command line was refused, or asked for help with `-h` or `--help` and so ran nothing; the usage or help text is on stderr. A flag cannot be abbreviated | both |
| `arguments` | `unreadable`: the configuration, the script or the notes file could not be read | both |
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
  SIGTERM there likewise ends the runner at once;
- when a probe's recorder reader fails in a way the role cannot turn into a
  reply;
- when a run could not write its own files or a role's release raised. Every
  role the run used has then already been sent its `release`, and the run
  directory has no `results.json`.

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
{"schema": 1, "ok": false,
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
  `{"schema": 1, "ok": false, "problems": […], "roles": {}, "trace": null}`.

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

Whatever leaves the script's `run`, the run ends under the runner's control,
writes its results and exits 1: a `KeyboardInterrupt` a script raises, an
exception deriving from `BaseException`, a `SystemExit`. Each is a
`script_error` unless a role's failure came first. What the results say, and
which one thing `failure` names, is in `run-directory.md`, *`results.json`*.

**Stopping a run.** From just before the script's `run` is called until its
roles are released, SIGINT (Ctrl-C) or SIGTERM to the runner stops the
script. A signal that arrives while a role is handling a command takes effect
once the reply is recorded.
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
