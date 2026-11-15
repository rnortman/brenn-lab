- A capture or play whose end never reached the run, because the role's
  clock or the runner's recording of an event raised, is now recorded with
  `ended` `failed` and the failure `end_not_reported`, not `ended` null.
  The assessment now measures the run's captures and reports that failure
  instead of refusing the whole run.
- A script command with a field of the wrong type, such as
  `playback.Play(source="clip.wav", volume_mb="-3000")`, now raises
  `TypeError` from the script's own `send`, naming the command and the
  field, before anything is journaled. The run fails as the script's error
  (`script_error`), not as the role's (`role_raised`).
