- Captures can now be lined up with what was played. `lab align --run <dir>`
  looks for each marked play (see below) on every channel of every capture
  in a run and writes `alignment.json` into the run directory. For each
  play, the file says where its marker was found in the capture's frames
  and how far the capture is offset from the played source. Where a play
  has a marker at each end, it also gives the drift between the two
  clocks. When a marker cannot be found, the file says why. Alignment reads
  no timestamps, so captures from any machine or device are handled the
  same way. It changes nothing else in the run. A
  run directory that cannot be written to (for example an archived,
  read-only copy) is refused with `not_writable`, and nothing in it
  changes. Captures are read through brenn-pod's pinned `frame-log` reader,
  and `--frame-log <path>` points at another copy of it.
- `lab mark --source <in.wav> --out <out.wav> --member <n>` writes a marked
  copy of a sound. The marker is half a second of noise-like signal on a
  2 kHz tone, placed before the sound and again after it. The marker scheme
  is named `gold-bpsk-1` and has 511 distinct members, so sounds played at
  the same time can each carry their own. The copy keeps the source's
  sample rate, channel count and sample format.
- When a marked sound is played, the playback role detects its marker and
  records it in the play's reply, the manifest and the results.
  `results.json` counts the marked plays in `played.plays_marked`. Unmarked
  sounds play exactly as before and record `marker: null`.
- Alignment uses numpy, newly pinned. numpy loads the host's C++ runtime
  (`libstdc++.so.6`) and zlib (`libz.so.1`), which are now prerequisites of
  `make check` and `lab align`. Runs, dry runs and `lab mark` do not load
  them.
