# The run directory, layout 2

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
  alignment.json       written by `lab align`, `lab assess` and a run's closing assessment
  assessment.json      written by `lab assess` and a run's closing assessment
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
  raised, it holds the traceback. When a role raised while handling a
  command the script sent, it holds that traceback, whether or not the script
  caught the error. A role that raised at the runner's own `release` is not
  noted here: the runner reports it on stderr and exits with status 3. When
  the run was told to stop, it says so.
- Nothing is written outside the run directory, and the directory must not
  exist before the run. A run never touches another run.
- `alignment.json` and `assessment.json` are written after `results.json`:
  both by the run's closing assessment, which `lab run` makes at the end of a
  run that was not interrupted, and by `lab assess`; `alignment.json` alone by
  `lab align`. None of them changes anything else in the directory (see
  *`alignment.json`* and *`assessment.json`*). An `.assessment.json.tmp` file
  is an assessment left unfinished, and is not part of the record. A
  `.alignment.tmp/` directory or an
  `.alignment.json.tmp` file is an alignment left unfinished, and is not part
  of the record. The next `align` or `assess` removes a leftover `.alignment.tmp`,
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
is **reserved**. Its name and meaning are fixed by layout 2, and no code in
this version creates it. The manifest's and the results' key `streams` is
reserved the same way.

### The version rule

`manifest.json`'s `layout` is 2 and `results.json`'s `schema` is 2;
`alignment.json`'s and `assessment.json`'s `schema` are each 1. Adding a file
or a key changes none of them: a new role kind that writes under a reserved
name, or adds a section of its own beside `captures`, a new key in an entry,
a new file beside the others. A reader ignores the files, keys and sections
of kinds it does not know. Renaming, removing or changing the meaning of
anything changes the number of the file it is in, and of the layout when it
is in the layout.

**Keys defined by the output kind.** Four keys of a play are defined by its
output kind, the `output.kind` of the play's source entry in the manifest:
`device`, `output.library`, the `delay_frames` of every mark (`first` in the
manifest; `first` and `last` in the results) and the results record's
`unplayed_frames`. A results record carries no `output`; its kind is that of
the source entry with its `role` and `play`. Each keeps one meaning in every
kind: `device` is the device the output resolved; `output.library` is the
code through which the role reached it, as that code names and versions
itself; `delay_frames` and `unplayed_frames` are frames by the output's own
account. *A source entry* and *`results.json`* say, per kind, what shape
`device` has and whose account the frames are. A new `output.kind`, with its
own `device` shape and its own account of delay, is an addition under the
rule and changes no number. What an output does after an underrun is
behaviour of its kind, stated per kind where `underruns` is defined
(*`results.json`*), and not a meaning of `underruns`, which in every kind says
that the output reported, at that point of the play, that it had run out of
frames.

**A reader that meets an `output.kind` it does not know** reads every other
key of the entry and of its results record as in any kind (the kept copy,
`source` and its `marker`, `granted`, `controls`, `found`, `level_mb`,
`cap_mb`, `frames`, `frames_handed`, `ended`, `stopped_by`, `underruns`,
`as_sent`, `restored`, `failure`: all the role's own, whatever the output)
and does not read the four. It never takes one of them in the sense of a
kind it knows, and it does not take frames handed after an underrun to have
sounded. This harness's two readers: `lab align` reads none of the four, of
any kind, and aligns such a play as any other; `lab assess` reads
`unplayed_frames` only, takes it as `null` for such a play, lists the play
under `not_assessed`, and calls every marker of such a play with an underrun
`unknown`, as it does on a sink (*`assessment.json`*).

**Older runs.** Layout 1 is the run directory before the four keys were
defined by the output kind. `lab align` and `lab assess` refuse a layout-1
run by its number, as they refuse any layout but their own; a layout-1 run is
read by the harness revision that wrote it.

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
- `command`: the command whose reply failed or that the role raised on;
  `null` when an event carried the failure, and for anything else.
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
{"layout": 2,
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
  trimmed). *The marker* below defines each identifier.
- `level_mb` is the level at which a full-scale sample is played: the level
  control's volume plus the held controls', from the values read back. It is
  what the cap bounds. **The play's own peak level** is
  `level_mb + source.peak_mb`, relative to the loudest the device's output can
  be driven; no field carries it.
- `controls` are the controls as the role set and read them back, on every
  channel; `found` is how they stood before the role moved them, which is
  what the role restores.
- `device`, `output.library` and `first.delay_frames` are defined by
  `output.kind` (*The version rule*).
  - `alsa`: `device` is the card as ALSA and USB name it, as in a capture
    entry; `output.library` is the host's ALSA library, which the build does
    not pin, with the version it reported; `delay_frames` is the device's own
    delay reading.
  - `pulse`: `device` is the sound server's sink, `{selector, sink_name,
    description, server_name, server_version, rate_hz, channels,
    server_sample_format, hardware_volume, base_volume_mb}`: the configured
    selector; the sink's name and description as the server gave them
    (`description` `null` when it has none); the server's name and version;
    the sink's own rate and channel count; `server_sample_format`, the
    server's string for the sink's own format (`s32le`, `float32le`, …), kept
    unparsed and **not** in the vocabulary of `granted.sample_format`;
    whether the server reports the sink's volume as a hardware one; and the
    server's base volume for the sink in millibels, read as a sink volume is
    read, 0 when the base is the sink's full volume. `output.library` is the
    pinned sink client, `pulseaudio-rs` with its version. `delay_frames` is
    **the sound server's estimate**: the frames in the stream's buffer plus
    the server's latency to the sink, as the server reports them.
- `first` is a **mark**: the frames handed so far, the output's delay reading
  then, and the role's clock reading directly after. Frame
  `frames_handed − delay_frames` of the kept copy is, by the output's own
  account, at its output at `at`: on a card by the device's, on a sink by the
  sound server's estimate.
- On a sink, `controls` and `found` hold one control, `sink` index 0, a
  channel per sink channel named by its position. `level_mb` is relative to
  the sink's full volume, not to the device's unamplified level: where
  `base_volume_mb` is under 0, the device's own gain at 0 mB is above its
  unamplified level by `−base_volume_mb`, and the same loudness reads lower
  on the sink by that much than on the card opened directly. A cap does not
  carry between the two (`experiments.md`, *On a sound-server sink*).

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
- A command a role raised on, instead of replying, has no reply line.
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
{"schema": 2, "run_id": "…",
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
 "usability": {"assessed": false,
               "reason": "results.json states no usability assessment; the run's assessment, when one was made, is assessment.json",
               "see": "assessment.json"}}
```

- `outcome`: `completed`, `failed` or `interrupted`. `completed` is
  `outcome == "completed"`. A run completed when the script's `run` returned,
  no reply was a failure, no role reported a failure event, no role raised
  while handling a command, and `release` found nothing running. A script
  that catches a role's failure, or an error a role raised, and carries on
  still has a run that did not complete.
- `failure`: `null` for a completed run, otherwise a problem (see
  *Problems*) naming one thing:
  - the runner was told to stop (SIGINT or SIGTERM), while the script ran or
    while the roles were released: `where` `runner`, `reason` `interrupted`,
    whatever else happened;
  - otherwise the first of these, in the order they happened:
    - a failure reply or a failure event: `where` `role`, with the role, its
      reason, detail and data, and `command` the command whose reply failed
      (`null` for an event);
    - a role that raised while handling a command of the script's, instead
      of replying: `where` `role`, `reason` `role_raised`, `detail`
      `"<ExceptionType>: <message>"`, the role, and `command` the command;
      the traceback is in `logs/runner.log`. The runner gives this reason;
      it is not the playback role's `role_error`, which the role reports in
      a `playback.failed` event when a play's thread raised;
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
    handed to the device. `unplayed_frames`: the delay read just before a
    cut, the handed frames that were discarded; 0 for a completed play,
    `null` when it could not be read. It and the marks' `delay_frames` are
    defined by the play's `output.kind` in its source entry (*The version
    rule*); for `alsa` they are the device's own account, for `pulse` the
    sound server's estimate.
  - `underruns`: every underrun, `{at_frame, at, detail}`: the output
    reported, at that point of the play, that it had run out of frames;
    `at_frame` is the count of frames handed when the report reached the
    role. What follows is the output's behaviour:
    - `alsa`: a gap of unknown length, then every frame handed after it,
      late. The kept copy is what sounded, with a gap.
    - `pulse`: a gap, and frames handed over after the server ran out may be
      skipped by the server and never sound. The kept copy of such a play is
      what the role handed to the sink and is **longer than what sounded**.
      The report can trail the server's running out by one write, so the
      skipped frames can begin shortly before `at_frame`. A sink's underrun
      is reported at the role's next write, so one that happens as the play's
      last write is sent is not on the record: such a play can end
      `completed` with no underrun, having lost frames of its last tenth of a
      second. Read in one server's source; measured on none.
  - `as_sent`: true only when the play ended `completed` with no underrun:
    the device took every frame of the copy as given. On a sink a play with
    an underrun may also have frames that never sounded, and `as_sent` is
    subject to the limit just stated: a sink that ran out as the last write
    was sent leaves it true.
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
    `{"event": "<name>", "reason", "detail", "data"}` for a failure event,
    `{"command": "<name>", "reason": "role_raised", "detail", "data": {}}`
    for a command the role raised on. A
    capture that dies unasked and is then stopped by the script has two
    entries: the event's and the stop's reply.
- `usability`: `assessed` is false: this file states no assessment of the
  run's usability. `results.json` is the runner's own account, written once.
  `see` names the file that does, `assessment.json` (*`assessment.json`*),
  when one was made; no such file means the run was not assessed. A reader
  written before `see` existed concludes "not assessed", which is the safe
  error.

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
| `script_error`, `role_raised`, `left_running`, `interrupted` | empty |

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
| `source_outside`, `source_missing`, `source_format`, `alsa_missing`, `mixer`, `role_io`, `role_error`, `already_playing`, `not_playing` | empty |
| `stream_died` | `{delay_frames}`, the last delay read, when the output did not play its buffer out by the end-of-play bound; empty otherwise |

On a sink (`output = "pulse"`) the reasons the sink output names carry:

| Reason | `data` |
|---|---|
| `no_sink` | at the resolve, `{selector, sinks}`: the configured `sink` and each sink present, `{name, description}`; later, `{sink}`, the sink's name, when it vanished |
| `ambiguous_sink` | `{selector, sinks, matched}`: as `no_sink`, and the matching sinks' names |
| `sink_volume` | `{sink}` |
| `stream_moved` | `{sink, volumes, muted}`: where the stream was and its raw volumes and mute, when it was off the sink, not at 0 dB or muted; `{sink}`, the play's sink, when the client refused to read the stream |
| `params`, `open` | `{rate_hz, channels, sample_format}`: what was asked |
| `no_server`, `client`, `client_missing`, `no_control`, `mixer`, `stream_died` | empty, `stream_died` at the end-of-play bound as above |

After `write_stalled` the frame log's completeness was not established;
brenn-pod's `frame-log` tool is the reader that can say more.

## `alignment.json`

Where each marked play lies in each capture, found by searching the captures
for the plays' markers. `lab align --run <dir>` writes it (`experiments.md`,
*Aligning a run*), and so does the assessment, `lab assess` and the one a run
makes at its end, in the same pass and through the same writer, before
`assessment.json`. It is written to `.alignment.json.tmp`, synced and renamed,
and it is the only file `align` leaves: `manifest.json`, `results.json` and the journal are not touched, so
none of them becomes untrue. The report is written into the run, so a run
directory that cannot be written (a read-only filesystem, a directory without
write permission) is refused with `not_writable`, before any capture is read,
and nothing in it changes. So is a run whose leftover `.alignment.tmp/`
cannot be removed; that leftover may then be partly removed, and nothing
else in the run changes. The report is its own record. It makes no
statement of usability; `assessment.json` does, and `results.json`'s
`usability.assessed` stays false.

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
  the play did not run evenly between them).
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
`unterminated` tail; the report describes the run so far. A source entry
with no `marker` key is `no_marker_record`, and results with no `sources`
are read as a run with no play. A layout-1 run is refused (*The version
rule*).

## `assessment.json`

Whether the run is usable, stated once for the whole run. `lab assess --run
<dir>` writes it (`experiments.md`, *Assessing a run*), and so does `lab run`
at the end of every run that was not interrupted. The assessment is
alignment's pass with a measurement attached: each capture is exported once,
every channel is measured while the export stands, the captures are searched
for the plays' markers, and the pass writes `alignment.json` and then
`assessment.json`, each to its `.<name>.tmp`, synced and renamed. Nothing else
in the run changes. No `assessment.json` means the run was not assessed.

The refusals are alignment's (*`alignment.json`*), with one addition:
`unreadable` also covers a manifest or results holding a key the assessment
reads with the wrong type, or lacking it. Every key the assessment reads is
checked before any capture is exported, so on a refusal nothing is written
and an existing `alignment.json` stays as it was. Such a run can still be
aligned by `lab align`, which does not read that key.

```json
{"schema": 1,
 "run_id": "…",
 "harness": {"repository": "brenn-lab", "commit": "…", "dirty": false, "source": "git"},
 "written": "2026-11-14T09:12:00-05:00",
 "replaces": null,
 "reader": {"program": "frame-log", "pinned": true, "path": null},
 "alignment": {"file": "alignment.json", "sha256": "…", "written": "…"},
 "results_read": true,
 "clock_readings": "not_used",
 "answer": "findings",
 "complete": false,
 "findings": [{"finding": "channel_silent", "role": "mic", "capture": 1, "play": null,
               "channel": "raw_mic_2", "at_frame": null, "frames": null,
               "detail": "every sample is 0", "data": {}}],
 "not_assessed": [{"what": "markers", "reason": "no_marker_record", "role": "speaker",
                   "capture": null, "play": 1, "detail": "…"}],
 "run": {"outcome": "completed", "failure": null},
 "captures": [{…}],
 "plays": [{…}],
 "summary": {"captures": 1, "captures_read": 1, "channels": 6, "plays": 2, "marked": 0,
             "located": 0, "findings": 1, "not_assessed": 1}}
```

- `answer`, the key to branch on: `clean` when everything the run holds was
  assessed and there is no finding; `findings` when there is at least one
  finding; `incomplete` when there is none in what was assessed and
  something was not assessed. `clean` never stands beside a `not_assessed`
  entry. A fourth word, `not_assessed`, is never in the file: it is what
  `lab run`'s pointer says when no assessment was made.
- `complete`: `not_assessed` is empty: every part of the run that could hold
  something was examined.
- `harness`, `written`, `replaces`: as in `alignment.json`; `replaces` names
  the `assessment.json` this one replaced.
- `reader`: as in `alignment.json`.
- `alignment`: the `alignment.json` written in the same pass, by its SHA-256
  and its `written`. Markers, offsets and drift are read there; nothing of
  alignment's is copied here except as each play's `found_in` says.
- `results_read`: whether the run had a `results.json`.
- `clock_readings`: `not_used`, as in alignment.
- `run`: `outcome` and `failure` from `results.json`, both `null` without one.
- `summary`: counts of what follows; `marked` and `located` are alignment's.

### Findings and what was not assessed

A **finding** states a fact the assessment established about the run: the
record is not what it claims, nothing was recorded, or a part of the run that
is here was given to the reader and could not be read. A **`not_assessed`
entry** states that something the run holds was not examined; it says nothing
is wrong and nothing is right. A condition is both when the fact established
is the reason something was not examined (an unreadable capture, an unusable
kept copy, a missing `results.json`), and neither when there was nothing to
examine (a play with no marker, a marker the play's record does not show to
have sounded). No finding rests on a tunable number: no level, no drift and
no count of late periods is judged. An experiment that needs a bar reads the
measurements and sets its own.

A finding has exactly the keys `finding`, `role`, `capture`, `play`,
`channel`, `at_frame`, `frames`, `detail`, `data`, in that order, `null`
where one does not apply. `at_frame` is a frame of the capture's index for a
capture's finding and a frame of the kept copy for a play's; `frames` is a
length, `null` when it is not known, never 0 for unknown.

| Finding | Fires when | `data` |
|---|---|---|
| `run_not_completed` | `results.json`'s `outcome` is not `completed`, or there is no `results.json` | `{outcome, where, reason}`, from the results' `failure`; `{outcome: null, reason: "no_results"}` |
| `role_recorded_nothing` | the manifest has a capture role with no capture entry | `{}` |
| `capture_failed` | the capture's record says `ended` `failed` | the record's `failure` |
| `capture_unreadable` | the capture is in the run and the reader was run on it, and no usable export came back: `missing`, `export_refused`, `capture_differs` or `reader_contract` | `{reason}` |
| `capture_empty` | the capture was read and holds no frame of audio | `{}` |
| `log_not_ended` | the reader's verdict on the log's tail is not `ended` | `{verdict}`; `frames` is `null` |
| `loss` | one per loss either witness states (below), at its `at_frame` | the capture's `loss` entry |
| `loss_witnesses_disagree` | one per difference between the witnesses (below) | `{difference, recorder, reader}` |
| `channel_silent` | every sample of the channel is 0 | `{}` |
| `channel_constant` | every sample is one value other than 0 | `{value}` |
| `channel_at_limit` | a sample is at a limit of the format; `at_frame` and `frames` are the first span's | `{limit_samples, limit_spans, longest_limit_span}` |
| `play_failed` | the play's record says `ended` `failed` | the record's `failure` |
| `play_cut_by_runner` | the play ended `stopped` with `stopped_by` `runner` | `{frames_handed, unplayed_frames}` |
| `play_underrun` | one per underrun, at its frame of the kept copy | `{}` |
| `controls_not_restored` | the play's `restored` is false | `{}` |
| `kept_copy_unusable` | alignment's state of the play is `failed` with `copy_missing` or `copy_differs` | `{reason}` |
| `play_not_located` | the play is marked, a pair with it was tried, its start marker is `expected`, and `found_in` is empty | `{start, end, pairs_tried, outcomes}`: the two markers' words, the pairs tried, and the count of each marker outcome alignment reported for the play |

Not findings: a play the script stopped, which is the script doing what it
meant to; a marker of any play that is `not_handed` or `unknown` and was not
found; a marker not found on one channel while the play is found on another
(a processed channel may remove the played sound); any level, drift or count
of `late`; and a play whose drift alignment could not measure, since each
reason for that is already a `loss` or a `play_underrun` finding.

A `not_assessed` entry has exactly `what`, `reason`, `role`, `capture`,
`play`, `detail`:

| `what` | `reason` | When |
|---|---|---|
| `role` | `unknown_kind` | the manifest names a role of a kind this harness does not assess; nothing of it is read |
| `capture` | `not_in_run` | the capture's pointer is not in the run |
| `capture` | `reader_missing`, `reader_usage` | the reader could not be started, or refused its command line: the log was never read, and there is no finding |
| `capture` | `missing`, `export_refused`, `capture_differs`, `reader_contract` | the capture could not be read; each is also `capture_unreadable`. `reader_contract` may be the reader's fault or the log's, and the assessment cannot tell which |
| `recorder_account` | `no_record` | `results.json` holds no record of a capture the manifest names |
| `results` | `no_results` | the run left no `results.json`: no recorder's account and no play side |
| `play_account` | `unknown_output_kind` | the play's `output.kind` is not one this harness knows: its `unplayed_frames` is not read and is `null` in the play's `record` and in `play_cut_by_runner`'s data; the detail names the role, the play and the kind. Listed for every such play, a completed one too |
| `markers` | `no_marker_record` | the play's entry has no `marker` key |
| `markers` | `unknown_definition` | the play's marker is one this harness cannot search for |
| `markers` | `copy_unusable` | the play is marked and its kept copy is missing or differs; also `kept_copy_unusable` |
| `markers` | `no_capture_searched` | the play is marked and no pair with it was tried: no capture, none readable, or every rate too low |

### Captures

One entry per capture entry of every capture role, in the manifest's order:

```json
{"role": "mic", "capture": 1, "file": {…pointer…},
 "rate_hz": 16000, "sample_format": "s16_le", "layout": ["…"],
 "read": true, "failure": null,
 "frames": 417600, "tail": "ended",
 "recorder": {"ended": "stopped", "frames": 417600, "gaps_reported": 0, "late": 0,
              "loss_free": true, "failure": null},
 "loss": [],
 "channels": [{"channel": "raw_mic_0", "frames": 417600, "min": -2457, "max": 2403,
               "mean": -0.5, "peak_mb": -2250, "rms_mb": -4540,
               "silent": false, "constant": false,
               "limit_samples": 0, "limit_spans": 0, "longest_limit_span": 0,
               "first_limit_spans": []}]}
```

- `read`, `failure`: alignment's reading of the capture; `failure` is
  `{reason, detail}` when it failed, and `frames`, `tail` and `channels` are
  then `null`.
- `frames`: the frames of audio the log holds, the sum of the reader's runs.
  `tail`: the reader's verdict on the log's end.
- `recorder`: the capture's record in `results.json`, `late` as a count;
  `null` when there is none.
- `loss`: every loss either witness states, by `at_frame`:
  `{at_frame, frames, basis, estimated_frames, cause, witnesses}`. `frames`
  is the length where a witness measured it (the recorder's gap length, else
  the reader's `measured` or `not_sent` fill), `null` otherwise.
  `estimated_frames` is the reader's fill for a `clock_estimate` hole, an
  estimate from clock stamps, and `null` for any other basis. `basis` is the
  reader's and `cause` the recorder's, each `null` when its witness states no
  such loss. `witnesses` is `["reader"]`, `["recorder"]` or both.
- `channels`: one entry per name of the layout, in its order.

**Measuring.** Each channel is measured over the frames of the capture's
runs and nothing else: the silence the reader writes in place of a hole is
never measured.

| Key | Definition |
|---|---|
| `frames` | samples measured |
| `min`, `max` | the smallest and largest sample, in the sample format's integers |
| `mean` | the mean sample, in the same units; the level without the offset is `rms² − mean²` |
| `peak_mb` | `ceil(2000·log₁₀(max(|min|, |max|) / 2^(bits−1)))`, millibels relative to full scale, the definition of a source's `peak_mb`; `null` when every sample is 0 |
| `rms_mb` | `round(2000·log₁₀(rms / 2^(bits−1)))` of the samples as they stand, offset included; `null` when every sample is 0. A full-scale square wave is 0, a full-scale sine −301 |
| `silent` | every sample is 0 |
| `constant` | every sample is one value other than 0 |
| `limit_samples` | samples equal to `2^(bits−1) − 1` or `−2^(bits−1)` |
| `limit_spans`, `longest_limit_span` | maximal runs of consecutive samples at a limit, within one run of the log: how many, and the longest in frames |
| `first_limit_spans` | the first 16 such spans, each `{at_frame, frames}` in the capture's frames |

A channel of a capture that was read and holds no audio has `frames` 0, the
three limit counts 0, `first_limit_spans` `[]`, and every other key `null`:
nothing was there to be silent. Its capture is `capture_empty`, and no
channel finding fires.

`silent` and `constant` are about every sample, not about level: a beam
with nobody speaking is quiet, not zero, and quiet is not a finding. No
device the harness drives has a channel that is zero by design, such as a
playback reference with nothing played; on such a channel `channel_silent`
would fire, and no device can yet say that a channel may be silent.

**The two witnesses of loss.** The recorder's witness is the capture's record
in `results.json`: its `gaps`, `gaps_reported`, `frames` and `ended`. The
reader's is its map: the holes, the runs and the tail. For a capture the
recorder reports stopped, each gap and its hole carry the same `at_frame`, a
gap with a length is a `measured` hole of that length and one without is a
`clock_estimate` or `unknown` hole, and the record's `frames` is the runs'
frames plus the `measured` holes' fill. With both witnesses present, each
difference is one `loss_witnesses_disagree`:

| `difference` | Fires when |
|---|---|
| `hole_without_gap` | the reader has a hole at an `at_frame` where the recorder reported no gap |
| `gap_without_hole` | the recorder reported a gap at an `at_frame` where the reader has no hole |
| `length` | both state a length for one loss and they differ, or one states a length and the other does not |
| `gaps_reported` | the recorder's count at the stop is not the number of gaps the record holds |
| `frames` | the record's `frames` is not the runs' frames plus the `measured` holes' fill |
| `end` | the record says `stopped` and the tail is not `ended` |

Gaps and holes are matched by `at_frame`, whatever their order. On a capture
that failed, a gap and its hole can lawfully fail to pair; the difference is
then reported beside `capture_failed`. With one witness absent nothing is
compared, and `not_assessed` says which is absent.

### Plays

One entry per source entry of every playback role:

```json
{"role": "speaker", "play": 1, "file": {…pointer…},
 "record": {"ended": "completed", "stopped_by": null, "frames": 312000, "frames_handed": 312000,
            "unplayed_frames": 0, "underruns": 0, "as_sent": true, "restored": true, "failure": null},
 "marker": {"state": "marked", "start": "expected", "end": "expected", "located": true,
            "found_in": [{"capture": {"role": "mic", "capture": 1}, "channel": "raw_mic_0",
                          "offset_frames": 29138.4, "ratio_basis": "measured",
                          "drift_ppm": 2.1, "drift_unmeasured": null}]}}
```

- `record`: the play's record in `results.json`, `underruns` as a count;
  `null` when there is none. Its `unplayed_frames` means what the results say
  it means for the play's output kind, and is `null` for a kind this harness
  does not know.
- `marker.state`: alignment's state of the play. `start`, `end`: whether
  each marker is expected in a capture, below; `null` unless the state is
  `marked`. `located`: alignment's rule, both markers found on one channel
  of some capture; `null` unless `marked`. `found_in`: every capture channel
  on which the start marker was found and, when the end marker is
  `expected`, the end marker too, each value copied from that channel in
  `alignment.json`. For a play cut before its end marker sounded,
  `drift_ppm` is `null` and `ratio_basis` is `nominal`.

**Which markers a play is expected to show.** A play the script cut is
healthy, and a marker the cut kept from sounding is in no capture. Each
marker of a marked play is given one word from the play's record:

| Word | When |
|---|---|
| `not_handed` | the frames handed do not reach the marker's last frame; alignment did not search for it |
| `expected` | the marker was handed whole and the record shows it left the device: the play ended `completed` (with no underrun, on an output that may skip after one), or `frames_handed − unplayed_frames` reaches the marker's last frame; also when the run has no record of the play. On a sink the record cannot show an underrun as the play's last write was sent (*`results.json`*, `underruns`), so an end marker `expected` there rests on that limit |
| `unknown` | the marker was handed whole and the play was cut or failed with the marker in the frames discarded, or with `unplayed_frames` `null`; or the play's record has an underrun and its output kind is one that may skip frames after one (`pulse`, or a kind this harness does not know), however the play ended, every marker of the play. Alignment searches for it, and the run cannot say whether it sounded |

A marker found counts wherever it was found. A marker `not_handed` or
`unknown` and not found is neither a finding nor a `not_assessed` entry.

### A run still in progress, older runs and broken runs

The assessment locks nothing; on a run still recording it describes the run
so far, and a capture being written is `log_not_ended`. A run with no
`results.json`, including one whose runner died while writing it, is
assessed in part: `run_not_completed` and `results`/`no_results`, every
capture read and measured, loss stated from the reader alone. A layout-1
run is refused by its number (*The version rule*). A `manifest.json` or
`results.json` that is present and damaged is refused, as above; a source
entry with no `output.kind` is such damage, refused before any capture is
read, though `lab align`, which does not read the key, still aligns the run. One `results.json` the runner writes itself is refused the same way.
When a capture's recorder reader failed, or a play's end could not be
reported, and a command of the script's raised that error, the run
ends `failed` and the record keeps `ended` `null`, a play's `restored` too;
the run is not assessed
(`errhandling-assessment-refuses-runner-written-null-ended` in the tracker).

If `assess` is killed between its two writes, `alignment.json` is new and
`assessment.json` is the previous one; the new `alignment.json`'s `replaces`
then holds the hash that `assessment.json`'s `alignment.sha256` names. A
later `lab align` replaces `alignment.json` the same way, and the assessment
still says which report it was made with. Two `align` or `assess` commands
on one run at once share the scratch directory and are not supported.

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

A play on a sound-server sink is no clock tie at all. The server resamples
and remixes the role's frames on their way to the device, so no frame of the
kept copy is a sample on the device's clock, a `pulse` role may state no
`sample_clock`, and its marks' `delay_frames` are the server's estimate. Its
marks place the play on the machine's clock to within that estimate and no
better; correlation of a marked source is the only measure of where it lies
in a capture.

Not measured by this harness: the offset to the frame by any means but
correlation; the latency from the capture's trigger to its frame 0; whether
the device's delay reading is accurate. The playback role's audible-lane test
asserts that the clock tie lies within 150 ms of the offset correlation
finds, which holds the delay reading, the capture's start latency and the
clock readings together and separates none of them. An exact tie needs the
device's own copy of what it played inside the capture, and the reSpeaker
Flex's six-channel image carries none (`own-device-playback-sample-relationship`).
