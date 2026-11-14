- `examples/anchoring_scenario.py` no longer carries its levels. Before the
  capture starts it sends `check` to `talker` and to `speaker` and plays each
  source at that role's cap, so the level of each play is set by the lab
  configuration's `cap_mb`. A configuration written for the earlier example,
  whose caps were ceilings over its −3000 mB plays, now plays at those caps:
  set each cap to the level the output should play at.
