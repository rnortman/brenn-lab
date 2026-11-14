- `examples/level_step.py` plays one step of a level search. While `mic`
  records, `speaker` plays `primer.wav`, a few seconds of any sound, and
  then `marked.wav`, a file made by `lab mark`, both at the role's cap. The
  primer gives a speaker that fades in after a silence time to do so before
  the marked sound starts. To try a level, set the role's `cap_mb` to it,
  run the script, and read the run's `alignment.json`.
- `brenn_lab.playback` now exports `Checked`, the playback role's reply to
  `check`, so a script can read its role's cap and controls.
