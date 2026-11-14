- `examples/anchoring_scenario.py` states its plays' starts instead of
  waiting between them: it reads each playback role's clock, then starts
  the talker two seconds after its reading and the speaker three seconds
  after its own. Each start is on its own role's clock, so the script runs
  unchanged when the two roles are on different machines.
