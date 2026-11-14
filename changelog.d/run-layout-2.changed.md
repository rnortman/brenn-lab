- A run directory is layout 2: `manifest.json`'s `layout` and
  `results.json`'s `schema` are 2, as is the dry-run report's `schema`. A
  play's `device`, `output.library`, marks' `delay_frames` and
  `unplayed_frames` are defined by its source entry's `output.kind`, and a
  later kind of output is an addition that changes no number. `lab align`
  and `lab assess` refuse a layout-1 run by its number.
- `lab assess` reads `output.kind` from every source entry and refuses a
  run whose source entry lacks it as `unreadable`. A play whose output kind
  it does not know has its `unplayed_frames` taken as `null` and is listed
  under `not_assessed` as `play_account`/`unknown_output_kind`.
