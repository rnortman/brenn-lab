- A play on a sound-server sink no longer fails with `stream_moved` when the
  play before it had a different number of channels. A session manager that
  restores a stream's volumes by the client's name (WirePlumber) gave a new
  stream the last one's volumes channel by channel, so a two-channel play
  after a one-channel one was muted on its second channel. The sink client
  now asks the session manager not to restore its streams' volumes or
  target, and not to remember them, and sets each stream to 0 dB once it is
  made: on PipeWire a stream nothing had set read silent on every channel
  with its sink at the sink's lowest volume.
