- A capture's entry in `manifest.json`, and the `capture.started` result a
  script receives, now carry the recorder's own statement about the device:
  `device.usb` (where the device is on USB, or `null`) and `device_state`,
  which says whether the device's own state was read and, when it was, what
  was read (for a reSpeaker Flex XVF3800, its firmware identity and routing
  registers). Both are carried as brenn-pod's recorder states them; their
  meaning is in brenn-pod's `docs/reference/frame-log.md`. A dry run's
  `capture.checked` result carries them too, so a board whose chip cannot be
  read shows it before anything is recorded. The two fields that said the
  device's firmware and configuration were never read, `device_firmware` and
  `device_configuration`, are removed.
- The run directory is now layout 4. `lab align` and `lab assess` refuse a
  layout-3 run by its number, as they refuse layouts 1 and 2; such a run is
  read by the harness revision that wrote it.
- A recorder whose `capture_started` line states nothing about the device's
  state is outside the recorder's contract: the start fails with
  `recorder_contract`.
