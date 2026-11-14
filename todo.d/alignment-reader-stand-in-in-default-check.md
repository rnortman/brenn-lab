The default check runs alignment against a stand-in for brenn-pod's `frame-log`, which hands over a prepared WAV and a prepared map; the real reader is run on a real capture in the hardware lane only. brenn-lab may not write a frame log, and brenn-pod has no program that writes a synthetic capture log (its `frame-log-writes-no-synthetic-capture-log`).

The work: once brenn-pod has that program and the pin has moved to it, the default check aligns a synthetic run whose capture is a real frame log written by brenn-pod's own code, through the pinned reader, and the stand-in is removed or kept only for the refusals a real log cannot be made to show.

Done = a default-check test reads a frame log through the pinned `frame-log`.
