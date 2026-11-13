- The build now pins and builds brenn-pod's recorder, `pod-capture`, and its
  reader, `frame-log`, at one stated commit. A lab configuration chooses the
  recorder per machine, explicitly: `programs.pod_capture = { pinned = true }`
  for the pinned build on the runner's own machine, or a path to an installed
  recorder. `make hardware-test` now records from an attached reSpeaker Flex
  through the capture role and checks the result with the pinned reader; the
  lanes need the host's ALSA runtime library.
