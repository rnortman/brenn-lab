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
pod_capture = "/opt/brenn-pod/bin/pod-capture"

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
  where programs that roles call are installed **on that machine**, each as an
  absolute path. Only program names some kind uses are read there; the
  capture kind uses `pod_capture`, the path of brenn-pod's recorder. Whether
  a program exists at its path is not a configuration problem: the role's
  `check` reports it (`recorder_missing`).
- `roles.<r>`: a role, the name a script uses for one device. A role name
  matches `[a-z][a-z0-9_]*` and is at most 32 bytes: it names a directory in
  the run and is the recorded stream's source name.
  - `kind`: the role's kind. This version has one, `capture`.
  - `machine`: a machine in `machines`. A role whose kind needs a program its
    machine does not name is `missing_program`.
  - `device`: the kind's parameters. For `capture`: `card`, a non-empty
    string that selects the card; `rate_hz`, a positive integer; `sample_format`,
    a string; `layout`, a non-empty list of distinct non-empty channel names.
  - `stated`: optional and free-form. It is copied into the manifest as the
    configuration's statement, never as a reading. A date, a time or a
    non-finite number in it is `wrong_type`: it has no JSON form.
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
    what that was. Only the runner sends it.

The registered names:

| | Names |
|---|---|
| Commands | `check`, `release`, `capture.start`, `capture.stop` |
| Results | `capture.started`, `capture.stopped`, `capture.checked`, `capture.released` |
| Events | `capture.gap`, `capture.late`, `capture.failed` |

## The capture kind

A capture role records every channel of one device to a frame log with
brenn-pod's recorder, `pod-capture`, found at the machine's
`programs.pod_capture`.

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
| `recorder_missing` | no executable at the configured path |
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
6. Send `release` to every role that passed step 3. A role still capturing is
   a problem, `left_running`: the script ends without stopping it.

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
