- An error raised while a play ends (closing its stream, putting its controls
  back, letting its output go) no longer leaves the play without an end, the
  output held at the play's level and the playback role unusable: every step
  of the end is still made, the play ends with a `playback.failed` event, and
  the role answers its next command.
- An error raised while a play first sets its controls no longer leaves them
  where it had set them: the play is ended, and its controls put back.
