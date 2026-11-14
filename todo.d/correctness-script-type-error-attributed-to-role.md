A command whose body has a field of the wrong type is not refused where the
script sends it. Neither `RoleHandle.send` nor `Command.__post_init__` checks
the body's fields, and the request dataclasses do not check them either, so
`playback.Play(source="clip.wav", volume_mb="-3000")` is journaled and handed
to the playback role, which raises a `TypeError` while handling it. The runner
records that as the role's raise: the run's `failure` is `where` `role`,
`reason` `role_raised`, naming the device role, and `logs/runner.log` says the
role raised, for what is a bug in the script. The same holds for any field a
role uses without checking it first, such as `source=None`.

The work is to reject a badly typed command field in the script's frame,
before anything is journaled or sent, so that it is the script's error
(`script_error` when it leaves the script's `run`), and to leave `role_raised`
for the roles' own errors. Where the check lives is the decision to make: one
check of every request's fields against its annotations where a command is
made, or a check in each request type, which puts it in the roles' message
modules.

Done when a script that sends a command with a badly typed field gets a
`TypeError` from its own `send` with nothing journaled, the run's `failure`
names the script when the error leaves `run`, and default-check tests hold
this for a capture command and a playback command.
