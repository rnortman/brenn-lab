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
  alignment.json       written by `lab align`, only when asked; absent until then
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
- `alignment.json` is not written by the run. `lab align` writes it later,
  on request, and changes nothing else in the directory (see
  *`alignment.json`*). A `.alignment.tmp/` directory or an
  `.alignment.json.tmp` file is an alignment left unfinished, and is not part
  of the record. The next `align` removes a leftover `.alignment.tmp`,
  whatever it is (a directory, a file or a link), and a leftover
  `.alignment.json.tmp` that is a file or a link. It writes through neither
  and follows no link: removing a link removes the link and never its
  target. A leftover `.alignment.tmp` it cannot remove is refused with
  `not_writable`, the detail naming the path that could not be removed.

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

`manifest.json`'s `layout` is 1, and `results.json`'s and `alignment.json`'s
`schema` are each 1. Adding a file or a key changes none of them: a new role
kind that writes under a reserved name, or adds a section of its own beside
`captures`, a new key in an entry, a new file beside the others. A reader
ignores the files, keys and sections of kinds it does not know. Renaming,
removing or changing the meaning of anything changes the number of the file
it is in, and of the layout when it is in the layout.

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
            "channels": 2, "sample_format": "s16_le", "peak_mb": -602,
            "marker": {"definition": "gold-bpsk-1", "member": 3, "start_code": 6, "end_code": 7,
                       "start": {"frame": 8000, "frames": 8193},
                       "end": {"frame": 299807, "frames": 8193}}},
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
- `source.marker` is the harness's marker the file carries, recognised from
  its samples when the play was judged, before the kept copy was written:
  the definition's identifier, the member, its start and end codes, and where
  each marker lies in the kept copy (`frame`, its first frame, and `frames`,
  its length). `null` when the file carries none, which refuses nothing: an
  unmarked file, or a marked one altered since (normalised, resampled,
  trimmed). An entry written before the harness recorded markers has no
  `marker` key. *The marker* below defines each identifier.
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
 "played": {"plays_started": 0, "plays_completed": 0, "plays_marked": 0, "frames_handed": 0},
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
  `completed`, `plays_marked` those of them whose source carries a marker,
  and `frames_handed` sums every play's frames handed to its device. A run
  with no playback role has all four 0.
- `sources`: one record per play whose start was reported, by role and
  number. A play refused at its start has no record.
  - `file`: the pointer to the kept copy. `source`: `{name, sha256}` of the
    file the script named. `marker`: the source entry's `source.marker`, the
    same object or `null`.
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

## `alignment.json`

Where each marked play lies in each capture, found by searching the captures
for the plays' markers. `lab align --run <dir>` writes it, only when asked
(`experiments.md`, *Aligning a run*); the run itself never does. It is written
to `.alignment.json.tmp`, synced and renamed, and it is the only file `align`
leaves: `manifest.json`, `results.json` and the journal are not touched, so
none of them becomes untrue. The report is written into the run, so a run
directory that cannot be written (a read-only filesystem, a directory without
write permission) is refused with `not_writable`, before any capture is read,
and nothing in it changes. So is a run whose leftover `.alignment.tmp/`
cannot be removed; that leftover may then be partly removed, and nothing
else in the run changes. The report is its own record. It makes no
statement of usability, and `results.json`'s `usability.assessed` stays false.

No clock reading is used, for anything: not to decide which pairs are tried,
not to narrow a search, not to attribute a marker. Every capture is searched
whole for every marker. Positions come from the sound alone.

```json
{"schema": 1,
 "run_id": "…",
 "harness": {"repository": "brenn-lab", "commit": "…", "dirty": false, "source": "git"},
 "written": "2026-11-13T21:04:10-05:00",
 "replaces": null,
 "reader": {"program": "frame-log", "pinned": true, "path": null},
 "results_read": true,
 "clock_readings": "not_used",
 "marker": {"definition": "gold-bpsk-1", "statistic": "rho", "threshold": 0.25,
            "band_hz": [500, 3500]},
 "plays": [{"role": "speaker", "play": 1, "file": {…pointer…}, "state": "marked",
            "reason": null, "detail": null, "marker": {…the source entry's marker…}}],
 "captures": [{"role": "mic", "capture": 1, "file": {…pointer…}, "rate_hz": 16000,
               "layout": ["…"], "exported_frames": 417600,
               "holes": [{"at_frame": 0, "filled_frames": 0, "basis": "…"}],
               "tail": "ended", "failure": null}],
 "pairs": [{"play": {"role": "speaker", "play": 1}, "capture": {"role": "mic", "capture": 1},
            "tried": true, "not_tried": null,
            "channels": [{"channel": "raw_mic_0",
                          "start": {"outcome": "found", "reason": null, "score": 0.81,
                                    "midpoint_frame": 41234.37, "instances": 1},
                          "end": {…},
                          "ratio": 1.0000021, "ratio_basis": "measured", "drift_ppm": 2.1,
                          "drift_unmeasured": null, "offset_frames": 29138.4}]}],
 "failures": [{"kind": "marker", "play": {…}, "capture": {…}, "channel": "asr_beam",
               "marker": "end", "outcome": "not_found", "reason": "below_threshold",
               "detail": "…"}],
 "summary": {"plays": 1, "marked": 1, "located": 1, "captures": 1,
             "marker_failures": 1, "read_failures": 0}}
```

- `harness`: the revision of the harness that wrote this report, from its
  build stamp (see *The harness's revision*). It may differ from the
  manifest's, which is the harness that ran.
- `written`: local time, with its offset, when the report was made.
- `replaces`: `null`, or `{harness, written, sha256}` of the `alignment.json`
  this one replaced, `sha256` being of that file's bytes. When the previous
  file could not be parsed, `harness` and `written` are `null`.
- `reader`: the program that read the captures, brenn-pod's `frame-log`.
  `pinned` true is the one the harness's build carries; false is one named
  with `--frame-log`, at `path`.
- `results_read`: false when the run left no `results.json`. Then nothing is
  known of what was handed to a device, and every marker is tried.
- `clock_readings`: always `not_used`.
- `marker`: what was searched for: the marker definition this harness
  implements, the statistic and the threshold (below), and the band searched,
  in Hz.

### Plays

Every source entry of every playback role, with its pointer and its record
copied from the manifest (`marker`, `null` when it is `null` or absent), and
a `state`:

- `marked`: the record is an object of the definition searched for, and the
  kept copy bears it out: the copy's frames, rate, channels and format are the
  entry's, and the marker recognised in its samples is the record.
- `no_marker`: the record is `null`. Not tried.
- `no_marker_record`: the entry has no `marker` key; the run was made before
  the harness recorded markers. Not tried, and not searched for any
  definition: the run does not say what its sources carried.
- `failed`, with a `reason` and a `detail`: `copy_missing` (the kept copy is
  not in the run), `copy_differs` (it is there and differs from the entry or
  from the record), `unknown_definition` (the record names another
  definition, which this harness cannot search for).

### Captures

Every capture entry of every capture role, whether or not any play is
marked, so that the report always says whether each capture is readable.
A capture is read through `frame-log export-wav`, into
`.alignment.tmp/export.wav`. One capture's export stands there at a
time: it is removed once `align` has finished with that capture, before the
next is exported, and the directory is removed when the alignment ends.

- `rate_hz`, `layout`: the entry's grant and layout. Channels are named by
  the layout, never by index.
- `exported_frames`: the frames of the exported file; `holes`: every hole the
  reader filled, `{at_frame, filled_frames, basis}`, `at_frame` in the
  capture's frame index; `tail`: the reader's verdict on how the log ends
  (`ended`, `unterminated`, …). Each `null` when the capture was not read.
- `failure`: `null`, or `{reason, detail}`: `not_in_run` (the pointer is not
  in the run), `missing` (no file), `export_refused` (the reader refused it:
  an overlap, a file past the 4 GiB WAV limit, which six channels of 16-bit at
  16 kHz reach after about 6.2 hours, a log with no hello), `reader_usage`
  (the reader refused its command line), `reader_missing` (the reader could
  not be started), `reader_contract` (its output is not what its contract
  says: a map without its keys, runs and holes that do not tile the file, a
  status outside its own), `capture_differs` (the exported file's rate,
  channel count or sample width is not the entry's grant, or its channel
  count not the layout's length). `detail` holds the reader's stderr or the
  reason.

A position in the exported file is reported as a frame of the capture, `run
first frame + (position − run's position in the file)`, through the run of
the reader's map that contains it; never in the exported file's frames. The
recorder's frame index advances across `measured` and `not_sent` holes and
not across `clock_estimate` and `unknown` ones, so across an unmeasured hole
the capture's frame index is not a time axis.

### Pairs

Every play is paired with every capture, on every machine. `tried` is true
when the play is `marked`, the capture was read and its rate is at least
8000 Hz; otherwise `not_tried` says why, `play_not_marked`,
`capture_failed` or `capture_rate_too_low`, and `channels` is empty. No
clock reading decides whether a pair is tried.

In a tried pair every channel of the capture is searched, by its layout name,
for both of the play's markers, and reported on its own: channels of one
device differ in what they carry and in delay. A processed channel that
removes the played sound reports `not_found` beside raw channels that report
`found`.

For each of `start` and `end`:

- `outcome`: `found`, `not_found`, `ambiguous` or `not_tried`.
- `reason`: `null` when found; otherwise:
  - `not_handed` (`not_tried`): the results show the marker was never handed
    whole to the device: the play's `frames_handed` is not past its last
    frame. It is not searched for.
  - `below_threshold` (`not_found`): nothing scored at or above the threshold.
  - `in_hole` (`not_found`): the instance the marker was attributed overlaps
    a hole the reader filled.
  - `instances_exceed_plays`, `count_mismatch`, `shared_across_roles`
    (`ambiguous`): see *Attribution*.
- `score`: the instance's `rho` when found; otherwise the largest `rho`
  anywhere in the channel for that marker's code; `null` when not tried.
- `midpoint_frame`: the capture frame of the marker's midpoint, fractional,
  refined between frames; `null` unless found. The midpoint, not the first
  frame, is reported because a rate offset does not move it.
- `instances`: how many instances of the marker's code the channel holds;
  `null` when not tried.

And for the pair on that channel:

- `ratio`: capture frames per source frame, `(end midpoint − start midpoint)
  / (source frames between the two markers' midpoints in the kept copy)`.
  `null` unless both markers are found and `drift_unmeasured` is `null`.
- `drift_ppm`: `(ratio / (capture rate / source rate) − 1) · 10⁶`, in parts
  per million. Positive means the capture counted more frames between the
  markers than the nominal rates predict. One device playing and recording on
  one clock is expected to show none.
- `drift_unmeasured`: `null`, or why `ratio` and `drift_ppm` are `null` though
  both markers were found: `capture_hole` (a `clock_estimate` or `unknown`
  hole lies between the midpoints, so the capture's index skipped time) or
  `underrun` (the play's results record an underrun between the markers, so
  the source skipped time).
- `offset_frames`: the capture frame at which frame 0 of the kept copy lies,
  fractional: `start midpoint − r · (the start marker's midpoint in the kept
  copy)`, `r` being `ratio` when it was measured and the nominal
  `capture rate / source rate` otherwise. `null` unless the start marker is
  found; it is never derived from the end marker alone.
- `ratio_basis`: `measured` or `nominal`, which `r` was used. With `nominal`,
  a 500 ppm offset between the two clocks moves `offset_frames` by about
  0.4 ms.

### Attribution

For one capture channel and one code, the plays whose marker of that code is
tried in the capture are matched against the instances of the code found, in
capture order. No clock is read.

| Plays | Instances | Outcome |
|---|---|---|
| one | none | `not_found`, `below_threshold` |
| one | one | `found` |
| one | more than one | `ambiguous`, `instances_exceed_plays`: something else sounded this marker |
| several, all on one role | as many as plays | `found`: the k-th instance for the k-th play by play number, since one role plays its sources one at a time in the order they were started |
| several | none | `not_found` for each |
| several | any other count | `ambiguous` for each: `shared_across_roles` when the plays are on more than one role, else `count_mismatch` |

An instance that overlaps a hole is `not_found`, `in_hole`.

A marker not handed whole takes no part. One handed whole and then discarded
at a cut does: whether it sounded is not something the run knows, and where
other plays share its code each of them is then `ambiguous`,
`count_mismatch`. **The remedy is one member per play that may sound in one
capture**, on every role: a member used twice is attributed only when one
role plays it and the capture holds every play of it.

### Failures and the summary

- `failures` lists every failure flat: `{"kind": "play", "play", "reason",
  "detail"}`, `{"kind": "capture", "capture", "reason", "detail"}`, and
  `{"kind": "marker", "play", "capture", "channel", "marker", "outcome",
  "reason", "detail"}` for each marker `not_found` or `ambiguous`. A marker
  `not_tried` is not a failure.
- `summary`: `plays` and `captures` counted; `marked`, the plays whose state
  is `marked`; `located`, the marked plays with both markers found on one
  channel of at least one capture; `marker_failures`; `read_failures`, the
  play and capture failures.
- `lab align` exits 0 when `read_failures` is 0 and every marked play is
  located, and 1 otherwise. A play and a capture that never overlapped in
  time are tried and are `not_found`, which the report cannot tell from a
  marker that sounded and was missed without reading a clock; a play located
  in any one capture counts.

### The statistic and its limits

Each channel is band-passed to 500–3500 Hz (a windowed sinc of
`round(0.016 · rate) | 1` taps under a Hann window, halves rounded away from
zero), so hum, rumble and a microphone's offset do not count as energy, and
correlated with the marker's complex template generated at the capture's own
rate. Nothing is resampled. The score at each position is

```
rho = √2 · |correlation| / (‖template‖ · ‖band-passed channel over the template's length‖)
```

and 0 where that energy is below 10⁻⁹ of the channel's largest. `rho` is 1
for a clean marker alone, and does not depend on the capture's level or the
marker's; `rho²` is the share of the energy over the marker's half second
that the marker accounts for. A position at or above the **threshold, 0.25**,
is an instance; the strongest suppresses everything within one marker length
either side.

What that implies, on synthetic signals; **none of it has been measured on a
played run**:

- Another member's marker scores at most about 0.17, however loud. Noise,
  level bursts and tones stay near 0.1.
- A marker is found down to about 12 dB below the other sound in its band
  over its own half second; below that it is `not_found`.
- A clock offset between player and capture is tolerated inside one marker up
  to about ±500 ppm, and measured between the two markers. A path that drifts
  faster, or inserts or drops frames inside a marker, makes it `not_found`.
- **How many plays may overlap.** Each member is searched for by its own code,
  so attribution sets no limit; the score does. `n` markers of equal level
  that overlap wholly each score `1/√n`: 0.71 for two, 0.58 for three, 0.50
  for four, 0.35 for eight, and the threshold itself for sixteen. Sixteen is
  where the rule meets the threshold, not a number of plays to plan for:
  anything else in the band, the plays' own content included, lowers the
  score further. Markers that overlap in part score higher. Past the limit
  nothing is misattributed: each marker under the threshold is `not_found`
  with the score seen.

**Memory and disk.** `align` holds no array of a capture's length. Each
channel is read from the exported file twice, block by block: once for its
largest energy, which the 10⁻⁹ rule needs before any position is scored, and
once to score every position, keeping only the positions at or above the
threshold, their neighbours and the largest score. What its memory does grow
with:

- the number of codes searched for in one capture: one template and one
  transformed response each, about 4 MB a code;
- the number of positions at or above the threshold, a few for each marker
  that sounded.

Searching one channel of 2²² frames at 16 kHz for two codes peaks at about
44 MB, the same as one of 2²⁰ frames. A channel of ten minutes at 16 kHz
searched for eight codes took about 2.5 s where it was measured. The 4 GiB limit of the export is
the one ceiling on a capture's length. The run's filesystem needs room for
the largest capture's export, in the run's `.alignment.tmp/`.

### A run still in progress, and older runs

`align` locks nothing. On a run still recording, the manifest is whole at
every instant and a capture still being written exports with an
`unterminated` tail; the report describes the run so far. A run made before
the harness recorded markers has no `marker` key in its source entries, and
its plays are `no_marker_record`. A run made before the playback kind has no
`sources` in its results; its captures are read as in any other run.

## The marker

A source can carry the harness's marker, so that `lab align` can find where
it lies in a capture without any clock. `lab mark` writes one
(`experiments.md`, *Marking a source*). Each definition is stated here under
its identifier. A definition that a released harness has written into a run
is never removed from this document; a later one is a new section beside it.

### `gold-bpsk-1`

Half a second of band-limited pseudo-noise: a binary code of 1023 chips,
phase-keyed onto a 2 kHz carrier. A marked source carries two, one before its
content and one after, with different codes. The family has 511 members;
member `m` (0 to 510) uses code `2m` for its start marker and code `2m + 1`
for its end marker.

**The codes.** Two ten-stage shift registers, both starting all ones (the
generators of the GPS C/A code):

- `G1`: output is stage 10; the xor of stages 3 and 10 is shifted into stage 1.
- `G2`: output is stage 10; the xor of stages 2, 3, 6, 8, 9 and 10 is shifted
  into stage 1.
- Each is stepped 1023 times. Code `s` (0 to 1021), chip `k` (0 to 1022):
  `bit = G1[k] xor G2[(k + s) mod 1023]`; the chip `c_k` is `+1` for bit 0 and
  `−1` for bit 1.
- Check: code 1018 is GPS PRN 1, whose first ten bits are `1100100000`.

**The waveform**, in continuous time `t` seconds from the marker's start, for
`0 ≤ t ≤ 0.512`:

```
Tc   = 1/2000 s                                    one chip
p(u) = cos²(π·u / (2·Tc))  for |u| < Tc, else 0     a Hann pulse two chips wide
b(t) = Σ_k c_k · p(t − (k + 1)·Tc)                  k = 0 … 1022
s(t) = 0.5 · b(t) · cos(2π · 2000 · t)              as a fraction of full scale
```

**Rounding.** Every `round` in this definition is to the nearest integer,
with halves rounded away from zero.

**At rate `R`**, the marker is `M = floor(0.512·R) + 1` frames; frame `n`
holds `round(s(n/R) · 2^(bits − 1))` in the source's sample format, `bits`
being 16 or 32, the same value on every channel. Its peak is half of full
scale. A rate below 8000 Hz carries no marker: the marker's band, about 800
to 3200 Hz, needs it.

**A marked source**, made from `C` content frames at rate `R`:

```
lead     round(0.5·R)  frames of silence
start    M frames, the member's start marker
gap      round(0.25·R) frames of silence
content  C frames, unchanged
gap      round(0.25·R) frames of silence
end      M frames, the member's end marker
tail     round(0.25·R) frames of silence
```

Same rate, channels and sample format as the content. The lead keeps the
start marker clear of what an output does to the first frames of a stream;
the tail keeps the end marker clear of the close.

**Recognition.** When a play is judged, the playback role reads the source's
first `lead + M` and last `M + tail` frames. It takes the member from the
signs of channel 0 at each start chip's centre, `t = (k + 1)·Tc`, where the
neighbouring pulses are zero and the carrier is at a peak, and then
regenerates both of that member's markers and compares them with the samples
of both spans on every frame and every channel, to within one least
significant bit. Any difference, a file too short, or no member matching:
the source carries none (`marker: null`).

Any change to this section changes the identifier's trailing number.

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
`lab align` is that correlation for a marked source: it reports the offset
and the drift between the markers in `alignment.json`, reading no clock.

Not measured by this harness: the offset to the frame by any means but
correlation; the latency from the capture's trigger to its frame 0; whether
the device's delay reading is accurate. The playback role's audible-lane test
asserts that the clock tie lies within 150 ms of the offset correlation
finds, which holds the delay reading, the capture's start latency and the
clock readings together and separates none of them. An exact tie needs the
device's own copy of what it played inside the capture, and the reSpeaker
Flex's six-channel image carries none (`own-device-playback-sample-relationship`).
