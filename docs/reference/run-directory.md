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
  sources/<role>/…     reserved
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

`sources/` (exact copies of what was sent to each output, per playback role)
and `streams/` (other recorded streams such as pose, status and faults, per
role) are **reserved**. Their names and meanings are fixed by layout 1, and no
code in this version creates them. The manifest's and the results' keys
`sources` and `streams` are reserved the same way.

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
`dirty` false does not show it. `dirty` may be `null` when a
build declared its commit rather than reading it from a checkout.

## `manifest.json`

Written when the run directory is created, and rewritten whole (to a
temporary file, then a rename) each time a capture starts.

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
   {"repository": "brenn-pod", "commit": "…", "dirty": false, "source": "git",
    "from": "the recorder's report at the start of capture mic/0001"}],
 "roles": {"mic": {
   "kind": "capture", "machine": "bench",
   "configured": {…roles.mic.device…},
   "stated_by_configuration": {…roles.mic.stated…},
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
  - `captures`: one entry per capture whose start the recorder reported, in
    order; empty until one starts.

### A capture entry

```json
{"capture": 1,
 "capture_id": "<32 hex>",
 "recorder": {"program": "pod-capture", "repository": "brenn-pod",
              "build": {"commit": "…", "dirty": false, "source": "git"}},
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
    - `release` finding a capture still running: `where` `runner`, `reason`
      `left_running`, with the role.

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
| `recorder_missing` | `{program}`: the configured path |
| `recorder_usage` | `{channel}` when the harness refused a channel name with a comma; `{stderr, exit_status}` when the recorder refused its command line |
| `recorder_exited` | `{exit_status}` |
| `recorder_contract` | `{line}` for a line outside the recorder's contract; `{exit_status}` for an exit status that disagrees with the last line; `{reason}` for a failure reason the recorder's contract does not list |
| `recorder_timeout`, `stopped_unasked`, `already_capturing`, `not_capturing` | empty |
| `unknown_command` | `{command}` |
| `role_io` | `{path, errno}`; `path` may be `null` |
| `unreachable` | `{machine}`: the role's machine |
| `not_configured`, `kind_mismatch` | empty |
| `script_error`, `left_running`, `interrupted` | empty |

After `write_stalled` the frame log's completeness was not established;
brenn-pod's `frame-log` tool is the reader that can say more.

## A recorder left running

The recorder runs in a session of its own, so a Ctrl-C at the runner's
terminal reaches the runner alone, and the runner stops every capture at the
end of a run. If the runner itself is killed outright (SIGKILL, a power
loss), the recorder keeps recording and keeps the device, and the next start
on that device fails with `open`. Find its pid in `logs/runner.log` and send
it SIGTERM. It then stops cleanly and finishes its frame log.
