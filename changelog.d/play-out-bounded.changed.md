- A play's wait for its output to play the buffer out is bounded, on every
  kind of output: once the source is handed over, a delay still above 0 two
  seconds after the first reading said the buffer would be empty ends the
  play with `stream_died`, and the role restores its controls as after any
  failure. Before, an output whose delay stopped falling held the play, and
  its controls at the play's level, until a stop.
