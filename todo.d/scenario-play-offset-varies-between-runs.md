`examples/anchoring_scenario.py` sends the talker's play, waits `OFFSET_S`
(one second), then sends the speaker's. In the runs the audible bench kept on
2026-11-14 with the Flex's speaker at −6000 and −5400 mB under a talker on a
Bluetooth sink, the Flex's play started in the capture either 0.98 to 1.06 s
after the talker's (all three runs whose Flex start marker scored over the
threshold) or 0.80 to 0.88 s after it (all thirteen that scored under), with
no run between. A start marker landing later falls in a stretch of the speech
about 12 dB quieter in band, and that is the high score. Ruled out as the
cause of the high scores: the talker's delay estimate, the talker's level and
the Flex's level. What makes the gap fall in one cluster or the other was not
read: neither when each role began its play, nor each output's path from its
play to the capture, was compared between the two clusters.

The bench's ramp counts a level found only on three detections in a row
(`CONFIRM_RUNS` in `brenn_lab/testing/bench.py`). That keeps a ramp from
stopping at a level whose one run fell in the later cluster; it does not
remove the clusters, and a run of later-cluster runs can still stop a ramp
low. Above −4000 mB, a later-cluster detection followed by an earlier-cluster
miss at one level ends the ramp at the checkpoint as not recognised, though
that level detected the play.

`alignment.json` gives each located play's `offset_frames`, so where both
plays are located the run directory already shows the gap that sounded. Each
play's first mark ties a frame to the machine's monotonic clock, so the gap
between the two plays' starts on that clock, and each play's offset less its
tie, can be read from runs already kept, without playing anything.

Done when `docs/reference/run-directory.md` states, with figures from runs
in both clusters, whether the gap moves between the two plays' first marks
on the machine's clock or between a play's mark and where it lies in the
capture, and for which output; and the reference says whether a script's
wait between two plays on two outputs sets the gap that sounds, and to what
precision.
