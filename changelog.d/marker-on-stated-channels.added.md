- A marker on some channels of a source now counts: a source carries the
  marker when at least one channel carries it and every other channel is
  exactly zero across the marker's frames. `lab mark --channels <index> …`
  writes the marker on those zero-based channels only and zero on the rest,
  so a multi-channel file that sounds one speaker can be marked from that
  speaker alone. Without `--channels` every channel carries it, as before.
  A play's source record, the manifest's source entry and `lab mark`'s
  document gain `marker_channels`, the channels that carry the marker.
