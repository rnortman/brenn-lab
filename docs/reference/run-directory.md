# The run directory, layout 1

A run directory is what one run of an experiment script leaves behind. This
document says what every file and key in it means, so that a reader with the
directory and this document needs nothing else. How to write a script and
start a run is in `experiments.md`.

## Layout

```
<run>/
  .gitignore           one line, `*`: the first file created; git ignores the whole run
  manifest.json        from the start; rewritten whole when a role reports device facts
  journal.jsonl        every command, reply and event, appended as it happens
  results.json         written last; absent when the run did not end under the runner's control
  script/<file name>   the script that ran, byte for byte
  lab/<file name>      the lab configuration as read, byte for byte
  notes/<file name>    the caller's free-form notes, if any were given
  captures/<role>/<NNNN>.framelog
  sources/<role>/<NNNN>.wav
  streams/<role>/…     reserved
  logs/runner.log
  logs/<role>/recorder-<NNNN>.stdout.jsonl
  logs/<role>/recorder-<NNNN>.stderr.log
```

- `<NNNN>` counts a role's captures in a run, from `0001`. A role may capture
  more than once, and a number is never reused within a run: a start that
  failed still uses its number.
- `script/`, `lab/` and `notes/` hold the bytes the runner read and ran, under
  the file name they were given with. The manifest gives each one's SHA-256.
- `captures/<role>/<NNNN>.framelog` is what the recorder, brenn-pod's
  `pod-capture`, wrote: a frame log in brenn-pod's format, read with brenn-pod's
  `frame-log` tool. A start that failed after the recorder created its file can
  leave a file here that the manifest and results name nowhere.
- `sources/<role>/<NNNN>.wav` is the **kept copy** of a playback role's play
  `<NNNN>`, numbered from `0001` like captures: a WAV holding exactly the
  frames the role handed the device, in order. The role plays from this file,
  so it is what was sent. It is written and synced before the play starts,
  and only once the play has passed its limits: a play refused for its
  source, the cap or quiet hours leaves nothing. A play that fails after
  that, at the open or at the controls, leaves a copy the manifest and
  results name nowhere, and its number is used.
- `logs/<role>/recorder-<NNNN>.stdout.jsonl` is the recorder's stdout as it
  printed it, one JSON event line per line. `…stderr.log` is its stderr. Both
  are copied as they arrive. A device probe (the dry run's `check`) leaves no
  log files.
- `logs/runner.log` is the runner's own log. It has one line for every
  recorder started, `<role>: capture <n>: pod-capture pid <pid>`. When a script
  raised, it holds the traceback. When the run was told to stop, it says so.
- Nothing is written outside the run directory, and the directory must not
  exist before the run. A run never touches another run.

### The run's `.gitignore`

The first file the runner creates in a run directory is `.gitignore`, whose
only content is `*` and a newline. Git then ignores everything in the
directory, that file included, whatever checkout the directory lies in and
however the runner was started. Recordings never enter a repository by a
`git add .`. Because the file is created first, a run that dies at any later
point is already hidden. To track a run in a repository of your own, delete
the file.

### Reserved names

`streams/` (other recorded streams such as pose, status and faults, per role)
is **reserved**. Its name and meaning are fixed by layout 1, and no code in
this version creates it. The manifest's and the results' key `streams` is
reserved the same way.

### The version rule

`manifest.json`'s `layout` is 1. A new role kind that writes under a reserved
name, or adds a section of its own beside `captures`, does not change it. A
reader ignores sections of kinds it does not know. Renaming, removing or
changing the meaning of anything in layout 1 changes the number.

## Common shapes

### Clock readings

Every timestamp in a run is a pair of readings on one machine:

```json
{"machine": "bench",
 "monotonic": {"clock": "CLOCK_MONOTONIC", "us": 81234567},
 "wall": {"clock": "CLOCK_REALTIME", "us": 1794000000000000}}
```

`us` is microseconds on the named clock. The runner's own readings are on the
runner's machine. A role's readings are on the role's machine. Readings on
two machines cannot be compared, and nothing in this version converts between
a role's clock and the runner's.

The recorder's event lines carry `mono_us` and `wall_us` and do not name
their clocks. The harness labels them `CLOCK_MONOTONIC` and `CLOCK_REALTIME`
because brenn-pod's frame-log reference says those are the recorder's
clocks. The labels are that document's statement, not something the recorder
reported.

### Pointers

The manifest and the results name a file by a pointer:

```json
{"machine": "bench", "path": "captures/mic/0001.framelog", "in_run": true}
```

With `in_run` true, `path` is relative to the run directory. With `in_run`
false, `path` is an absolute path on the named machine: the file was written
there and not brought into the run. In this version a role on the runner's
own machine writes inside the run, so every capture is `in_run` true.

### Problems

A run's `failure` is a problem: an object with exactly these seven keys, in
this order, `null` where a key does not apply.

```json
{"where": "role", "reason": "no_card", "detail": "…", "role": "mic",
 "command": "capture.start", "key": null, "data": {…}}
```

- `where`: `role`, `script` or `runner` in a run's results.
- `reason`: a word from the vocabularies below.
- `detail`: prose.
- `role`: the role, for `where` `role` and for `left_running`.
- `command`: the command whose reply failed; `null` when an event carried the
  failure, and for anything that was not a reply.
- `key`: `null` in a run (it names a configuration key in a dry-run report).
- `data`: what the reason carries (see *Failure data*); `{}` when nothing.

### What `dirty` means

In every revision in a run, `dirty` true means a tracked file differed from
the commit. Untracked files do not count. That is what brenn-pod's build stamp
means by it, and the harness's stamp means the same. The harness's build lists
every source file by name, so a new source file reaches the runner only
through a change to a tracked BUILD file, which is dirty until it is
committed. A commit that names a source file and leaves the file itself
untracked stamps clean: the runner then holds code that no commit has, and
`dirty` false does not show it.

`dirty` is `null` when a build declared its commit rather than reading it
from a checkout, and `null` means "not asserted", not "clean". The recorder
this repository's build pins is such a build: it reports `source` `declared`,
`dirty` `null`, and as `commit` the brenn-pod commit Bazel fetched, the one
`MODULE.bazel` pins.

## `manifest.json`

Written when the run directory is created, and rewritten whole (to a
temporary file, then a rename) each time a capture or a play starts.

```json
{"layout": 1,
 "run_id": "<uuid4>",
 "created": {…clock reading, runner's machine…},
 "runner": {"machine": "bench"},
 "harness": {"repository": "brenn-lab", "commit": "<40 hex>", "dirty": false, "source": "git"},
 "script": {"file": "script/ten_seconds.py", "sha256": "…", "given_as": "examples/ten_seconds.py",
            "roles": {"mic": "capture"}},
 "lab": {"file": "lab/lab.toml", "sha256": "…"},
 "notes": {"file": "notes/room.md"},
 "repositories": [
   {"repository": "brenn-lab", "commit": "…", "dirty": false, "source": "git",
    "from": "the harness's build stamp"},
   {"repository": "brenn-pod", "commit": "…", "dirty": null, "source": "declared",
    "from": "the recorder's report at the start of capture mic/0001"}],
 "roles": {"mic": {
   "kind": "capture", "machine": "bench",
   "configured": {…roles.mic.device…},
   "stated_by_configuration": {…roles.mic.stated…},
   "sample_clock": null,
   "captures": [{…a capture entry…}]}}}
```

- `run_id`: a random UUID, the same in `results.json`.
- `created`: when the run directory was made.
- `runner.machine`: the machine the runner was told it is on (`--machine`).
- `harness`: the revision of brenn-lab the runner was built from, from its
  build stamp (see *The harness's revision*). When that is not known:
  `{"repository": "brenn-lab", "commit": null, "dirty": null, "source": "unknown", "reason": "…"}`.
- `script.given_as`: the `--script` argument as the caller typed it.
  `script.roles`: the script's `ROLES`.
- `notes`: `null` when no notes file was given.
- `repositories`: the revision of every repository involved, each entry saying
  where it came from.
  - The first is always brenn-lab: the `harness` block plus `from`.
  - A recorder's entry is the recorder's repository and its build as the
    recorder reported it at the start of a capture, plus `from`, which names
    that capture. It is added when a capture starts whose repository and
    build no entry has yet, so two captures by one build give one entry,
    naming the first. It is never taken from the configuration.
  - **An absent repository is one nothing reported.** A run in which no
    capture started has no brenn-pod entry.
- `roles.<r>`: one section per role in the script's `ROLES`, an unreachable
  role's included.
  - `configured`: the role's device parameters, as the configuration gives
    them.
  - `stated_by_configuration`: the configuration's free-form `stated` table,
    what a person says about the device. It is a statement, never a reading.
    `null` when the configuration states nothing.
  - `sample_clock`: the configuration's `roles.<r>.sample_clock`, or `null`.
    Two roles with the same name here are stated to sample on one clock; a
    statement, never a reading.
  - One key more, the role's kind's section, holding its entries: for a
    capture role, `captures`, one entry per capture whose start the recorder
    reported, in order; for a playback role, `sources`, one entry per play
    started, in order. Each is empty until one starts. A role has its own
    kind's key and no other kind's.
- Nothing is added to `repositories` by a play: no other repository's code
  plays. The host's ALSA library is named with its version in every source
  entry.

### A capture entry

```json
{"capture": 1,
 "capture_id": "<32 hex>",
 "recorder": {"program": "pod-capture", "repository": "brenn-pod",
              "build": {"commit": "…", "dirty": null, "source": "declared"}},
 "device": {"selector": "…", "card_index": 2, "card_name": "…", "card_long_name": "…",
            "pcm": "hw:2,0", "usb_id": "2886:0022"},
 "granted": {"rate_hz": 16000, "channels": 6, "sample_format": "s16_le",
             "period_frames": 320, "buffer_frames": 5120},
 "layout": ["conference_beam", "…"],
 "start": {…clock reading, the role's machine…},
 "device_firmware": {"reason": "…", "read": false},
 "device_configuration": {"reason": "…", "read": false},
 "file": {…pointer…}}
```

The entry is the role's `capture.started` result with its `location`
replaced by `file`, the pointer to the frame log. A field that result gains
later appears here too.

- `capture`: the capture's number in the role, the `<NNNN>` of its files.
- `capture_id`: the recorder's own id for the recording.
- **Three things are kept apart.**
  - `recorder` is the program that recorded and its build: `build.commit`,
    `build.dirty` (may be `null`) and `build.source`, as the recorder reports
    them.
  - `device` and `granted` are the card as ALSA and USB name it (`usb_id` may
    be `null`) and what the open stream granted.
  - `device_firmware` and `device_configuration` are the device's own firmware
    and configuration. In this version both are always
    `{"reason": "…", "read": false}`: the recorder sends the device no control
    command, so neither is read. They are never absent and never filled from
    the recorder's build or the card's name. `{"read": true, "value": …}` is
    reserved for a later version; nothing in this version writes it.
- `layout` is the channel names the configuration gave and the recorder
  wrote into the log. It is the configuration's naming of the channels, not a
  reading of the chip's routing. The harness holds the recorder's reported
  layout equal to the one it sent, and `granted.channels` equal to its
  length; a capture where they differ fails with `recorder_contract`.
- `start`: the recorder's reading at the start of the capture.

### A source entry

```json
{"play": 1,
 "source": {"name": "clip.wav", "sha256": "…", "frames": 312000, "rate_hz": 16000,
            "channels": 2, "sample_format": "s16_le", "peak_mb": -602},
 "output": {"kind": "alsa", "library": {"name": "libasound.so.2", "version": "1.2.13"}},
 "device": {…as in a capture entry…},
 "granted": {"rate_hz": 16000, "channels": 2, "sample_format": "s16_le",
             "period_frames": 320, "buffer_frames": 8000},
 "controls": [{"element": "PCM", "index": 1, "part": "level",
               "channels": [{"channel": "Front Left", "volume_mb": -1800, "switch": true}, …]}, …],
 "found": [{…the same shape, as found…}],
 "level_mb": -1800, "cap_mb": -1800,
 "local_time": "2026-11-13T14:38:31-05:00",
 "first": {"frames_handed": 1600, "delay_frames": 1600, "at": {…clock reading…}},
 "file": {…pointer…}}
```

The entry is the role's `playback.started` result with its `copy` replaced by
`file`, the pointer to the kept copy. `experiments.md`, *The playback kind*,
says what each field means.

- `source` is the file the script named, as the role read it. `peak_mb` is
  its largest sample magnitude relative to full scale, rounded up, `null` for
  silence.
- `level_mb` is the level at which a full-scale sample is played: the level
  control's volume plus the held controls', from the values read back. It is
  what the cap bounds. **The play's own peak level** is
  `level_mb + source.peak_mb`, relative to the loudest the device's output can
  be driven; no field carries it.
- `controls` are the controls as the role set and read them back, on every
  channel; `found` is how they stood before the role moved them, which is
  what the role restores.
- `output.library` is the host's ALSA library, which the build does not pin,
  with the version it reported.
- `first` is a **mark**: the frames handed so far, the device's own delay
  reading then, and the role's clock reading directly after. Frame
  `frames_handed − delay_frames` of the kept copy is, by the device's
  account, at its output at `at`.

### The harness's revision

The runner is stamped at build time with the commit and cleanliness of the
tree it was built from. Under `bazel run` the build and the run are one step.
A runner built earlier and started directly reports the tree as it was at its
build, not as it is now. `source` is `git` when the stamp holds a commit, and
`unknown` with a `reason` when the workspace was not a git checkout or git
failed.

## `journal.jsonl`

One JSON object per line, appended and flushed as the runner sees each
message:

```json
{"seq": 1, "at": {…}, "by": "script", "role": "mic", "command": "capture.start", "dry": false, "args": {}}
{"seq": 2, "at": {…}, "by": "script", "role": "mic", "command": "capture.start", "dry": false, "ok": true, "result_type": "capture.started", "result": {…}}
{"seq": 3, "at": {…}, "role": "mic", "event": "capture.gap", "data": {…}}
```

- `seq` counts from 1. `at` is the runner's clock reading when the line was
  written.
- `by` is on a command's line and on its reply's, and says who sent the
  command: `runner` for a `release`, `script` for everything else. An event's
  line has no `by`.
- The rest of the line is the message as it crosses the seam (see
  `experiments.md`, *The seam*): a command, a reply (`ok` true with
  `result_type` and `result`, or `ok` false with `failure`), or an event.
- A reply to a role on a machine the runner cannot reach is the runner's own,
  journaled like any other.
- **Order.** Lines are in the order the runner saw them. A role reports events
  from a thread of its own, so an event line may sit between a command's line
  and its reply's, and an event of a capture may come before the reply to the
  command that started it (a capture that dies at once). A command's reply is
  the next reply line of that role, not necessarily the next line.
- The journal is the record if the runner dies. A directory with a journal
  and no `results.json` still says what was sent and what came back.

## `results.json`

Written last, to a temporary file and then renamed. **A run directory without
`results.json` is a run that did not end under the runner's control**: the
runner was killed, or it could not finish its own files. The journal and the
manifest still stand. A `.results.json.tmp` or `.manifest.json.tmp` beside
them is such a write left unfinished, and is not part of the record.

```json
{"schema": 1, "run_id": "…",
 "outcome": "completed", "completed": true, "failure": null,
 "started": {…}, "ended": {…},
 "recorded": {"captures_started": 1, "captures_stopped": 1, "frames": 159680},
 "captures": [{
   "role": "mic", "capture": 1, "capture_id": "…", "file": {…pointer…},
   "ended": "stopped", "stopped_by": "script",
   "frames": 159680, "gaps_reported": 0,
   "gaps": [], "late": [],
   "loss_free": true, "failure": null,
   "end": {…clock reading…}}],
 "played": {"plays_started": 0, "plays_completed": 0, "frames_handed": 0},
 "sources": [],
 "roles": {"mic": {"kind": "capture", "commands": 3, "failures": []}},
 "usability": {"assessed": false, "reason": "this harness version makes no usability assessment"}}
```

- `outcome`: `completed`, `failed` or `interrupted`. `completed` is
  `outcome == "completed"`. A run completed when the script's `run` returned,
  no reply was a failure, no role reported a failure event, and `release`
  found nothing running. A script that catches a role's failure and carries
  on still has a run that did not complete.
- `failure`: `null` for a completed run, otherwise a problem (see
  *Problems*) naming one thing:
  - the runner was told to stop (SIGINT or SIGTERM), while the script ran or
    while the roles were released: `where` `runner`, `reason` `interrupted`,
    whatever else happened;
  - otherwise the first of these, in the order they happened:
    - a failure reply or a failure event: `where` `role`, with the role, its
      reason, detail and data, and `command` the command whose reply failed
      (`null` for an event);
    - anything else that left the script's `run`: `where` `script`, `reason`
      `script_error`, `detail` `"<ExceptionType>: <message>"`, `role` `null`;
      the traceback is in `logs/runner.log`;
    - `release` finding a capture or a play still running: `where`
      `runner`, `reason` `left_running`, with the role.

  A run that did not complete always names a failure.
- `started`, `ended`: the runner's readings when the run started and when the
  results were written.
- `recorded` answers "did this run record anything":
  `captures_started` counts the captures whose start was reported,
  `captures_stopped` those of them that ended `stopped`, and `frames` sums
  every capture's `frames` that is not `null`. A run that recorded nothing
  has `frames` 0 or `captures_stopped` 0 here, whatever else it says.
- `captures`: one record per capture whose start was reported, by role and
  number. A start that failed has no record.
  - `ended`: `stopped` or `failed`.
  - `stopped_by`: `script`, or `runner` when the runner stopped it at
    `release`; `null` for a failed capture.
  - `frames`, `gaps_reported`: the recorder's own counts at the stop. `null`
    for a failed capture, except one the recorder stopped cleanly with nobody
    asking, which keeps its counts.
  - `gaps`: every gap the recorder reported,
    `{cause, at_frame, frames, at, detail}`, with `frames` `null` for a loss of
    unknown length.
  - `late`: every time the recorder fell a period or more behind with nothing
    lost, `{at_frame, backlog_frames, at}`.
  - `loss_free`: true only when the capture ended `stopped`, `gaps` is empty
    and `gaps_reported` is 0. A gap of unknown length cannot be read as no
    gap.
  - `failure`: `null`, or the role's failure as it crossed the seam,
    `{reason, detail, data}`. It is not a seven-key problem.
  - `end`: the recorder's reading at the stop; `null` for a failed capture.
- `played` answers "did this run play anything": `plays_started` counts the
  plays whose start was reported, `plays_completed` those of them that ended
  `completed`, and `frames_handed` sums every play's frames handed to its
  device. A run with no playback role has all three 0.
- `sources`: one record per play whose start was reported, by role and
  number. A play refused at its start has no record.
  - `file`: the pointer to the kept copy. `source`: `{name, sha256}` of the
    file the script named.
  - `ended`: `completed` (the source was handed over whole and the device
    played its buffer out), `stopped` or `failed`.
  - `stopped_by`: `script`, or `runner` when the runner cut the play at
    `release`; `null` otherwise.
  - `frames`: the kept copy's frames. `frames_handed`: how many of them were
    handed to the device. `unplayed_frames`: the device's delay read just
    before a cut, the handed frames that were discarded; 0 for a completed
    play, `null` when it could not be read.
  - `underruns`: every underrun, `{at_frame, at, detail}`. Sound was
    interrupted at `at_frame` for an unknown length.
  - `as_sent`: true only when the play ended `completed` with no underrun:
    the device took every frame of the copy as given.
  - `first`, `last`: the marks after the first and the last write. `end`: the
    role's reading once the stream was closed.
  - `restored`: whether the role put every control back as found
    (`experiments.md`, *The cap*, says what false means).
  - `failure`: `null`, or the role's failure as it crossed the seam.
- `roles.<r>`:
  - `commands`: every command the role was sent, the runner's `release`
    included: the number of that role's command lines in the journal.
  - `failures`: every failure of the role, in order, first or not.
    `{"command": "<name>", "reason", "detail", "data"}` for a failure reply,
    `{"event": "<name>", "reason", "detail", "data"}` for a failure event. A
    capture that dies unasked and is then stopped by the script has two
    entries: the event's and the stop's reply.
- `usability`: where a later version states levels, clipping, markers and
  drift. `assessed` is false and says so, so that no reader takes silence for
  a pass.

### Failure data

What a failure's `data` holds, by reason. The reasons themselves are
described in `experiments.md`, *Failure reasons*.

| Reason | `data` |
|---|---|
| the recorder's ten (`destination_exists`, `no_card`, `ambiguous_card`, `open`, `params`, `destination`, `stream_died`, `destination_full`, `write_failed`, `write_stalled`) | `at_frame` (`null` before a log exists) and `at` (a clock reading on the role's machine); `cards`, each `{index, name, long_name}`, as well when the recorder listed cards (`no_card`, `ambiguous_card`); `log_complete: "not_established"` as well for `write_stalled` |
| `recorder_missing` | `{program}`: the path tried, the configured one or the pinned recorder's |
| `recorder_usage` | `{channel}` when the harness refused a channel name with a comma; `{stderr, exit_status}` when the recorder refused its command line |
| `recorder_exited` | `{exit_status}` |
| `recorder_contract` | `{line}` for a line outside the recorder's contract; `{exit_status}` for an exit status that disagrees with the last line; `{reason}` for a failure reason the recorder's contract does not list |
| `recorder_timeout`, `stopped_unasked`, `already_capturing`, `not_capturing` | empty |
| `unknown_command` | `{command}` |
| `role_io` | `{path, errno}`; `path` may be `null` |
| `unreachable` | `{machine}`: the role's machine |
| `not_configured`, `kind_mismatch` | empty |
| `script_error`, `left_running`, `interrupted` | empty |

A playback role's reasons carry their own data. Where a name is also one of
the capture kind's, this table holds for a playback role's failure:

| Reason | `data` |
|---|---|
| `no_card`, `ambiguous_card` | `selector`, the configured `card`; `cards`, each `{index, name, long_name}`; for `ambiguous_card`, `matched`, the matching cards' indices. Empty when the cards could not be enumerated |
| `no_control`, `unheld_control` | `{controls}`, each `{element, index}` |
| `volume_range` | `control`, the control as `'<element>' index <n>`; `min_mb` and `max_mb` when it has a volume |
| `over_cap` | `{level_control_mb, held_mb, level_mb, cap_mb}`: the level control's volume, the held controls' sum, their total, and the cap |
| `quiet_hours` | `{start, end, local_time, seconds}`: the span, the local time judged by, and the seconds of play judged (0 for a check between chunks) |
| `open` | `{pcm}`, the device opened |
| `params` | `{rate_hz, channels, sample_format}`: what was granted when the grant differed, what was asked when the device refused it |
| `controls` | `{controls}`: every control as read back, in `playback.started`'s shape |
| `controls_moved` | `{control}`, the control that moved; empty when the controls could not be read |
| `source_changed` | `{sha256}`: the hash the source had when it was judged |
| `unknown_command` | `{command}` |
| `source_outside`, `source_missing`, `source_format`, `alsa_missing`, `mixer`, `stream_died`, `role_io`, `role_error`, `already_playing`, `not_playing` | empty |

After `write_stalled` the frame log's completeness was not established;
brenn-pod's `frame-log` tool is the reader that can say more.

## A recorder left running

The recorder runs in a session of its own, so a Ctrl-C at the runner's
terminal reaches the runner alone, and the runner stops every capture at the
end of a run. If the runner itself is killed outright (SIGKILL, a power
loss), the recorder keeps recording and keeps the device, and the next start
on that device fails with `open`. Find its pid in `logs/runner.log` and send
it SIGTERM. It then stops cleanly and finishes its frame log.

## The same device played and recorded

`sample_clock` states that two roles sample on one clock; nothing checks it.
For a playback role and a capture role on one device that state the same
`sample_clock`, the run records:

- for each play, the kept copy, the frames handed to the device, every
  underrun with its frame, and two marks, `first` and `last`, each tying a
  frame count to the machine's monotonic clock and to the device's delay
  reading;
- for each capture, the recorder's start reading, taken directly after its
  stream was triggered, and in the frame log a clock reading per read.

From those the offset between a frame of the kept copy and a frame of the
capture is known to within milliseconds, not frames: the two streams are
started independently, and nothing the device or ALSA reports ties a frame of
one to a frame of the other. On a device that samples both on one clock the
offset is constant across a play with no underrun; correlation finds it once.

Not measured by this harness: the offset to the frame by any means but
correlation; the latency from the capture's trigger to its frame 0; whether
the device's delay reading is accurate. The playback role's audible-lane test
asserts that the clock tie lies within 150 ms of the offset correlation
finds, which holds the delay reading, the capture's start latency and the
clock readings together and separates none of them. An exact tie needs the
device's own copy of what it played inside the capture, and the reSpeaker
Flex's six-channel image carries none (`own-device-playback-sample-relationship`).
