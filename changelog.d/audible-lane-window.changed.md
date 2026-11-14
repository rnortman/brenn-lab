- A new audible bench, `//brenn_lab:bench_audible_test`, plays on real
  outputs (the Flex's own speaker and a sound-server sink such as a
  Bluetooth speaker). For each output it first finds the quietest level the
  Flex's microphones reliably detect: it starts at the output's lowest level
  and steps up, never past a fixed ceiling, until a marked play is detected
  in three runs in a row. Everything else then plays one step above that
  level: a marked play on each output, aligned and assessed; a play over the
  cap, which must be refused with nothing heard; the anchoring scenario
  three times across both outputs; and a recording of the Flex's
  microphones while a dry run opens its output. It replaces the old audible
  test that played unmarked clips on the Flex.
- `make audible-test` now requires the agreed window as `WINDOW=HH:MM-HH:MM`
  in local time and refuses any other form. The bench fails outside the
  window, and every playback role it configures is quiet after it ends. New
  options: `SINK=<sink>` names the sink the bench plays on (by part of its
  name or description), and `ONLY=<pytest -k expression>` runs only matching
  bench tests. The lane's tests now receive `XDG_RUNTIME_DIR`, so they can
  reach the desktop's sound server.
